"""Bounded private-reference request codec and lossless text/timing envelope."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import io
import math
import re
import struct
import wave

MAX_BODY_BYTES = 1_000_000
MAX_AUDIO_SECONDS = 30
MAX_RESPONSE_BYTES = 8_000_000
FIELDS = frozenset({"model", "response_format", "language"})
MODELS = frozenset({"whisper", "whisper-large-v3", "openai/whisper-large-v3"})


class InvalidRequest(ValueError):
    """Fixed public category; never include request values in errors."""


def validate_riff(audio: bytes) -> None:
    """Reject ambiguous/truncated containers before the standard WAV decoder."""
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE" or \
            struct.unpack_from("<I", audio, 4)[0] != len(audio) - 8:
        raise InvalidRequest("Invalid request")
    offset, found = 12, set()
    while offset < len(audio):
        if offset + 8 > len(audio):
            raise InvalidRequest("Invalid request")
        name, size = struct.unpack_from("<4sI", audio, offset)
        offset += 8
        if offset + size + (size % 2) > len(audio):
            raise InvalidRequest("Invalid request")
        if name in (b"fmt ", b"data"):
            if name in found or (name == b"data" and b"fmt " not in found):
                raise InvalidRequest("Invalid request")
            if name == b"data" and (size == 0 or size % 2):
                raise InvalidRequest("Invalid request")
            found.add(name)
        offset += size + (size % 2)
    if found != {b"fmt ", b"data"}:
        raise InvalidRequest("Invalid request")


def parse_request(body: bytes, content_type: str) -> tuple[str, str | None, bytes, float]:
    if not 0 < len(body) <= MAX_BODY_BYTES or len(content_type) > 512 or "\r" in content_type or "\n" in content_type:
        raise InvalidRequest("Invalid request")
    try:
        prefix = ("MIME-Version: 1.0\r\nContent-Type: " + content_type + "\r\n\r\n").encode("ascii")
        message = BytesParser(policy=policy.default).parsebytes(prefix + body)
        if message.get_content_type() != "multipart/form-data" or not message.is_multipart() or message.defects:
            raise InvalidRequest("Invalid request")
        parts = list(message.iter_parts())
        if not 1 <= len(parts) <= 4:
            raise InvalidRequest("Invalid request")
        fields, audio = {}, None
        for part in parts:
            if part.is_multipart() or part.defects or part.get_content_disposition() != "form-data":
                raise InvalidRequest("Invalid request")
            if len(part.get_all("Content-Disposition", [])) != 1 or part.get("Content-Transfer-Encoding") is not None:
                raise InvalidRequest("Invalid request")
            name = part.get_param("name", header="Content-Disposition")
            value = part.get_payload(decode=True)
            if not isinstance(value, bytes):
                raise InvalidRequest("Invalid request")
            if name == "file":
                if audio is not None:
                    raise InvalidRequest("Invalid request")
                audio = value
            elif name in FIELDS and name not in fields and part.get_filename() is None and len(value) <= 128:
                fields[name] = value.decode("utf-8", errors="strict")
            else:
                raise InvalidRequest("Invalid request")
        if fields.get("model") not in MODELS or audio is None:
            raise InvalidRequest("Invalid request")
        response_format = fields.get("response_format", "json")
        if response_format not in {"json", "verbose_json", "text"}:
            raise InvalidRequest("Invalid request")
        language = fields.get("language", "auto")
        if language != "auto" and not re.fullmatch("[a-z]{2,3}", language):
            raise InvalidRequest("Invalid request")
        validate_riff(audio)
        with wave.open(io.BytesIO(audio), "rb") as stream:
            if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (1, 2, 16000, "NONE"):
                raise InvalidRequest("Invalid request")
            frames = stream.getnframes()
            if not 0 < frames <= MAX_AUDIO_SECONDS * 16000:
                raise InvalidRequest("Invalid request")
            pcm = stream.readframes(frames)
            if len(pcm) != frames * 2:
                raise InvalidRequest("Invalid request")
        return response_format, None if language == "auto" else language, pcm, frames / 16000
    except InvalidRequest:
        raise
    except (UnicodeError, ValueError, TypeError, wave.Error, EOFError, AttributeError):
        raise InvalidRequest("Invalid request") from None


def normalize_result(result: dict, duration: float) -> dict:
    """Keep canonical text exactly; never stretch or invent native timestamps.

    Unmappable segments leave the complete text in an explicitly untimed anchor.
    The original reference segment list remains available for private auditing.
    """
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise ValueError("Invalid reference result")
    text = result["text"]
    original = result.get("segments")
    language = result.get("language")
    if len(text.encode("utf-8")) > 1_000_000 or not isinstance(original, list) or len(original) > 4096:
        raise ValueError("Invalid reference result")
    if not isinstance(language, str) or not re.fullmatch("[a-z]{2,3}", language):
        raise ValueError("Invalid reference result")
    observations = result.get("decoding_observations")
    if not isinstance(observations, list) or len(observations) > 64:
        raise ValueError("Missing or invalid decoding observations")
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {
                "temperature", "token_count", "effective_sample_len", "budget_reached"}:
            raise ValueError("Invalid decoding observation")
        temperature = observation["temperature"]
        count, limit = observation["token_count"], observation["effective_sample_len"]
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0 or \
                type(count) is not int or not 0 <= count <= 448 or \
                type(limit) is not int or not 1 <= limit <= 448 or \
                type(observation["budget_reached"]) is not bool or \
                observation["budget_reached"] != (count >= limit):
            raise ValueError("Invalid decoding observation")
    status = "budget_reached" if any(row["budget_reached"] for row in observations) else "not_reached_no_coverage_claim"
    if result.get("decoding_budget_status") != status:
        raise ValueError("Invalid decoding budget status")
    rows, cursor, previous_time, mapping_failed = [], 0, 0.0, False
    for item in original:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("Invalid reference result")
        value = item["text"]
        if not value:
            continue
        position = text.find(value, cursor)
        if position < 0 or text[cursor:position].strip():
            mapping_failed = True
            break
        start, end = item.get("start"), item.get("end")
        numeric = all(type(v) in (int, float) and math.isfinite(v) for v in (start, end))
        timed = numeric and 0 <= start <= end <= duration and start >= previous_time
        row = {"text": value, "start_offset": position, "end_offset": position + len(value),
               "start": start if timed else None, "end": end if timed else None,
               "kind": "segment", "timing_source": "openai-whisper-reference"}
        if timed:
            row["timing_status"] = "native_unqualified"
            previous_time = start
        else:
            row["timing_status"] = "unknown_out_of_bounds_or_invalid"
        for key in ("temperature", "avg_logprob", "compression_ratio", "no_speech_prob"):
            if key in item:
                number = item[key]
                if type(number) not in (int, float) or not math.isfinite(number):
                    raise ValueError("Invalid reference result")
                row[key] = number
        rows.append(row)
        cursor = position + len(value)
    if text[cursor:].strip() or (text and not rows):
        mapping_failed = True
    if mapping_failed:
        rows = [{"text": text, "start_offset": 0, "end_offset": len(text), "start": None, "end": None,
                 "kind": "segment", "timing_source": "openai-whisper-reference",
                 "timing_status": "unknown_reference_mapping"}] if text else []
    return {"text": text, "segments": rows, "duration": duration, "language": language,
            "timing_qualified": False, "segment_mapping": "unknown" if mapping_failed else "exact_text",
            "reference_segments": original, "decoding_observations": observations,
            "decoding_budget_status": status}
