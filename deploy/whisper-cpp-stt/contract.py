#!/usr/bin/env python3
"""Bounded HTTP input/output codecs for the pinned Whisper candidate."""
from email import policy
from email.parser import BytesParser
import io
import json
import math
import re
import secrets
import wave

MODEL_ID = "whisper-large-v3"
ALIASES = {"whisper", MODEL_ID, "stt-danish", "whisper-turbo"}
MAX_AUDIO_SECONDS = 180
MAX_AUDIO_BYTES = MAX_AUDIO_SECONDS * 16_000 * 2 + 44
MAX_BODY_BYTES = MAX_AUDIO_BYTES + 128_000
MAX_CONFIGURED_AUDIO_SECONDS = 7_200
MAX_CONFIGURED_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_RESPONSE_BYTES = 8_000_000
# WHISPER_MAX_DECODERS in the checksum-pinned upstream src/whisper.cpp.
MAX_NATIVE_DECODERS = 8
CONTROL_TOKEN = re.compile(r"<\|[^|\r\n]{1,80}\|>")
FIELD_NAME = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,80}$")
ALLOWED_FIELDS = {"model", "response_format", "language", "language_candidates", "prompt", "temperature",
                  "temperature_inc", "no_timestamps", "max_context",
                  "translate", "carry_initial_prompt", "beam_size", "best_of", "max_len"}

class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message



def parse_multipart(body: bytes, content_type: str, *, max_body_bytes: int = MAX_BODY_BYTES) -> tuple[dict[str, str], bytes]:
    if len(body) > max_body_bytes or "\r" in content_type or "\n" in content_type or len(content_type) > 512:
        raise RequestError(413 if len(body) > max_body_bytes else 400, "Invalid or oversized transcription request")
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            ("MIME-Version: 1.0\r\nContent-Type: " + content_type + "\r\n\r\n").encode("ascii") + body
        )
    except (ValueError, UnicodeError):
        raise RequestError(400, "Invalid multipart transcription request") from None
    boundary = message.get_boundary()
    if message.get_content_type() != "multipart/form-data" or not message.is_multipart() or \
            not boundary or len(boundary) > 70 or message.defects:
        raise RequestError(400, "Invalid multipart transcription request")
    fields: dict[str, str] = {}
    audio_items: list[bytes] = []
    for index, part in enumerate(message.iter_parts()):
        if index >= 64 or part.is_multipart() or part.defects or part.get_content_disposition() != "form-data":
            raise RequestError(400, "Invalid multipart transcription request")
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
            raise RequestError(400, "Invalid multipart transcription request")
        if part.get("Content-Transfer-Encoding", "binary").lower() not in {"binary", "7bit", "8bit"}:
            raise RequestError(400, "Invalid multipart transcription request")
        value = part.get_payload(decode=True)
        if not isinstance(value, bytes):
            raise RequestError(400, "Invalid multipart transcription request")
        if name == "file":
            if part.get_filename() is None or not value:
                raise RequestError(400, "Exactly one nonempty audio file is required")
            audio_items.append(value)
            continue
        if part.get_filename() is not None:
            raise RequestError(400, "Invalid transcription parameter")
        if name not in ALLOWED_FIELDS and name != "timestamp_granularities[]":
            raise RequestError(400, "Unsupported transcription parameter")
        if len(value) > 16_384:
            raise RequestError(400, "Invalid transcription parameter")
        try:
            decoded = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RequestError(400, "Invalid transcription parameter") from None
        if name == "timestamp_granularities[]":
            fields.setdefault(name, "")
            fields[name] += ("\n" if fields[name] else "") + decoded
        elif name in fields:
            raise RequestError(400, "Duplicate transcription parameter")
        else:
            fields[name] = decoded
    if len(audio_items) != 1:
        raise RequestError(400, "Exactly one audio file is required")
    return fields, audio_items[0]


