"""Acoustic boundary proposals, gated separately by native language observations.

Normalized samples are detector inputs only. Every returned WAV contains original
source samples, and detector probabilities do not establish silence or coverage.
"""
from __future__ import annotations

from array import array
from collections.abc import Callable, Sequence
import io
import math
import re
import sys
import wave

VERSION = "whole-source-normalized-vad-1s-rms24dbfs-cap30db-pause128ms-adjacent-language-confidence0.5-v2"
ISLAND_POLICY = "original-pcm-singleton-cut-confirmation-v2"
REFLOW_POLICY = "local-original-pcm-vad-pause-reflow-v1"
RATE = 16000
FRAME_SAMPLES = 512
MIN_PAUSE_FRAMES = 4
MIN_CHILD_SAMPLES = 48000
MAX_SAMPLES = 1200 * RATE
MAX_PROPOSAL_SAMPLES = 30 * RATE
MAX_GAIN = 10 ** (30 / 20)
TARGET_RMS = 32768 * 10 ** (-24 / 20)


def _normalized_pcm(pcm: bytes) -> bytes:
    # Use compact int16 storage even for the entire bounded recording. WAV PCM
    # is little-endian; array's native representation needs an explicit swap.
    samples = array("h", pcm)
    if samples.itemsize != 2:
        raise RuntimeError("Unsupported PCM sample representation")
    if sys.byteorder != "little":
        samples.byteswap()
    for begin in range(0, len(samples), RATE):
        block = samples[begin:begin + RATE]
        rms = math.sqrt(sum(value * value for value in block) / len(block))
        gain = min(MAX_GAIN, max(1.0, TARGET_RMS / rms)) if rms else 1.0
        if gain > 1:
            samples[begin:begin + len(block)] = array("h", (
                max(-32768, min(32767, round(value * gain))) for value in block))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


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


def _wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(RATE)
        target.writeframes(pcm)
    return output.getvalue()


def _bounded_ranges(begin: int, end: int):
    """Existing native-window geometry, including evenly shared tiny tails."""
    while begin < end:
        remaining = end - begin
        count = min(MAX_PROPOSAL_SAMPLES, remaining)
        if 0 < remaining - count < MIN_CHILD_SAMPLES:
            count = remaining // 2
        yield begin, begin + count
        begin += count


def observed_probabilities(audio: bytes, *, observe: Callable[[bytes], Sequence[float]]) -> list[float]:
    """Validate probabilities from the unchanged normalized detector-copy policy."""
    frames, pcm = _read_pcm(audio)
    probabilities = observe(_normalized_pcm(pcm))
    if not isinstance(probabilities, (list, tuple)) or len(probabilities) != (frames + 511) // 512:
        raise ValueError("Speech observation frame count differs")
    for value in probabilities:
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid speech observation")
    return list(probabilities)


