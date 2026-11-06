"""Conservative, bounded recovery after a trusted native timestamp ends early."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import io
import json
import math
import time
import wave

VERSION = "silero-v5.1.2-cpu-prob0.5-grace500ms-contiguous512ms-suffix-v1"
VAD_VERSION = "silero-v5.1.2"
RATE = 16000
FRAME_SAMPLES = 512
MIN_VOICED_FRAMES = 16
TIMESTAMP_GRACE_SAMPLES = 8000


_SPEC = importlib.util.spec_from_file_location("whisper_silero_vad", Path(__file__).with_name("silero_vad.py"))
_VAD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VAD)

_BOUNDARY_SPEC = importlib.util.spec_from_file_location(
    "whisper_language_boundaries", Path(__file__).with_name("language_boundaries.py"))
_BOUNDARIES = importlib.util.module_from_spec(_BOUNDARY_SPEC)
_BOUNDARY_SPEC.loader.exec_module(_BOUNDARIES)


def verify_detector():
    _VAD.verify_model()
    values = activity(bytes(FRAME_SAMPLES * 2))
    if len(values) != 1 or not math.isfinite(values[0]) or not 0 <= values[0] < 0.5:
        raise RuntimeError("VAD silence warmup failed")


def activity(pcm):
    return _VAD.probabilities(pcm)


class ObservationUnavailable(RuntimeError):
    pass


def candidate(audio: bytes, result: dict):
    """Unknown timestamps or speech observations never authorize a new request.

    VAD observes the exact submitted PCM on CPU, without adding gain or cutting context.
    It is a request gate, not evidence of transcript correctness or coverage.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
                return None
            frames = source.getnframes()
            pcm = source.readframes(frames)
        if len(pcm) != frames * 2 or frames > 30 * RATE:
            return None
        text, rows = result.get("text"), result.get("segments")
        if not isinstance(text, str) or not text.strip() or not isinstance(rows, list) or not rows:
            return None
        cursor, previous_end = 0, 0.0
        for row in rows:
            start, end = row.get("start"), row.get("end")
            value = row.get("text")
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end)
                    or not previous_end <= start < end <= frames / RATE
                    or row.get("timing_status") is not None
                    or row.get("kind") != "segment" or not isinstance(value, str) or not value.strip()
                    or type(row.get("start_offset")) is not int or type(row.get("end_offset")) is not int
                    or row["start_offset"] != cursor or row["end_offset"] != cursor + len(value)
                    or text[cursor:cursor + len(value) + 1] != value + "\n"):
                return None
            cursor += len(value) + 1
            previous_end = end
        if cursor != len(text):
            return None
        # Round down to avoid dropping a source frame through float conversion.
        seek = math.floor(previous_end * RATE)
        if seek < RATE // 10 or frames - seek < TIMESTAMP_GRACE_SAMPLES + MIN_VOICED_FRAMES * FRAME_SAMPLES:
            return None
        try:
            probabilities = activity(pcm)
        except Exception:
            raise ObservationUnavailable("Speech observation unavailable") from None
        if len(probabilities) != (frames + FRAME_SAMPLES - 1) // FRAME_SAMPLES:
            raise ObservationUnavailable("Speech observation frame count differs")
        sustained = 0
        for index, probability in enumerate(probabilities):
            if (type(probability) not in (int, float) or not math.isfinite(probability)
                    or not 0 <= probability <= 1):
                raise ObservationUnavailable("Invalid speech observation")
            begin = index * FRAME_SAMPLES
            complete = begin + FRAME_SAMPLES <= frames
            voiced = complete and begin >= seek + TIMESTAMP_GRACE_SAMPLES and probability >= 0.5
            sustained = sustained + 1 if voiced else 0
            if sustained >= MIN_VOICED_FRAMES:
                output = io.BytesIO()
                with wave.open(output, "wb") as target:
                    target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
                    target.writeframes(pcm[seek * 2:])
                return seek, frames, output.getvalue()
    except ObservationUnavailable:
        raise
    except Exception:
        return None
    return None


