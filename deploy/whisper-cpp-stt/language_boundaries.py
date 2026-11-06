"""Acoustic boundary proposals, gated separately by native language observations.

Normalized samples are detector inputs only. Every returned WAV contains original
source samples, and detector probabilities do not establish silence or coverage.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import io
import math
import re
import struct
import wave

VERSION = "normalized-vad-1s-rms24dbfs-cap30db-pause800ms-language-confidence0.5-v1"
RATE = 16000
FRAME_SAMPLES = 512
MIN_PAUSE_FRAMES = 25
MIN_CHILD_SAMPLES = 48000
MAX_SAMPLES = 30 * RATE
MAX_GAIN = 10 ** (30 / 20)
TARGET_RMS = 32768 * 10 ** (-24 / 20)


def _normalized_pcm(pcm: bytes) -> bytes:
    # WAV PCM is little-endian on every host. Normalize each complete or partial
    # one-second block independently, retaining exact zero and never attenuating.
    samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
    for begin in range(0, len(samples), RATE):
        block = samples[begin:begin + RATE]
        rms = math.sqrt(sum(value * value for value in block) / len(block))
        gain = min(MAX_GAIN, max(1.0, TARGET_RMS / rms)) if rms else 1.0
        samples[begin:begin + len(block)] = [
            max(-32768, min(32767, round(value * gain))) for value in block
        ]
    return struct.pack(f"<{len(samples)}h", *samples)


def _read_pcm(audio: bytes) -> tuple[int, bytes]:
    if not isinstance(audio, bytes):
        raise ValueError("Invalid bounded PCM WAV")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(),
                    source.getcomptype()) != (1, 2, RATE, "NONE"):
                raise ValueError("Invalid bounded PCM WAV")
            frames = source.getnframes()
            if not 0 < frames <= MAX_SAMPLES:
                raise ValueError("Invalid bounded PCM WAV")
            pcm = source.readframes(frames + 1)
    except (EOFError, wave.Error):
        raise ValueError("Invalid bounded PCM WAV") from None
    if len(pcm) != frames * 2:
        raise ValueError("Truncated PCM WAV")
    return frames, pcm


def partitions(audio: bytes, *, observe: Callable[[bytes], Sequence[float]]) -> list[tuple[float, float, bytes]]:
    """Propose <=30s partitions without dropping, gaining or duplicating samples.

    Only complete probability frames can bound an internal low-probability run.
    All observations, including the partial final frame, must be valid. Failure
    raises instead of silently selecting a different request boundary.
    """
    frames, pcm = _read_pcm(audio)
    probabilities = observe(_normalized_pcm(pcm))
    if not isinstance(probabilities, (list, tuple)) or len(probabilities) != (frames + 511) // 512:
        raise ValueError("Speech observation frame count differs")
    for value in probabilities:
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid speech observation")
    cuts = [0]
    pause_start = None
    for index in range(frames // FRAME_SAMPLES):
        if probabilities[index] < 0.5:
            if pause_start is None:
                pause_start = index
        elif pause_start is not None:
            # A complete voiced frame precedes and follows this entire run.
            if pause_start > 0 and index - pause_start >= MIN_PAUSE_FRAMES:
                cut = (pause_start + index) * (FRAME_SAMPLES // 2)
                if cut - cuts[-1] >= MIN_CHILD_SAMPLES and frames - cut >= MIN_CHILD_SAMPLES:
                    cuts.append(cut)
            pause_start = None
    if len(cuts) == 1:
        return [(0.0, frames / RATE, audio)]
    cuts.append(frames)
    result = []
    for begin, end in zip(cuts, cuts[1:]):
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(RATE)
            target.writeframes(pcm[begin * 2:end * 2])
        result.append((begin / RATE, (end - begin) / RATE, output.getvalue()))
    return result


def _canonical_candidates(candidates: object) -> bool:
    return (isinstance(candidates, list) and 1 <= len(candidates) <= 16
            and all(isinstance(code, str) and re.fullmatch(r"[a-z]{2,3}", code) for code in candidates)
            and candidates == sorted(set(candidates)) and len(",".join(candidates)) <= 63)


def has_confident_language_change(probe_results: list[dict], candidates: list[str]) -> bool:
    """Require two different confident selected IDs; never inspect hypothesis text.

    Native detected language may be outside the restricted candidate list. Weak
    but valid observations retain the original context; malformed data raises.
    Validate the complete list even when earlier probes already show a change.
    """
    if not _canonical_candidates(candidates) or not isinstance(probe_results, list):
        raise ValueError("Invalid native language observations")
    confident = set()
    for result in probe_results:
        if not isinstance(result, dict):
            raise ValueError("Invalid native language observation")
        selection = result.get("language_selection")
        if (not isinstance(selection, dict) or selection.get("mode") != "candidate_auto"
                or selection.get("candidates") != candidates
                or not isinstance(selection.get("selected"), str) or selection["selected"] not in candidates
                or not isinstance(selection.get("detected"), str)
                or not re.fullmatch(r"[a-z]{2,3}", selection["detected"])):
            raise ValueError("Invalid native language observation")
        for key in ("selected_probability", "detected_probability"):
            value = selection.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Invalid native language probability")
        if selection["selected_probability"] >= 0.5:
            confident.add(selection["selected"])
    return len(confident) >= 2
