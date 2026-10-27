"""Fusion checked against values worked out by hand, not by running the code under test.

Ties are asserted on purpose: the benchmark compares runs, so the order of equal scores has to be
the same every time, and the order is the first appearance across the lists.
"""

import pytest

from ...src.app.functionality.fusion import min_max_fusion


def test_min_max_normalises_each_list_before_weighting_it():
    # 10 -> 1, 0 -> 0, 5 -> 0.5; weight 2 doubles each
    fused = min_max_fusion([[("a", 10), ("b", 0), ("c", 5)]], [2.0])
    assert fused == [
        ("a", pytest.approx(2.0)),
        ("c", pytest.approx(1.0)),
        ("b", pytest.approx(0.0)),
    ]


def test_min_max_sums_weighted_scores_across_lists_and_ties_keep_first_appearance():
    # list 1: a -> 1, b -> 0 (weight 1)
    # list 2: b -> 1, c -> 0, a -> 0.5 (weight 2)
    # a = 1 + 2*0.5 = 2, b = 0 + 2*1 = 2: a and b tie, and a appears first
    # c is only in list 2, so it scores 0
    fused = min_max_fusion(
        [[("a", 3), ("b", 1)], [("b", 9), ("c", 3), ("a", 6)]], [1.0, 2.0]
    )
    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]
    assert fused[0][1] == pytest.approx(2.0)
    assert fused[1][1] == pytest.approx(2.0)
    assert fused[2][1] == pytest.approx(0.0)


def test_min_max_counts_a_repeated_id_at_its_first_occurrence_only():
    # first a is 0, b is 5, so b normalises to 1 and a to 0. the later a=10 is ignored; had it
    # been used, a would have won
    fused = min_max_fusion([[("a", 0), ("b", 5), ("a", 10)]], [1.0])
    assert fused == [("b", pytest.approx(1.0)), ("a", pytest.approx(0.0))]


def test_an_empty_list_contributes_nothing_to_min_max():
    fused = min_max_fusion([[("a", 2), ("b", 1)], []], [1.0, 3.0])
    assert fused == [("a", pytest.approx(1.0)), ("b", pytest.approx(0.0))]


def test_min_max_with_no_ids_returns_nothing():
    assert min_max_fusion([], []) == []
    assert min_max_fusion([[]], [1.0]) == []


def test_a_list_of_equal_scores_normalises_every_entry_to_one():
    # no spread to normalise against, so each entry is 1.0 * weight 0.5, tied in input order
    fused = min_max_fusion([[("a", 4), ("b", 4), ("c", 4)]], [0.5])
    assert fused == [
        ("a", pytest.approx(0.5)),
        ("b", pytest.approx(0.5)),
        ("c", pytest.approx(0.5)),
    ]


def test_min_max_raises_when_the_weights_do_not_match_the_lists():
    with pytest.raises(ValueError):
        min_max_fusion([[("a", 1.0)], [("b", 2.0)]], [1.0])
