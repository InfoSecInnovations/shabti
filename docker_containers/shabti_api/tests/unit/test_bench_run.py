"""Benchmark runner checked without a stack or network: evaluation, report and ids."""

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


def test_nothing_held_out_reports_no_empty_share_rather_than_zero():
    result = run.evaluate({"q1": ["a"]}, {"q1": {"a": 1}}, [], {"q1": 10.0})
    assert result["empty_unanswerable"] is None

    report = run.markdown_report("nano-scifact-fr", result)
    row = report.splitlines()[-1]
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    # the unanswerable column is a dash, not a percentage
    assert cells[4] == "—"
    assert cells[5] == "0.0%"


def test_markdown_report_has_heading_and_one_row_of_metrics():
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

    report = run.markdown_report("scifact", summary([0.2, 0.4, 0.6]))

    lines = report.splitlines()
    assert lines[0] == "# Retrieval benchmark: scifact"
    assert "Variant" not in report
    assert lines[-1] == ("| 1.000 | 1.000 | 0.400 | 1.000 | 25.0% | 0.0% | 12 | 99 |")


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
