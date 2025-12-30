from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Tuple


END_PUNCT = {".", "!", "?", "…", ":"}


@dataclass
class Operation:
    op: str
    detail: str
    before: str
    after: str
    confidence: float
    source: str


def _norm(s: str) -> str:
    """Normalize text for comparison: lowercase + collapse whitespace."""
    return re.sub(r"\s+", " ", s.lower()).strip()


def build_alias_map(context_inferred: Dict[str, Any], min_conf: float = 0.6) -> Dict[str, str]:
    """Build alias -> canonical term mapping from glossary (only high-confidence entries)."""
    alias_map: Dict[str, str] = {}
    for g in context_inferred.get("glossary", []) or []:
        conf = float(g.get("confidence", 0.0))
        term = str(g.get("term", "")).strip()
        if not term or conf < min_conf:
            continue
        
        # Map canonical term to itself
        alias_map[_norm(term)] = term
        
        # Map each alias to canonical term
        for alias in g.get("aliases", []) or []:
            alias = str(alias).strip()
            if alias:
                alias_map[_norm(alias)] = term
    
    return alias_map

def build_error_map(context_inferred: Dict[str, Any], min_conf: float = 0.80) -> Dict[str, str]:
    """Build incorrect -> correct mapping from language errors (only high-confidence)."""
    error_map: Dict[str, str] = {}
    for err in context_inferred.get("language_errors", []) or []:
        conf = float(err.get("confidence", 0.0))
        incorrect = str(err.get("incorrect_text", "")).strip()
        correct = str(err.get("correct_form", "")).strip()
        if not incorrect or not correct or conf < min_conf:
            continue
        error_map[_norm(incorrect)] = correct
    return error_map


def apply_fixes(text: str, alias_map: Dict[str, str], error_map: Dict[str, str] = None) -> Tuple[str, List[Operation]]:
    """
    Apply all safe transformations in order:
    1. Glossary normalization (aliases → canonical)
    2. Language error fixes (spelling/grammar)
    3. Remove immediate repetitions
    4. Light punctuation (capitalize + add period)
    
    Returns: (fixed_text, list_of_operations)
    """
    operations: List[Operation] = []
    result = text
    
    if error_map is None:
        error_map = {}

    # Fix 1: Glossary normalization
    for alias in sorted(alias_map.keys(), key=len, reverse=True):
        term = alias_map[alias]
        if not alias:
            continue
        
        # Word-boundary replacement (case-insensitive)
        pattern = re.compile(rf"(?i)\b{re.escape(alias)}\b")
        if pattern.search(result):
            before = result
            result = pattern.sub(term, result)
            if result != before:
                operations.append(Operation(
                    op="glossary_normalization",
                    detail=f"'{alias}' → '{term}'",
                    before=before,
                    after=result,
                    confidence=0.9,
                    source="glossary"
                ))
    
    # Fix 2: Language errors (spelling/grammar)
    for incorrect in sorted(error_map.keys(), key=len, reverse=True):
        correct = error_map[incorrect]
        if not incorrect:
            continue
        
        # Word-boundary replacement (case-insensitive)
        pattern = re.compile(rf"(?i)\b{re.escape(incorrect)}\b")
        if pattern.search(result):
            before = result
            result = pattern.sub(correct, result)
            if result != before:
                operations.append(Operation(
                    op="language_error_fix",
                    detail=f"'{incorrect}' → '{correct}'",
                    before=before,
                    after=result,
                    confidence=0.85,
                    source="llm_error"
                ))
    

    # Fix 3: Remove immediate word repetitions
    before = result
    result = re.sub(r"\b(\w+)\s+\1\b", r"\1", result, flags=re.IGNORECASE)
    if result != before:
        operations.append(Operation(
            op="deduplication",
            detail="Removed immediate repetition",
            before=before,
            after=result,
            confidence=0.9,
            source="rule"
        ))
    
    # Fix 4: Light punctuation
    before = result
    result = result.strip()
    
    # Capitalize first letter
    if result and result[0].isalpha():
        result = result[0].upper() + result[1:]
    
    # Add ending punctuation if missing
    if result and result[-1] not in END_PUNCT:
        result += "."
    
    if result != before:
        operations.append(Operation(
            op="punctuation",
            detail="Capitalization + ending punctuation",
            before=before,
            after=result,
            confidence=0.8,
            source="rule"
        ))
    
    return result, operations

def edit_transcript(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply safe edits to all transcript segments:
    - Normalize glossary terms
    - Fix spelling/grammar errors (high-confidence only)
    - Remove word repetitions
    - Add light punctuation
    
    Preserves: timestamps, speaker labels, metadata
    """
    out_obj = dict(obj)
    messages = obj.get("messages", [])
    ctx = obj.get("context_inferred", {}) or {}
    alias_map = build_alias_map(ctx)
    #print(alias_map)
    error_map = build_error_map(ctx, min_conf=0.70)  # Only fix high-confidence errors
    #print(error_map)
    edited_messages: List[Dict[str, Any]] = []
    segment_reports: List[Dict[str, Any]] = []
    
    for i, m in enumerate(messages):
        original_content = str(m.get("content", ""))
        
        # Apply all fixes
        fixed_content, operations = apply_fixes(original_content, alias_map, error_map)
        
        # Update message with fixed content
        edited_messages.append({**m, "content": fixed_content})
        
        # Record transformation
        segment_reports.append({
            "index": i,
            "start_time": m.get("start_time"),
            "end_time": m.get("end_time"),
            "speaker": m.get("speaker"),
            "changed": original_content != fixed_content,
            "operations": [asdict(op) for op in operations]
        })
    
    out_obj["messages"] = edited_messages
    out_obj.setdefault("transformation_report", {})
    out_obj["transformation_report"]["editor"] = {
        "policy": {
            "glossary_normalization": True,
            "language_error_correction": True,
            "deduplication": True,
            "light_punctuation": True,
            "timestamps_preserved": True,
            "speaker_labels_preserved": True
        },
        "total_segments": len(messages),
        "segments_modified": sum(1 for r in segment_reports if r["changed"]),
        "segment_reports": segment_reports
    }
    
    return out_obj