def partitions(audio: bytes, *, observe: Callable[[bytes], Sequence[float]],
               pause_cuts: list[int] | None = None) -> list[tuple[float, float, bytes]]:
    """Observe <=1200s globally, then propose sample-conserving <=30s atoms.

    Acoustic proposals precede fixed ASR windows, so language regions can cross
    old thirty-second boundaries. Only complete frames bound pause candidates;
    every observation, including the partial final frame, must be valid.
    """
    probabilities = observed_probabilities(audio, observe=observe)
    frames, pcm = _read_pcm(audio)
    cuts = [0]
    pause_start = None
    for index in range(frames // FRAME_SAMPLES):
        if probabilities[index] < 0.5:
            if pause_start is None:
                pause_start = index
        elif pause_start is not None:
            if pause_start > 0 and index - pause_start >= MIN_PAUSE_FRAMES:
                cut = (pause_start + index) * (FRAME_SAMPLES // 2)
                if cut - cuts[-1] >= MIN_CHILD_SAMPLES and frames - cut >= MIN_CHILD_SAMPLES:
                    cuts.append(cut)
                    if pause_cuts is not None:
                        pause_cuts.append(cut)
            pause_start = None
    if len(cuts) == 1 and frames <= MAX_PROPOSAL_SAMPLES:
        return [(0.0, frames / RATE, audio)]
    cuts.append(frames)
    return [(begin / RATE, (end - begin) / RATE, _wav(pcm[begin * 2:end * 2]))
            for left, right in zip(cuts, cuts[1:]) for begin, end in _bounded_ranges(left, right)]


def _canonical_candidates(candidates: object) -> bool:
    return (isinstance(candidates, list) and 1 <= len(candidates) <= 16
            and all(isinstance(code, str) and re.fullmatch(r"[a-z]{2,3}", code) for code in candidates)
            and candidates == sorted(set(candidates)) and len(",".join(candidates)) <= 63)


def _selections(probe_results: list[dict], candidates: list[str]) -> list[dict]:
    """Validate every native selection without inspecting hypothesis text.

    The unrestricted detected language may lie outside the candidate list.
    Confidence affects region decisions only after all observations are valid.
    """
    if not _canonical_candidates(candidates) or not isinstance(probe_results, list):
        raise ValueError("Invalid native language observations")
    selections = []
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
        selections.append(selection)
    return selections


def has_confident_language_change(probe_results: list[dict], candidates: list[str]) -> bool:
    """Compatibility predicate; region selection uses adjacent evidence instead."""
    return len({item["selected"] for item in _selections(probe_results, candidates)
                if item["selected_probability"] >= 0.5}) >= 2


def _sample_position(seconds: object) -> int:
    if type(seconds) not in (int, float) or not math.isfinite(seconds):
        raise ValueError("Invalid proposal geometry")
    scaled = seconds * RATE
    if not math.isfinite(scaled) or abs(scaled - round(scaled)) > 0.000001:
        raise ValueError("Proposal geometry is not sample exact")
    return round(scaled)


def _boundary_cuts(selections: list[dict], ends: list[int],
                   proposals: list[tuple[float, float, bytes]],
                   observe_islands: Callable[[tuple[bytes, ...]], Sequence[str]] | None,
                   island_audit: list[dict] | None) -> tuple[list[int], list[int]]:
    cuts = {index for index, (left, right) in enumerate(zip(selections, selections[1:]))
            if (left["selected"] != right["selected"] and left["selected_probability"] >= 0.5
                and right["selected_probability"] >= 0.5)}
    original = [ends[index] for index in sorted(cuts)]
    if observe_islands is None:
        return original, original
    islands = [i for i in range(1, len(selections) - 1) if i - 1 in cuts and i in cuts
               and selections[i - 1]["selected"] == selections[i + 1]["selected"]]
    if not islands:
        return original, original
    # This is one batch of the exact original proposals, after validating source
    # geometry. An observer may veto a pair of existing cuts, never create a cut
    # or select the language of a native decode.
    observed = observe_islands(tuple(proposals[i][2] for i in islands))
    if (not isinstance(observed, (tuple, list)) or len(observed) != len(islands)
            or any(not isinstance(code, str) or not re.fullmatch(r"[a-z]{2,3}", code)
                   for code in observed)):
        raise ValueError("Invalid independent language observations")
    unconfirmed = {i for i, code in zip(islands, observed) if code != selections[i]["selected"]}
    for i, code in zip(islands, observed):
        # Disagreement with the isolated proposal does not establish another
        # language. Retain context; overlapping veto requests keep both pairs.
        veto = i in unconfirmed and i - 1 not in unconfirmed and i + 1 not in unconfirmed
        if veto:
            cuts.difference_update((i - 1, i))
        if island_audit is not None:
            island_audit.append({"source_start_frame": ends[i - 1], "frames": ends[i] - ends[i - 1],
                                 "native_language": selections[i]["selected"],
                                 "flanking_language": selections[i - 1]["selected"],
                                 "observed_language": code, "vetoed": veto})
    return original, [ends[index] for index in sorted(cuts)]


def _pause_layout(begin: int, end: int, pauses: Sequence[int],
                  islands: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Fit existing acoustic pauses to the same minimum decode count, if possible."""
    bounded = list(_bounded_ranges(begin, end))
    count = len(bounded)
    if count == 1:
        return bounded
    length = end - begin
    # Keep at least one minimum decode window of context around vetoed speech
    # when feasible, then try other real pauses outside the rejected islands.
    for margin in (MIN_CHILD_SAMPLES, 0):
        available = [p for p in pauses if begin < p < end
                     and not any(a - margin <= p <= b + margin for a, b in islands)]
        states = {begin: (0, ())}
        for step in range(1, count):
            remaining = count - step
            next_states = {}
            for cut in available:
                if not remaining * MIN_CHILD_SAMPLES <= end - cut <= remaining * MAX_PROPOSAL_SAMPLES:
                    continue
                distance = abs((cut - begin) * count - length * step)
                paths = ((cost + distance, (*path, cut)) for prior, (cost, path) in states.items()
                         if MIN_CHILD_SAMPLES <= cut - prior <= MAX_PROPOSAL_SAMPLES)
                best = min(paths, default=None)
                if best is not None:
                    next_states[cut] = best
            states = next_states
            if not states:
                break
        if states:
            _, path = min(states.values())
            points = (begin, *path, end)
            return list(zip(points, points[1:]))
    # Dense speech may offer no usable set of pauses. The deterministic
    # bounded fallback still reflows only this block and preserves every frame.
    return bounded


def _scoped_layout(begin: int, end: int, cuts: Sequence[int],
                   islands: Sequence[tuple[int, int]], pauses: Sequence[int]
                   ) -> list[tuple[int, int]]:
    """Preserve the old primary windows outside overlapping veto neighborhoods."""
    old_points = [begin, *(cut for cut in cuts if begin < cut < end), end]
    old = [window for left, right in zip(old_points, old_points[1:])
           for window in _bounded_ranges(left, right)]
    index = {window: i for i, window in enumerate(old)}
    blocks = []
    for island in islands:
        i = index[island]  # One exact original proposal, never over 30 seconds.
        if not 0 < i < len(old) - 1:
            raise ValueError("Vetoed island lacks neighboring context")
        start, stop = i - 1, i + 2
        if blocks and start < blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], stop)
            blocks[-1][2].append(island)
        else:
            blocks.append([start, stop, [island]])
    result, cursor = [], 0
    for start, stop, affected in blocks:
        result.extend(old[cursor:start])
        layout = _pause_layout(old[start][0], old[stop - 1][1], pauses, affected)
        if len(layout) > stop - start:
            raise ValueError("Scoped reflow added primary decodes")
        result.extend(layout)
        cursor = stop
    result.extend(old[cursor:])
    return result


def scoped_audio_windows(audio: bytes, region_start: int, ranges: Sequence[tuple[int, int]]):
    """Yield absolute-window geometry relative to its region, with original PCM."""
    frames, pcm = _read_pcm(audio)
    cursor = region_start
    for begin, end in ranges:
        if begin != cursor or not 0 < end - begin <= MAX_PROPOSAL_SAMPLES or end > region_start + frames:
            raise ValueError("Invalid scoped decode geometry")
        cursor = end
    if cursor != region_start + frames:
        raise ValueError("Scoped windows did not cover the region")
    for begin, end in ranges:
        yield (begin - region_start) / RATE, (end - begin) / RATE, _wav(
            pcm[(begin - region_start) * 2:(end - region_start) * 2])


def language_regions(audio: bytes, proposals: list[tuple[float, float, bytes]],
                     probes: list[dict], candidates: list[str], *,
                     observe_islands: Callable[[tuple[bytes, ...]], Sequence[str]] | None = None,
                     island_audit: list[dict] | None = None,
                     pause_cuts: Sequence[int] = (),
                     scoped_windows: dict[int, list[tuple[int, int]]] | None = None
                     ) -> list[tuple[float, float, bytes]] | None:
    """Merge all adjacent proposals lacking a confident language transition.

    All source samples and observations are validated before any decision. Weak
    intervening evidence cannot establish a boundary between nonadjacent probes.
    Returned global regions may exceed thirty seconds; callers bound ASR later.
    """
    frames, pcm = _read_pcm(audio)
    selections = _selections(probes, candidates)
    if not isinstance(proposals, list) or not proposals or len(proposals) != len(selections):
        raise ValueError("Proposal and language observation counts differ")
    ends, cursor = [], 0
    for proposal in proposals:
        if not isinstance(proposal, (tuple, list)) or len(proposal) != 3:
            raise ValueError("Invalid proposal geometry")
        offset, duration, data = proposal
        begin, count = _sample_position(offset), _sample_position(duration)
        minimum = min(frames, MIN_CHILD_SAMPLES)
        if begin != cursor or not minimum <= count <= MAX_PROPOSAL_SAMPLES or begin + count > frames:
            raise ValueError("Invalid proposal geometry")
        actual_count, actual_pcm = _read_pcm(data)
        if actual_count != count or actual_pcm != pcm[begin * 2:(begin + count) * 2]:
            raise ValueError("Proposal PCM differs from source")
        cursor += count
        ends.append(cursor)
    if cursor != frames:
        raise ValueError("Proposals do not conserve every source sample")
    audit = []
    original, interior = _boundary_cuts(selections, ends, proposals, observe_islands, audit)
    if island_audit is not None:
        island_audit.extend(audit)
    cuts = [0, *interior, frames]
    if scoped_windows is not None and original != interior:
        islands = [(r["source_start_frame"], r["source_start_frame"] + r["frames"])
                   for r in audit if r["vetoed"]]
        for begin, end in zip(cuts, cuts[1:]):
            affected = [island for island in islands if begin < island[0] and island[1] < end]
            if affected:
                scoped_windows[begin] = _scoped_layout(begin, end, original, affected, pause_cuts)
    if not interior and not scoped_windows:
        return None
    if not interior:
        return [(0.0, frames / RATE, audio)]
    return [(begin / RATE, (end - begin) / RATE, _wav(pcm[begin * 2:end * 2]))
            for begin, end in zip(cuts, cuts[1:])]
