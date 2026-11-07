"""Bounded insertions from a donor decode with native token timing estimates."""
import re
import unicodedata

VERSION = "original-pcm-unique-anchor-insertion-v1"
TIMING = "whisper_cpp_dtw_token_boundary_estimate"
RATE = 16000
MAX_DONORS = 3
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def _lexemes(text):
    matches = list(WORD.finditer(text))
    return matches, [unicodedata.normalize("NFC", m.group()).casefold() for m in matches]


def _matches(tokens, anchor, tolerance):
    return [i for i in range(len(tokens) - len(anchor) + 1)
            if sum(a != b for a, b in zip(tokens[i:i + len(anchor)], anchor)) <= tolerance]


def _frame(seconds):
    if type(seconds) not in (int, float) or not 0 <= seconds <= 1200:
        raise ValueError("Missing native timestamp")
    exact = seconds * RATE
    value = round(exact)
    if value < 0 or abs(value - exact) > 0.001:
        raise ValueError("Native timestamp is outside sample grid")
    return value


def windows(islands, regions, proposals):
    """Select one original-PCM donor around each corroborated veto at a seam."""
    cuts = sorted({p["source_start_frame"] for p in proposals})
    decoded = [row for region in regions for row in region["decoded_windows"]]
    before = {row["source_start_frame"] + row["frames"]: row["source_start_frame"]
              for row in decoded}
    after = {row["source_start_frame"]: row["source_start_frame"] + row["frames"]
             for row in decoded}
    for island in islands:
        seam = island["source_start_frame"]
        language = island["flanking_language"]
        if (not island["vetoed"] or island["observed_language"] != language
                or seam not in before or seam not in after):
            continue
        target_end = seam + island["frames"]
        start = max(before[seam], seam - 18 * RATE)
        latest = min(start + 30 * RATE, after[seam])
        minimum = target_end + 3 * RATE
        if seam - start < 4 * RATE or minimum > latest:
            continue
        end = next((cut for cut in cuts if minimum <= cut <= latest), minimum)
        yield start, seam, target_end, end, language


def anchors(base, seam):
    """Bind anchors to adjacent canonical rows at a known primary window start."""
    text = base["text"]
    segments = base["segments"]
    for i, right in enumerate(segments[1:], 1):
        try:
            delta = _frame(right.get("start")) - seam
        except ValueError:
            continue
        if not 0 <= delta <= RATE // 2:
            continue
        left = segments[i - 1]
        if any(not isinstance(s.get("text"), str)
               or type(s.get("start_offset")) is not int or type(s.get("end_offset")) is not int
               or not 0 <= s["start_offset"] < s["end_offset"] <= len(text)
               or text[s["start_offset"]:s["end_offset"]] != s["text"] for s in (left, right)):
            return None
        if text[left["end_offset"]:right["start_offset"]] != "\n":
            return None
        ending = left["text"].rstrip()
        if not (ending.endswith(("...", "…"))
                or left.get("timing_status") == "unknown_out_of_bounds"
                and not ending.endswith((".", "!", "?"))):
            return None
        _, prefix = _lexemes(left["text"])
        matches, suffix = _lexemes(right["text"])
        if len(prefix) < 6 or len(suffix) < 3:
            return None
        if right["text"][:matches[0].start()].strip():
            return None
        return right["start_offset"] + matches[0].start(), prefix[-10:], suffix[:6]
    return None


def _word_times(segment, matches):
    """Use the first token's DTW point and the following token's point as bounds."""
    pieces = segment.get("words")
    if not isinstance(pieces, list) or not pieces or len(pieces) > 512:
        return None
    cursor = 0
    parts = []
    for piece in pieces:
        token = piece.get("word") if isinstance(piece, dict) else None
        if not isinstance(token, str) or not token:
            return None
        parts.append((cursor, cursor + len(token), piece))
        cursor += len(token)
    if "".join(piece["word"] for _, _, piece in parts) != segment["text"]:
        return None
    bounds = []
    points = [piece.get("t_dtw") for _, _, piece in parts]
    for match in matches:
        indices = []
        for index, (a, b, _) in enumerate(parts):
            if a < match.end() and b > match.start():
                if WORD.search(segment["text"][a:match.start()]) or WORD.search(segment["text"][match.end():b]):
                    return None  # A single token provides no boundary between two words.
                indices.append(index)
        if not indices or indices[-1] + 1 >= len(points):
            return None
        timing = points[indices[0]:indices[-1] + 2]
        if (any(type(t) is not int or not 0 <= t <= 3000 for t in timing)
                or any(a > b for a, b in zip(timing, timing[1:]))
                or timing[0] >= timing[-1]):
            return None
        bounds.append((timing[0] * 160, timing[-1] * 160))
    if any(a[1] > b[0] for a, b in zip(bounds, bounds[1:])):
        return None
    return bounds


