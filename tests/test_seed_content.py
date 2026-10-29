"""Tests for the ghost's seeded content selection.

The seed (1, 2, or 3) is chosen in code at session start and deterministically
selects which variant of each of the five topics the ghost "knows". These tests
pin that contract so Phase 5 edits to the content don't silently break the shape.
"""

import pytest

from services.seed_content import (
    build_seed_context,
    SEED_CONTENT,
    TOPIC_ORDER,
)


def test_all_topics_have_three_variants():
    for topic in TOPIC_ORDER:
        assert topic in SEED_CONTENT, f"missing topic: {topic}"
        assert set(SEED_CONTENT[topic].keys()) == {1, 2, 3}, f"topic {topic} must have seeds 1,2,3"
        for seed in (1, 2, 3):
            assert SEED_CONTENT[topic][seed].strip(), f"{topic} seed {seed} is empty"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_build_seed_context_contains_every_topics_chosen_variant(seed):
    context = build_seed_context(seed)
    for topic in TOPIC_ORDER:
        # The heading for each topic appears...
        assert topic in context
        # ...and the variant it carries is the one for THIS seed, not another.
        assert SEED_CONTENT[topic][seed] in context


def test_build_seed_context_out_of_range_falls_back_to_one():
    assert build_seed_context(99) == build_seed_context(1)