def validate_audio(audio: bytes, max_seconds: int) -> float:
    if len(audio) > max_seconds * 32_000 + 44:
        raise RequestError(413, "Audio duration exceeds the limit")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getcomptype() != "NONE" or source.getnchannels() != 1 or
                    source.getsampwidth() != 2 or source.getframerate() != 16_000):
                raise RequestError(400, "Audio must be mono 16 kHz PCM16 WAV")
            frames = source.getnframes()
            if not 0 < frames <= max_seconds * 16_000:
                raise RequestError(413 if frames > max_seconds * 16_000 else 400,
                                   "Audio duration is outside the supported range")
            expected_pcm_bytes = frames * source.getnchannels() * source.getsampwidth()
            if len(source.readframes(frames)) != expected_pcm_bytes:
                raise RequestError(400, "PCM WAV payload is truncated")
            return frames / 16_000
    except (wave.Error, EOFError, OSError):
        raise RequestError(400, "Invalid PCM WAV audio") from None


def normalize_fields(fields: dict[str, str]) -> tuple[str, list[tuple[str, str]]]:
    model = fields.get("model", "")
    if model not in ALIASES:
        raise RequestError(400, "Unsupported transcription model")
    response_format = fields.get("response_format", "json")
    if response_format not in {"text", "json", "verbose_json"}:
        raise RequestError(400, "Only text, json and verbose_json response formats are supported")
    if "timestamp_granularities[]" in fields:
        granularities = fields["timestamp_granularities[]"].split("\n")
        if len(set(granularities)) != len(granularities) or any(g not in {"segment", "word"} for g in granularities):
            raise RequestError(400, "Invalid timestamp granularity")
        if "word" in granularities:
            raise RequestError(422, "Word timestamps are unsupported; only native segment timestamps are available")
        if response_format != "verbose_json" and granularities:
            raise RequestError(400, "Segment timestamps require verbose_json")
        if "segment" in granularities and fields.get("no_timestamps", "false").lower() in {"true", "1"}:
            raise RequestError(400, "Segment timestamps conflict with no_timestamps")
    native: list[tuple[str, str]] = [("response_format", response_format)]
    language = fields.get("language", "auto")
    if language != "auto" and not re.fullmatch(r"[A-Za-z]{2,3}", language):
        raise RequestError(400, "Unsupported language value")
    native.append(("language", language.lower()))
    if "language_candidates" in fields:
        native.append(("language_candidates", normalize_language_candidates(fields["language_candidates"])))
    if "prompt" in fields:
        if "\x00" in fields["prompt"]:
            raise RequestError(400, "Invalid transcription parameter")
        native.append(("prompt", fields["prompt"]))
    for name in ("temperature", "temperature_inc"):
        if name in fields:
            try:
                number = float(fields[name])
            except ValueError:
                raise RequestError(400, "Invalid decoding parameter") from None
            if not math.isfinite(number) or not 0.0 <= number <= 1.0:
                raise RequestError(400, "Invalid decoding parameter")
            native.append((name, str(number)))
    for name in ("no_timestamps", "translate", "carry_initial_prompt"):
        if name in fields:
            value = fields[name].lower()
            if value not in {"true", "false", "1", "0"}:
                raise RequestError(400, "Invalid decoding parameter")
            native.append((name, "true" if value in {"true", "1"} else "false"))
    if "max_context" in fields:
        try:
            value = int(fields["max_context"])
        except ValueError:
            raise RequestError(400, "Invalid decoding parameter") from None
        if not 0 <= value <= 448:
            raise RequestError(400, "Invalid decoding parameter")
        native.append(("max_context", str(value)))
    beam_size = _bounded_integer(fields.get("beam_size", "5"), "beam_size", -1, MAX_NATIVE_DECODERS,
                                 allow_minus_one=True)
    best_of = _bounded_integer(fields.get("best_of", "5"), "best_of", 1, MAX_NATIVE_DECODERS)
    max_len = _bounded_integer(fields.get("max_len", "-1"), "max_len", -1, 1000,
                               allow_minus_one=True)
    native.extend((("beam_size", str(beam_size)), ("best_of", str(best_of)),
                   ("max_len", str(max_len))))
    return response_format, native


def normalize_language_candidates(raw: str) -> str:
    # Native validates membership in the pinned model's language vocabulary.
    # Reject controls, aliases and duplicates before forwarding any audio.
    if len(raw) > 127 or re.search(r"[^A-Za-z, ]", raw):
        raise RequestError(400, "Invalid language candidates")
    codes = [code.strip(" ").lower() for code in raw.split(",")]
    canonical = ",".join(sorted(codes))
    if not 1 <= len(codes) <= 16 or len(canonical) > 63 or \
            len(set(codes)) != len(codes) or any(not re.fullmatch(r"[a-z]{2,3}", code) for code in codes):
        raise RequestError(400, "Invalid language candidates")
    return canonical


