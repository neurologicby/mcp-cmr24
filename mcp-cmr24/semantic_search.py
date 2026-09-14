"""Deterministic Russian-first reference matcher with ambiguity reporting."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

DEFAULT_SYNONYMS = {
    "реф": "рефрижератор",
    "безнал": "безналичный расчет",
    "нал": "наличные",
    "бок": "боковая",
    "верх": "верхняя",
    "зад": "задняя",
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


@dataclass(frozen=True)
class Candidate:
    id: Any
    value: str
    score: float


@dataclass(frozen=True)
class MatchResult:
    matched: Candidate | None
    candidates: tuple[Candidate, ...] = ()
    ambiguous: bool = False


def _items(resource_data: Any) -> list[tuple[Any, str]]:
    if isinstance(resource_data, dict):
        return [(key, str(value)) for key, value in resource_data.items()]
    if isinstance(resource_data, list) and all(
        isinstance(item, str) for item in resource_data
    ):
        return [(None, item) for item in resource_data]
    if isinstance(resource_data, list) and all(
        isinstance(item, dict) and "id" in item for item in resource_data
    ):
        result = []
        for item in resource_data:
            value = item.get("text") or item.get("name")
            if value:
                result.append((item["id"], str(value)))
        return result
    raise ValueError("Неподдерживаемый формат справочника")


def match_reference(
    resource_data: Any,
    query: str,
    *,
    threshold: float = 0.84,
    margin: float = 0.08,
    synonyms: dict[str, str] | None = None,
    limit: int = 3,
) -> MatchResult:
    items = _items(resource_data)
    if not items:
        return MatchResult(None)
    normalized_query = normalize_text(query)
    if not normalized_query:
        return MatchResult(None)
    synonym_map = {
        normalize_text(k): normalize_text(v)
        for k, v in (synonyms or DEFAULT_SYNONYMS).items()
    }
    normalized_query = synonym_map.get(normalized_query, normalized_query)

    scored: list[Candidate] = []
    for item_id, value in items:
        normalized_value = normalize_text(value)
        if normalized_value == normalized_query:
            return MatchResult(Candidate(item_id, value, 1.0))
        score = SequenceMatcher(None, normalized_query, normalized_value).ratio()
        if normalized_query in normalized_value or normalized_value in normalized_query:
            score = max(
                score,
                min(len(normalized_query), len(normalized_value))
                / max(len(normalized_query), len(normalized_value)),
            )
        scored.append(Candidate(item_id, value, score))

    scored.sort(
        key=lambda candidate: (-candidate.score, normalize_text(candidate.value))
    )
    best = scored[0]
    suggestions = tuple(scored[:limit])
    if best.score < threshold:
        return MatchResult(None, suggestions)
    second_score = scored[1].score if len(scored) > 1 else 0.0
    if best.score - second_score < margin:
        return MatchResult(None, suggestions, ambiguous=True)
    return MatchResult(best, suggestions)


def semantic_search(
    resource_data: Any,
    query: str,
    resource_key: str | None = None,
    threshold: float = 0.84,
) -> tuple[Any, str | None]:
    """Compatibility wrapper for the former tuple-based matcher."""
    del resource_key
    result = match_reference(resource_data, query, threshold=threshold)
    if result.matched is None:
        return None, None
    return result.matched.id, result.matched.value
