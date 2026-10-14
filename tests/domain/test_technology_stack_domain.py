from __future__ import annotations

import pytest

from pybot.domain.services.technology_stack import (
    TechnologyLevel,
    TechnologyStackItem,
    TechnologyStackValidationError,
    normalize_technology_name,
    normalize_technology_stack,
    technology_identity,
)


@pytest.mark.parametrize(
    ("raw_name", "canonical_name"),
    [
        ("докер", "Docker"),
        ("DOCKER", "Docker"),
        ("Питон", "Python"),
        ("js", "JavaScript"),
        ("постгрес", "PostgreSQL"),
        ("ПОСТГРЭСКЬЮЭЛЬ", "PostgreSQL"),
        ("  Rust  ", "Rust"),
    ],
)
def test_normalizes_aliases_case_and_unknown_names(raw_name: str, canonical_name: str) -> None:
    assert normalize_technology_name(raw_name) == canonical_name


def test_technology_identity_matches_aliases_and_case() -> None:
    assert technology_identity("постгрес") == technology_identity("POSTGRESQL")
    assert technology_identity("докер") == technology_identity("Docker")


def test_stack_normalization_deduplicates_aliases_with_latest_level() -> None:
    stack = normalize_technology_stack(
        (
            TechnologyStackItem("докер", TechnologyLevel.KNOW),
            TechnologyStackItem("Docker", TechnologyLevel.LEARNING),
        )
    )

    assert stack == (TechnologyStackItem("Docker", TechnologyLevel.LEARNING),)


@pytest.mark.parametrize(
    "items",
    [
        (),
        (TechnologyStackItem("   ", TechnologyLevel.KNOW),),
        (TechnologyStackItem("x" * 41, TechnologyLevel.KNOW),),
        tuple(TechnologyStackItem(f"Tech {index}", TechnologyLevel.KNOW) for index in range(21)),
    ],
)
def test_rejects_empty_malformed_or_over_limit_stack(items: tuple[TechnologyStackItem, ...]) -> None:
    with pytest.raises(TechnologyStackValidationError):
        normalize_technology_stack(items)