def _language_selection(value: object) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("mode"), str) or \
            value["mode"] not in {"candidate_auto", "explicit"}:
        raise RequestError(502, "Speech backend returned invalid language selection")
    codes = value.get("candidates")
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        raise RequestError(502, "Speech backend returned invalid language selection")
    try:
        if normalize_language_candidates(",".join(codes)).split(",") != codes:
            raise ValueError()
    except ValueError:
        raise RequestError(502, "Speech backend returned invalid language selection") from None
    if not isinstance(value.get("selected"), str) or not re.fullmatch(r"[a-z]{2,3}", value["selected"]):
        raise RequestError(502, "Speech backend returned invalid language selection")
    if value["mode"] == "candidate_auto":
        if value["selected"] not in codes or not isinstance(value.get("detected"), str) or \
                not re.fullmatch(r"[a-z]{2,3}", value["detected"]):
            raise RequestError(502, "Speech backend returned invalid language selection")
        for key in ("detected_probability", "selected_probability"):
            number = value.get(key)
            if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 1:
                raise RequestError(502, "Speech backend returned invalid language selection")
    return value


def _bounded_integer(raw: str, name: str, minimum: int, maximum: int, *, allow_minus_one=False) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise RequestError(400, "Invalid decoding parameter") from None
    if value > maximum or (value < minimum and not (allow_minus_one and value == -1)) or value == 0:
        raise RequestError(400, "Invalid decoding parameter")
    return value


def make_native_multipart(fields: list[tuple[str, str]], audio: bytes) -> tuple[bytes, str]:
    boundary = "hc-whisper-cpp-" + secrets.token_hex(16)
    parts: list[bytes] = []
    for name, value in fields:
        if not FIELD_NAME.fullmatch(name):
            raise RequestError(400, "Invalid transcription parameter")
        parts.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                      value.encode("utf-8"), b"\r\n"))
    parts.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                  audio, f"\r\n--{boundary}--\r\n".encode()))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        raise RequestError(502, "Speech backend returned invalid text")
    return CONTROL_TOKEN.sub("", value)


def normalize_native_response(payload: bytes, response_format: str, duration: float) -> tuple[bytes, str]:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RequestError(502, "Speech backend response is too large")
    if response_format == "text":
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RequestError(502, "Speech backend returned invalid text") from None
        return CONTROL_TOKEN.sub("", text).encode("utf-8"), "text/plain; charset=utf-8"
    if response_format != "verbose_json":
        try:
            result = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RequestError(502, "Speech backend returned invalid JSON") from None
        if not isinstance(result, dict):
            raise RequestError(502, "Speech backend returned invalid JSON")
        result["text"] = _clean_text(result.get("text"))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), "application/json"
    try:
        result = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RequestError(502, "Speech backend returned invalid JSON") from None
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        raise RequestError(502, "Speech backend returned invalid verbose JSON")
    result["text"] = _clean_text(result.get("text"))
    if "language_selection" in result:
        result["language_selection"] = _language_selection(result["language_selection"])
    cursor = 0
    exact_serialization = True
    parts: list[str] = []
    for index, segment in enumerate(result["segments"]):
        if not isinstance(segment, dict):
            raise RequestError(502, "Speech backend returned an invalid segment")
        segment_text = _clean_text(segment.get("text"))
        segment["text"] = segment_text
        segment["kind"] = "segment"
        # whisper.cpp token objects are model tokens, not word alignments.
        segment.pop("words", None)
        start = segment.get("start")
        end = segment.get("end")
        if start is not None or end is not None:
            if type(start) not in (int, float) or type(end) not in (int, float) or \
                    not math.isfinite(start) or not math.isfinite(end) or \
                    start < 0 or end < start or start > duration or \
                    (start == duration and end > duration):
                raise RequestError(502, "Speech backend returned out-of-bounds segment timestamps")
            if end > duration:
                # Canonical text remains usable, but the native decoder has not
                # provided supportable timing. Never clamp into invented bounds.
                segment["start"] = None
                segment["end"] = None
                segment["timing_status"] = "unknown_out_of_bounds"
        parts.append(segment_text + "\n")
        begin, finish = cursor, cursor + len(segment_text)
        if result["text"][begin:finish] != segment_text or \
                (finish < len(result["text"]) and result["text"][finish:finish + 1] != "\n"):
            exact_serialization = False
        if "kind" in segment:
            segment["start_offset"] = begin
            segment["end_offset"] = finish
        cursor = finish + 1
    if exact_serialization and "".join(parts) == result["text"]:
        pass
    else:
        for segment in result["segments"]:
            segment.pop("start_offset", None)
            segment.pop("end_offset", None)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), "application/json"


