"""Benchmark runner checked without a stack or network: evaluation, report, env handling and ids."""

import os

import pytest

from ..bench import run


def test_evaluate_means_and_empty_shares_match_hand_computed_values():
    qrels = {"q1": {"a": 1}, "q2": {"b": 1}}
    ranked = {"q1": ["a", "x"], "q2": ["x", "y"], "u1": [], "u2": ["z"]}
    latencies = {"q1": 10.0, "q2": 20.0, "u1": 30.0, "u2": 40.0}

    result = run.evaluate(ranked, qrels, ["u1", "u2"], latencies)

    # q1 hits at rank 1, q2 misses entirely
    assert result["hit@5"] == pytest.approx(0.5)
    assert result["recall@5"] == pytest.approx(0.5)
    assert result["ndcg@10"] == pytest.approx(0.5)
    assert result["mrr@10"] == pytest.approx(0.5)
    assert result["per_query"]["mrr@10"] == [1.0, 0.0]
    assert result["empty_unanswerable"] == pytest.approx(0.5)
    assert result["empty_answerable"] == 0.0
    assert result["p50_ms"] == pytest.approx(25.0)


def test_markdown_report_lists_every_variant_and_a_p_value_for_non_baseline_rows():
    def summary(ndcg: list[float]) -> dict:
        return {
            "hit@5": 1.0,
            "recall@5": 1.0,
            "ndcg@10": sum(ndcg) / len(ndcg),
            "mrr@10": 1.0,
            "per_query": {"ndcg@10": ndcg},
            "empty_unanswerable": 0.25,
            "empty_answerable": 0.0,
            "p50_ms": 12.4,
            "p95_ms": 98.6,
        }

    results = {
        "B0": summary([0.2, 0.4, 0.6]),
        "H-rrf60": summary([0.5, 0.7, 0.9]),
    }

    report = run.markdown_report("scifact", results)

    assert "# Retrieval benchmark: scifact" in report
    assert "| B0 |" in report
    assert "| H-rrf60 |" in report
    # the baseline row has no delta, the other row has one with a p-value
    baseline_row = next(
        line for line in report.splitlines() if line.startswith("| B0 |")
    )
    variant_row = next(
        line for line in report.splitlines() if line.startswith("| H-rrf60 |")
    )
    assert "—" in baseline_row
    assert "(p=" in variant_row
    assert "25.0%" in variant_row
    assert "12" in variant_row


def test_variant_env_sets_overrides_clears_other_variant_keys_and_restores_exactly(
    monkeypatch,
):
    monkeypatch.setenv("SHABTI_RRF_K", "99")
    monkeypatch.setenv("SHABTI_HYBRID_DENSE_WEIGHT", "0.9")
    monkeypatch.delenv("SHABTI_RERANK_MODEL", raising=False)
    monkeypatch.delenv("SHABTI_RETRIEVAL_MODE", raising=False)

    overrides = run.VARIANTS["H-rrf20"]
    with run.variant_env(overrides):
        assert os.environ["SHABTI_RETRIEVAL_MODE"] == "hybrid"
        assert os.environ["SHABTI_HYBRID_FUSION"] == "rrf"
        assert os.environ["SHABTI_RRF_K"] == "20"
        # keys another variant uses but this one does not are cleared
        assert "SHABTI_HYBRID_DENSE_WEIGHT" not in os.environ
        assert "SHABTI_RERANK_MODEL" not in os.environ

    # restored to exactly what it was before: the set value, and the unset ones still unset
    assert os.environ["SHABTI_RRF_K"] == "99"
    assert os.environ["SHABTI_HYBRID_DENSE_WEIGHT"] == "0.9"
    assert "SHABTI_RERANK_MODEL" not in os.environ
    assert "SHABTI_RETRIEVAL_MODE" not in os.environ


def test_corpus_ids_takes_the_stem_of_each_source_filename_in_order():
    context = {
        "context": "ignored",
        "sources": [
            {
                "text": "a",
                "page_metadata": {},
                "doc_metadata": {"filename": "12-3.txt"},
            },
            {
                "text": "b",
                "page_metadata": {},
                "doc_metadata": {"filename": "MED.7.txt"},
            },
        ],
    }
    assert run.corpus_ids(context) == ["12-3", "MED.7"]


def test_parser_rejects_unknown_dataset_before_touching_the_network():
    with pytest.raises(SystemExit):
        run.main(["not-a-dataset"])


def test_rrf_puts_a_chunk_found_by_both_legs_first():
    # h2 is second in both legs, which beats h1 and h3 each found by only one leg at rank 1
    legs = {
        "q": {
            "dense": [("h1", "A", 0.9), ("h2", "B", 0.8)],
            "keyword": [("h3", "C", 5.0), ("h2", "B", 4.0)],
        }
    }

    runs = run.sweep_runs(legs)

    assert runs["S-rrf60"]["q"][0] == "B"


def test_min_max_at_dense_weight_90_follows_the_dense_order():
    # keyword favours C, but at a 0.9 dense weight the dense spread dominates the order
    legs = {
        "q": {
            "dense": [("d1", "A", 0.9), ("d2", "B", 0.7), ("d3", "C", 0.5)],
            "keyword": [("d3", "C", 9.0), ("d2", "B", 1.0)],
        }
    }

    runs = run.sweep_runs(legs)

    assert runs["S-mm90"]["q"] == ["A", "B", "C"]


def test_bm25_run_is_the_keyword_leg_in_its_own_order():
    legs = {
        "q": {
            "dense": [("d1", "A", 0.9)],
            "keyword": [("k2", "Y", 7.0), ("k1", "X", 3.0)],
        }
    }

    runs = run.sweep_runs(legs)

    assert runs["S-bm25"]["q"] == ["Y", "X"]


def test_empty_dense_leg_empties_fused_runs_but_not_bm25():
    legs = {
        "q": {
            "dense": [],
            "keyword": [("k1", "X", 3.0)],
        }
    }

    runs = run.sweep_runs(legs)

    assert runs["S-bm25"]["q"] == ["X"]
    assert runs["S-dense"]["q"] == []
    assert runs["S-rrf60"]["q"] == []
    assert runs["S-mm50"]["q"] == []


def test_candidate_ceilings_are_mean_recall_over_answerable_queries_at_full_depth():
    legs = {
        # relevant A, B, C; dense finds A only, keyword finds B and A
        "q1": {
            "dense": [("h1", "A", 0.9), ("h2", "X", 0.8)],
            "keyword": [("h3", "B", 5.0), ("h4", "A", 4.0)],
        },
        # relevant Z; neither leg finds it
        "q2": {
            "dense": [("h5", "Y", 0.7)],
            "keyword": [],
        },
        # unanswerable: must not count towards the means
        "u1": {
            "dense": [("h9", "C", 0.6)],
            "keyword": [],
        },
    }
    qrels = {"q1": {"A": 1, "B": 1, "C": 1}, "q2": {"Z": 1}}

    ceilings = run.candidate_ceilings(legs, qrels)

    # q1: dense 1/3, keyword 2/3, union 2/3 (A, X, B distinct); q2: all 0
    assert ceilings["dense"] == pytest.approx((1 / 3 + 0) / 2)
    assert ceilings["keyword"] == pytest.approx((2 / 3 + 0) / 2)
    assert ceilings["union"] == pytest.approx((2 / 3 + 0) / 2)