def insertion(donor, anchor, start, seam, target_end, end, language):
    """Return one insertion only when both lexical and timing gates agree."""
    if not isinstance(donor, dict):
        return None
    selection = donor.get("language_selection")
    if (donor.get("task") != "transcribe" or not isinstance(selection, dict)
            or selection.get("selected") != language
            or type(selection.get("selected_probability")) not in (int, float)
            or not 0.5 <= selection["selected_probability"] <= 1):
        return None
    at, left, right = anchor
    segments = donor.get("segments")
    if not isinstance(segments, list) or not all(
            isinstance(row, dict) and isinstance(row.get("text"), str) for row in segments):
        return None
    text = "\n".join(row["text"] for row in segments)
    _, tokens = _lexemes(text)
    # Permit one substitution inside a longer left anchor, never at the join.
    tolerance = int(len(left) >= 8)
    left_hits = [i for i in _matches(tokens, left, tolerance) if tokens[i + len(left) - 1] == left[-1]]
    if len(left_hits) != 1:
        return None
    right_hits = []
    for width in range(len(right), 2, -1):
        right_hits = _matches(tokens, right[:width], 0)
        if right_hits:
            break
    if len(right_hits) != 1:
        return None
    first, last = left_hits[0] + len(left), right_hits[0]
    if not 0 < last - first <= 6:
        return None
    if set(tokens[first:last]) & set(right[:3] + left[-3:]):
        return None
    cursor = 0
    for row in segments:
        local, _ = _lexemes(row["text"])
        if cursor <= first - 1 and last < cursor + len(local):
            begin, stop = first - cursor, last - cursor
            try:
                a, b = _frame(row.get("start")) + start, _frame(row.get("end")) + start
                bounds = _word_times(row, local[begin - 1:stop + 1])
            except ValueError:
                return None
            if bounds is None or not start <= a < seam < b <= end:
                return None
            times = [(x + start, y + start) for x, y in bounds]
            if not (a <= times[0][0] and times[-1][1] <= b):
                return None
            if not (max(a, seam - RATE) <= times[1][0] < seam + RATE
                    and times[-2][1] <= min(b, target_end)
                    and times[-1][1] <= b):
                return None
            origin, finish = local[begin].start(), local[stop].start()
            patch = row["text"][origin:finish]
            if len(patch) > 80 or "\n" in patch or not patch.endswith(" "):
                return None
            words = [{"text": m.group(), "start_offset": m.start() - origin,
                      "end_offset": m.end() - origin, "start_frame": x, "end_frame": y}
                     for m, (x, y) in zip(local[begin:stop], times[1:-1])]
            return {"insert_offset": at, "text": patch, "words": words,
                    "donor_start_frame": start, "donor_end_frame": end, "seam_frame": seam,
                    "target_end_frame": target_end, "language": language, "timing_method": TIMING}
        cursor += len(local)
    return None


def overlay(text, edits):
    """Apply insertions to a separate view, preserving canonical source offsets."""
    parts, mapped, cursor, shift = [], [], 0, 0
    for edit in sorted(edits, key=lambda value: value["insert_offset"]):
        at = edit["insert_offset"]
        parts.extend((text[cursor:at], edit["text"]))
        words = [{**word, "start_offset": word["start_offset"] + at + shift,
                  "end_offset": word["end_offset"] + at + shift} for word in edit["words"]]
        mapped.append({**edit, "words": words})
        cursor = at
        shift += len(edit["text"])
    parts.append(text[cursor:])
    return {"version": VERSION, "offset_unit": "unicode_code_point",
            "sample_rate": RATE, "text": "".join(parts), "edits": mapped}


def repair(base, decode_donor):
    """Request bounded DTW donors from a separate decoder, leaving base intact."""
    boundary = base.get("language_boundary_recovery", {})
    independent = base.get("independent_language_boundaries", {})
    edits, donors = [], []
    for start, seam, target_end, end, language in windows(
            independent.get("islands", []), boundary.get("regions", []),
            boundary.get("proposals", [])):
        anchor = anchors(base, seam)
        if anchor is None or edits and edits[-1]["insert_offset"] >= anchor[0]:
            continue
        result = decode_donor(start, end)
        edit = insertion(result, anchor, start, seam, target_end, end, language)
        if edit is not None and edits and edits[-1]["words"][-1]["end_frame"] > edit["words"][0]["start_frame"]:
            edit = None
        donors.append({"start_frame": start, "end_frame": end, "seam_frame": seam,
                       "target_end_frame": target_end, "accepted": edit is not None})
        if edit is not None:
            edits.append(edit)
        if len(donors) == MAX_DONORS:
            break
    if donors:
        return {**overlay(base["text"], edits), "native_donor_calls": len(donors), "donors": donors}
    return None
