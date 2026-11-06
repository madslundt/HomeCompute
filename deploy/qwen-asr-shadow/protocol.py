"""Bounded diagnostic requests; text is not assigned invented time bounds."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import io
import re
import struct
import wave

MAX_BODY_BYTES = 1_000_000
MAX_AUDIO_SECONDS = 30
MAX_RESPONSE_BYTES = 8_000_000
FIELDS = frozenset({"model", "response_format", "language"})
MODELS = frozenset({"whisper", "qwen3-asr-0.6b", "Qwen/Qwen3-ASR-0.6B-hf"})


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
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise ValueError("Invalid ASR result")
    text = result["text"]
    if len(text.encode("utf-8")) > 1_000_000:
        raise ValueError("ASR result exceeds bound")
    generation = result.get("generation")
    required = {"generated_tokens", "max_new_tokens", "eos_reached", "last_token_id",
                "budget_reached", "prompt_tokens", "audio_tokens", "coverage_claim"}
    if not isinstance(generation, dict) or set(generation) != required:
        raise ValueError("Missing generation evidence")
    for key in ("generated_tokens", "max_new_tokens", "last_token_id", "prompt_tokens", "audio_tokens"):
        if type(generation[key]) is not int:
            raise ValueError("Invalid generation evidence")
    count = generation["generated_tokens"]
    if not 1 <= count <= 512 or generation["max_new_tokens"] != 512 or \
            not 0 <= generation["last_token_id"] < 151936 or \
            not 0 < generation["audio_tokens"] < generation["prompt_tokens"] <= 4096:
        raise ValueError("Invalid generation evidence")
    if type(generation["eos_reached"]) is not bool or type(generation["budget_reached"]) is not bool or \
            generation["eos_reached"] != (generation["last_token_id"] in (151643, 151645)) or \
            generation["budget_reached"] != (count >= 512) or generation["coverage_claim"] is not False:
        raise ValueError("Invalid generation evidence")
    if result.get("language") is not None and not re.fullmatch("[a-z]{2,3}", result["language"]):
        raise ValueError("Invalid model language metadata")
    if not isinstance(result.get("language_label"), str) or len(result["language_label"]) > 256 or \
            not isinstance(result.get("raw_decoded"), str):
        raise ValueError("Invalid model envelope")
    ids = result.get("generated_token_ids")
    if not isinstance(ids, list) or len(ids) != count or \
            any(type(token) is not int or not 0 <= token < 151936 for token in ids) or \
            ids[-1] != generation["last_token_id"]:
        raise ValueError("Invalid generated token record")
    rows = [{"text": text, "start_offset": 0, "end_offset": len(text),
             "start": None, "end": None, "kind": "segment", "timing_source": "not_provided",
             "timing_status": "unknown_model_timing"}] if text else []
    return {"text": text, "segments": rows, "duration": duration,
            "language": result["language"], "language_label": result["language_label"],
            "generation": generation, "raw_decoded": result["raw_decoded"],
            "generated_token_ids": ids, "timing_qualified": False}