NATIVE_WINDOW_SECONDS = 30
MIN_NATIVE_TAIL_SECONDS = 3


def native_audio_windows(audio: bytes):
    """Partition validated PCM frames without changing, skipping or duplicating samples."""
    with wave.open(io.BytesIO(audio), "rb") as source:
        count = source.getnframes()
        frames_per_window = NATIVE_WINDOW_SECONDS * 16_000
        begin = 0
        while begin < count:
            remaining = count - begin
            frames = min(frames_per_window, remaining)
            if 0 < remaining - frames < MIN_NATIVE_TAIL_SECONDS * 16_000:
                # Avoid an isolated tiny final request. Divide the final pair
                # evenly by integer frames; every sample still appears once.
                frames = remaining // 2
            pcm = source.readframes(frames)
            if len(pcm) != frames * 2:
                raise RequestError(400, "PCM WAV payload is truncated")
            output = io.BytesIO()
            with wave.open(output, "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(16_000)
                target.writeframes(pcm)
            yield begin / 16_000, frames / 16_000, output.getvalue()
            begin += frames


def combine_native_windows(responses, response_format: str, duration: float) -> tuple[bytes, str]:
    """Combine independently normalized native windows, retaining only proved anchors."""
    text_parts = []
    text_length = 0
    last_character = ""
    segments = []
    language_windows = []
    languages = []
    task = None
    normalized_bytes = 0
    for offset, window_duration, raw in responses:
        normalized, content_type = normalize_native_response(raw, response_format, window_duration)
        normalized_bytes += len(normalized)
        if normalized_bytes > MAX_RESPONSE_BYTES:
            raise RequestError(502, "Speech backend response is too large")
        result = None if response_format == "text" else json.loads(normalized)
        text = normalized.decode("utf-8") if result is None else result["text"]
        # Keep each native canonical text intact. Add only an explicit separator
        # where two nonempty outputs otherwise concatenate words across a boundary.
        separator = "\n" if (text and text_length and
                              not last_character.isspace() and not text[0].isspace()) else ""
        text_parts.append(separator)
        text_length += len(separator)
        if response_format == "verbose_json":
            for segment in result["segments"]:
                segment["id"] = len(segments)
                if segment.get("start") is not None:
                    segment["start"] += offset
                    segment["end"] += offset
                if "start_offset" in segment:
                    segment["start_offset"] += text_length
                    segment["end_offset"] += text_length
                segments.append(segment)
            language = result.get("language")
            if not isinstance(language, str) or not language or len(language) > 64:
                language = None
            window = {"start": offset, "end": offset + window_duration, "language": language}
            if "language_selection" in result:
                window["language_selection"] = result["language_selection"]
            language_windows.append(window)
            if language is not None and text.strip() and language not in languages:
                languages.append(language)
            if task is None:
                task = result.get("task")
            elif result.get("task") != task:
                raise RequestError(502, "Speech backend returned inconsistent tasks")
        text_parts.append(text)
        text_length += len(text)
        if text:
            last_character = text[-1]
    text = "".join(text_parts)
    if response_format == "text":
        payload = text.encode("utf-8")
        content_type = "text/plain; charset=utf-8"
    else:
        result = {"text": text}
        if response_format == "verbose_json":
            result.update(segments=segments, duration=duration, language_windows=language_windows)
            if len(languages) == 1:
                result["language"] = languages[0]
            elif len(languages) > 1:
                result["language"] = "mixed"
            if task is not None:
                result["task"] = task
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        content_type = "application/json"
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RequestError(502, "Speech backend response is too large")
    return payload, content_type
