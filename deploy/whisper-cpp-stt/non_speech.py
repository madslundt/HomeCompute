"""Bounded non-speech decisions without lexical filtering or quiet-audio floors."""
from __future__ import annotations

from collections.abc import Callable, Sequence
import io
import math
import wave

VERSION = "exact-zero-or-normalized-vad0.5-native-no-speech0.6-v1"
RATE = 16000
FRAME_SAMPLES = 512
MAX_SAMPLES = 30 * RATE
MIN_OBSERVATION_SAMPLES = 3 * RATE
NATIVE_THRESHOLD = 0.6
VAD_THRESHOLD = 0.5


def _read_pcm(audio: bytes) -> tuple[int, bytes]:
    if not isinstance(audio, bytes):
        raise ValueError("Invalid bounded non-speech PCM WAV")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(),
                    source.getcomptype()) != (1, 2, RATE, "NONE"):
                raise ValueError("Invalid bounded non-speech PCM WAV")
            frames = source.getnframes()
            if not 0 < frames <= MAX_SAMPLES:
                raise ValueError("Invalid bounded non-speech PCM WAV")
            pcm = source.readframes(frames + 1)
    except (EOFError, wave.Error):
        raise ValueError("Invalid bounded non-speech PCM WAV") from None
    if len(pcm) != frames * 2:
        raise ValueError("Truncated non-speech PCM WAV")
    return frames, pcm


def decision(audio: bytes, result: dict, *,
             normalized_observation: Callable[[bytes], Sequence[float]]) -> dict | None:
    """Return an audit authorizing empty text, or preserve the entire hypothesis.

    Exact digital zero is independent of either model. For nonzero audio, both
    native non-speech confidence and normalized VAD must agree. Missing optional
    native confidence preserves text; failed or malformed observations raise.
    Neither hypothesis words nor signal loudness select a decision.
    """
    frames, pcm = _read_pcm(audio)
    if not isinstance(result, dict) or not isinstance(result.get("text"), str) or not result["text"].strip():
        return None
    audit = {"method": "exact_zero_pcm", "frames": frames,
             "native_min_no_speech_probability": None,
             "normalized_vad_max_probability": None,
             "normalized_vad_probability_count": 0,
             "native_segment_count": None, "coverage_claim": False}
    if not any(pcm):
        return audit
    if frames < MIN_OBSERVATION_SAMPLES:
        return None
    segments = result.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    confidences = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            return None
        if not segment["text"].strip():
            continue
        value = segment.get("no_speech_prob")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            return None
        confidences.append(value)
    if not confidences or min(confidences) < NATIVE_THRESHOLD:
        return None
    values = normalized_observation(audio)
    if not isinstance(values, (list, tuple)) or len(values) != (frames + FRAME_SAMPLES - 1) // FRAME_SAMPLES:
        raise ValueError("Non-speech observation frame count differs")
    for value in values:
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid non-speech observation")
    maximum = max(values)
    if maximum >= VAD_THRESHOLD:
        return None
    return {**audit, "method": "normalized_vad_and_native_no_speech",
            "native_min_no_speech_probability": min(confidences),
            "normalized_vad_max_probability": maximum,
            "normalized_vad_probability_count": len(values),
            "native_segment_count": len(confidences)}
