"""Dedup on (portal, tender_id) and on normalised title+buyer hash."""

from __future__ import annotations

from collections.abc import Iterable

from tender_bhai.models import Candidate, dedup_hash


def dedup_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Remove duplicates inside one run (same portal+id, or same title+buyer)."""
    seen_ids: set[tuple[str, str]] = set()
    seen_hashes: set[str] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        key = (candidate.portal, candidate.tender_id)
        digest = dedup_hash(candidate.title, candidate.buyer_org)
        if key in seen_ids or digest in seen_hashes:
            continue
        seen_ids.add(key)
        seen_hashes.add(digest)
        unique.append(candidate)
    return unique


def filter_known(
    candidates: Iterable[Candidate],
    known_ids: set[tuple[str, str]],
    known_hashes: set[str],
) -> list[Candidate]:
    """Drop candidates already present in the database."""
    fresh: list[Candidate] = []
    for candidate in candidates:
        if (candidate.portal, candidate.tender_id) in known_ids:
            continue
        if dedup_hash(candidate.title, candidate.buyer_org) in known_hashes:
            continue
        fresh.append(candidate)
    return fresh


def passes_value_floor(value: float | None, minimum: float) -> bool:
    """Unknown values pass (we mark them 'value not stated'); known values must reach the floor."""
    return value is None or value >= minimum
