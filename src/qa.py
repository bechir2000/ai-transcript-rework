from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Tuple


@dataclass
class InvalidSegment:
    index: int
    reason: str


@dataclass
class Gap:
    prev_index: int
    next_index: int
    prev_end: float
    next_start: float
    gap_s: float


@dataclass
class Overlap:
    prev_index: int
    next_index: int
    prev_end: float
    next_start: float
    overlap_s: float


@dataclass
class LongSegment:
    index: int
    start_time: float
    end_time: float
    duration_s: float


def qa_check_transcript(
    obj: Dict[str, Any],
    *,
    gap_threshold_s: float = 2.0,
    long_segment_threshold_s: float = 25.0,
    sort_for_analysis: bool = True,
) -> Tuple[Dict[str, Any], List[InvalidSegment]]:
    msgs = obj.get("messages", [])
    if not isinstance(msgs, list) or not msgs:
        report = {
            "ok": False,
            "total_segments": 0,
            "valid_segments": 0,
            "invalid_segments_count": 0,
            "invalid_segments": [],
            "omission_suspects": [],
            "overlaps": [],
            "long_segments": [],
            "warnings": [],
            "errors": ["messages missing/empty"],
            "thresholds": {
                "gap_threshold_s": gap_threshold_s,
                "long_segment_threshold_s": long_segment_threshold_s,
            },
        }
        return report, []

    invalid: List[InvalidSegment] = []
    valid: List[Tuple[int, float, float]] = []  # (index, start, end)

    for i, m in enumerate(msgs):
        # Segment must be an object/dict.
        if not isinstance(m, dict):
            invalid.append(InvalidSegment(i, "not an object"))
            continue

        # Timestamps must exist and be convertible to float.
        try:
            st = float(m["start_time"])
            et = float(m["end_time"])
        except (KeyError, TypeError, ValueError):
            invalid.append(InvalidSegment(i, "missing/invalid timestamps"))
            continue

        # speaker/content must be non-empty strings.
        speaker = m.get("speaker")
        content = m.get("content")
        if not isinstance(speaker, str) or not speaker.strip():
            invalid.append(InvalidSegment(i, "missing/invalid speaker"))
            continue
        if not isinstance(content, str) or not content.strip():
            invalid.append(InvalidSegment(i, "missing/invalid content"))
            continue

        # Timestamps must be coherent: start >= 0 and end strictly after start.
        if st < 0 or et <= st:
            invalid.append(InvalidSegment(i, "invalid timestamps"))
            continue

        # Keep only what we need for timeline analysis: index + numeric start/end.
        valid.append((i, st, et))

    # Timeline checks: overlaps and large gaps between consecutive segments.
    overlaps: List[Overlap] = []
    gaps: List[Gap] = []

    analyzed = valid
    reordered_for_analysis = False
    if sort_for_analysis:
        analyzed = sorted(valid, key=lambda x: (x[1], x[2]))
        reordered_for_analysis = analyzed != valid

    # Compare adjacent segments in the analyzed order.
    for (pi, pst, pet), (ni, nst, net) in zip(analyzed, analyzed[1:]):
        # Overlap: next starts before previous ends.
        if nst < pet:
            overlaps.append(Overlap(pi, ni, pet, nst, round(pet - nst, 3)))
        else:
            # Gap: time between previous end and next start.
            g = nst - pet
            if g >= gap_threshold_s:
                gaps.append(Gap(pi, ni, pet, nst, round(g, 3)))

    # Long segment check: unusually long single segments.
    long_segments: List[LongSegment] = []
    for i, st, et in valid:
        dur = et - st
        if dur >= long_segment_threshold_s:
            long_segments.append(LongSegment(i, st, et, round(dur, 3)))

    warnings: List[str] = []
    errors: List[str] = []

    if invalid:
        warnings.append(f"{len(invalid)} invalid segments detected (not removed)")
    if reordered_for_analysis:
        warnings.append("segments were out of chronological order; sorted for analysis")

    if overlaps:
        warnings.append(f"{len(overlaps)} overlaps detected")
    if gaps:
        warnings.append(f"{len(gaps)} large gaps detected (>= {gap_threshold_s}s)")

    ok = len(errors) == 0  # keep it simple;

    report = {
        "ok": ok,
        "total_segments": len(msgs),
        "valid_segments": len(valid),
        "invalid_segments_count": len(invalid),
        "invalid_segments": [asdict(x) for x in invalid],
        "omission_suspects": [asdict(x) for x in gaps],  # large gaps
        "overlaps": [asdict(x) for x in overlaps],
        "long_segments": [asdict(x) for x in long_segments],
        "warnings": warnings,
        "errors": errors,
        "thresholds": {
            "gap_threshold_s": gap_threshold_s,
            "long_segment_threshold_s": long_segment_threshold_s,
        },
        "sorted_for_analysis": bool(sort_for_analysis),
    }
    return report, invalid