def transcribe(audio, duration, native_fields, timeout, *, request, codec, clock=time.monotonic,
               language_boundaries=False):
    """At most one additional native call per <=30s window; no recursive retries."""
    deadline = clock() + timeout
    response_bytes, native_calls = 0, 0
    audits = []
    boundary_audits = []
    language_probes = 0

    def call(data, fields=None):
        nonlocal response_bytes, native_calls
        remaining = deadline - clock()
        if remaining <= 0:
            raise codec.RequestError(502, "Speech backend deadline exceeded")
        body, content_type = codec.make_native_multipart(native_fields if fields is None else fields, data)
        raw = request(body, content_type, remaining)
        native_calls += 1
        response_bytes += len(raw)
        if clock() >= deadline:
            raise codec.RequestError(502, "Speech backend deadline exceeded")
        if response_bytes > codec.MAX_RESPONSE_BYTES:
            raise codec.RequestError(502, "Speech backend response is too large")
        return raw

    def one(offset, window_duration, window):
        raw = call(window)
        normalized, _ = codec.normalize_native_response(raw, "verbose_json", window_duration)
        try:
            tail = candidate(window, json.loads(normalized))
        except ObservationUnavailable:
            raise codec.RequestError(502, "Speech observation unavailable") from None
        if tail is None:
            return raw
        seek, frames, suffix = tail
        extra = call(suffix)
        combined, _ = codec.combine_native_windows(
            [(0, window_duration, raw), (seek / RATE, (frames - seek) / RATE, extra)],
            "verbose_json", window_duration)
        audits.append({"source_window_start_frame": round(offset * RATE),
                       "source_window_frames": frames, "continuation_start_frame": seek,
                       "continuation_frames": frames - seek,
                       "overlapping_acoustic_context": True})
        return combined

    def planned_window(offset, window_duration, window):
        nonlocal language_probes
        fields = dict(native_fields)
        candidates = fields.get("language_candidates")
        if (not language_boundaries or candidates is None
                or fields.get("language", "auto") != "auto"
                or fields.get("translate", "false") != "false"):
            return one(offset, window_duration, window)
        try:
            children = _BOUNDARIES.partitions(window, observe=activity)
        except Exception:
            raise codec.RequestError(502, "Language boundary observation unavailable") from None
        if len(children) == 1:
            return one(offset, window_duration, window)
        probes = []
        probe_fields = [(key, value) for key, value in native_fields if key != "detect_language"]
        probe_fields.append(("detect_language", "true"))
        for _child_offset, child_duration, data in children:
            raw = call(data, probe_fields)
            language_probes += 1
            try:
                value = json.loads(raw)
                if (not isinstance(value, dict) or value.get("text") != ""
                        or value.get("segments") != [] or value.get("task") != "transcribe"
                        or type(value.get("duration")) not in (int, float)
                        or not math.isfinite(value["duration"])
                        or abs(value["duration"] - child_duration) > 0.001):
                    raise ValueError("Invalid language-only response")
                probes.append(value)
            except Exception:
                raise codec.RequestError(502, "Language boundary observation unavailable") from None
        try:
            changed = _BOUNDARIES.has_confident_language_change(probes, candidates.split(","))
        except Exception:
            raise codec.RequestError(502, "Language boundary observation unavailable") from None
        if not changed:
            # Identical/uncertain language keeps the entire original acoustic
            # context; no child transcript is produced or selected.
            return one(offset, window_duration, window)
        decoded = []
        partitions = []
        for (child_offset, child_duration, data), probe in zip(children, probes):
            child_raw = one(offset + child_offset, child_duration, data)
            normalized, _ = codec.normalize_native_response(child_raw, "verbose_json", child_duration)
            result = json.loads(normalized)
            actual_windows = result.get("language_windows")
            if actual_windows is None:
                actual_windows = [{"start": 0, "end": child_duration,
                                   "language": result.get("language"),
                                   "language_selection": result.get("language_selection")}]
            # Preserve actual ASR selections separately from the language probes.
            actual_windows = [{**item, "start": offset + child_offset + item["start"],
                               "end": offset + child_offset + item["end"]} for item in actual_windows]
            partitions.append({"start_frame": round(child_offset * RATE),
                               "frames": round(child_duration * RATE),
                               "language_selection": probe["language_selection"],
                               "asr_language_windows": actual_windows})
            decoded.append((child_offset, child_duration, child_raw))
        raw, _ = codec.combine_native_windows(decoded, "verbose_json", window_duration)
        boundary_audits.append({
            "source_window_start_frame": round(offset * RATE),
            "source_window_frames": round(window_duration * RATE),
            "partitions": partitions,
        })
        return raw

    if duration <= codec.NATIVE_WINDOW_SECONDS:
        raw = planned_window(0, duration, audio)
        payload, content_type = codec.normalize_native_response(raw, "verbose_json", duration)
    else:
        windows = ((offset, seconds, planned_window(offset, seconds, data))
                   for offset, seconds, data in codec.native_audio_windows(audio))
        payload, content_type = codec.combine_native_windows(windows, "verbose_json", duration)
    if audits or boundary_audits:
        value = json.loads(payload)
        if audits:
            value["suffix_recovery"] = {"version": VERSION, "windows": audits,
                                        "native_calls": native_calls,
                                        "max_extra_requests_per_window": 1, "coverage_claim": False}
        if boundary_audits:
            value["language_boundary_recovery"] = {
                "version": _BOUNDARIES.VERSION, "windows": boundary_audits,
                "native_calls": native_calls, "language_probes": language_probes,
                "asr_pcm_unchanged": True, "source_frames_conserved": True,
                "coverage_claim": False,
            }
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > codec.MAX_RESPONSE_BYTES:
            raise codec.RequestError(502, "Speech backend response is too large")
    if clock() >= deadline:
        raise codec.RequestError(502, "Speech backend deadline exceeded")
    return payload, content_type
