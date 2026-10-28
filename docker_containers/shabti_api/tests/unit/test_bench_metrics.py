"""Benchmark metrics checked against values worked out by hand, with no network access."""

import math

import pytest

from ..bench import metrics


def test_dedupe_keeps_the_first_occurrence_of_each_id_in_order():
    assert metrics.dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_hit_at_k_counts_a_relevant_chunk_in_the_raw_window_even_when_its_doc_repeats():
    relevant = {"a": 1}
    assert metrics.hit_at_k(["x", "a", "a"], relevant, 1) == 0.0
    assert metrics.hit_at_k(["a", "a"], relevant, 1) == 1.0


def test_recall_at_k_divides_by_all_relevant_docs_using_the_deduped_window():
    relevant = {"a": 1, "b": 2, "c": 1}
    ranked = ["a", "x", "a", "b"]
    # deduped list is a, x, b, so the top 2 holds one of the three relevant docs
    assert metrics.recall_at_k(ranked, relevant, 2) == pytest.approx(1 / 3)
    assert metrics.recall_at_k(ranked, relevant, 3) == pytest.approx(2 / 3)
    assert metrics.recall_at_k(ranked, relevant, 10) == pytest.approx(2 / 3)


def test_recall_at_k_is_zero_when_no_doc_is_relevant():
    assert metrics.recall_at_k(["a", "b"], {"a": 0}, 2) == 0.0


def test_ndcg_at_k_weights_grade_two_docs_above_grade_one_docs():
    relevant = {"a": 2, "b": 1}
    ranked = ["x", "a", "a", "b"]
    # deduped list is x, a, b: gains are 0, 3, 1 at positions 1, 2, 3
    # dcg = 3/log2(3) + 1/log2(4); ideal gains are 3, 1 so idcg = 3/log2(2) + 1/log2(3)
    dcg = 3 / math.log2(3) + 1 / math.log2(4)
    idcg = 3 / math.log2(2) + 1 / math.log2(3)
    assert metrics.ndcg_at_k(ranked, relevant, 3) == pytest.approx(dcg / idcg)
    assert metrics.ndcg_at_k(ranked, relevant, 3) == pytest.approx(0.6590, abs=1e-4)


def test_ndcg_at_k_cuts_the_ideal_ranking_at_k_as_well():
    relevant = {"a": 2, "b": 1}
    # the top 1 holds no relevant doc, so dcg is 0 whatever the ideal ranking is
    assert metrics.ndcg_at_k(["x", "a", "b"], relevant, 1) == 0.0


def test_ndcg_at_k_is_zero_when_no_doc_is_relevant():
    assert metrics.ndcg_at_k(["a", "b"], {"a": 0}, 2) == 0.0


def test_mrr_at_k_uses_the_rank_of_the_first_relevant_doc_after_dedupe():
    relevant = {"b": 1}
    # the repeated "x" must not push "b" down: deduped list is x, b, so b is at rank 2 not 3
    assert metrics.mrr_at_k(["x", "x", "b"], relevant, 3) == pytest.approx(0.5)
    assert metrics.mrr_at_k(["x", "a", "a", "b"], {"a": 1, "b": 1}, 3) == pytest.approx(
        0.5
    )


def test_mrr_at_k_is_zero_when_the_first_relevant_doc_is_outside_the_window():
    relevant = {"a": 1}
    assert metrics.mrr_at_k(["x", "y", "a"], relevant, 2) == 0.0


def test_mean_of_values_and_zero_for_empty_input():
    assert metrics.mean([1, 2, 3, 4]) == pytest.approx(2.5)
    assert metrics.mean([]) == 0.0


def test_percentile_interpolates_linearly_between_closest_ranks():
    values = [4, 1, 3, 2]
    # sorted is 1, 2, 3, 4; the p-th percentile sits at position p/100 * 3
    assert metrics.percentile(values, 0) == pytest.approx(1)
    assert metrics.percentile(values, 25) == pytest.approx(1.75)
    assert metrics.percentile(values, 50) == pytest.approx(2.5)
    assert metrics.percentile(values, 90) == pytest.approx(3.7)
    assert metrics.percentile(values, 100) == pytest.approx(4)


def test_percentile_of_empty_input_is_zero():
    assert metrics.percentile([], 50) == 0.0
