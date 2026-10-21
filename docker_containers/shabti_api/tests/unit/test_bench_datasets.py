"""Benchmark datasets parsed from a tiny BEIR-shaped fixture, with no network access.

The holdout invariants are checked across several seeds, so they do not depend on which query one
seed happens to pick.
"""

import json

import pytest

from ..bench import datasets
from ..bench.datasets import Dataset, Doc


def write_beir(root, qrels_rows, queries=("q1", "q2", "q3", "q4")):
    root.mkdir(parents=True, exist_ok=True)
    corpus = [
        {"_id": "d1", "title": "First", "text": "alpha"},
        {"_id": "d2", "title": "Second", "text": "beta"},
        {"_id": "d3", "title": "Third", "text": "gamma"},
    ]
    (root / "corpus.jsonl").write_text(
        "\n".join(json.dumps(d) for d in corpus), encoding="utf-8"
    )
    query_rows = [{"_id": q, "text": f"question {q}"} for q in queries]
    (root / "queries.jsonl").write_text(
        "\n".join(json.dumps(q) for q in query_rows), encoding="utf-8"
    )
    (root / "qrels").mkdir(exist_ok=True)
    lines = ["query-id\tcorpus-id\tscore", *qrels_rows]
    (root / "qrels" / "test.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def make_dataset(qrels):
    return Dataset(name="t", corpus={}, queries={}, qrels=qrels)


def test_parse_drops_zero_score_judgements_and_limits_queries_to_the_test_split(
    tmp_path,
):
    root = write_beir(
        tmp_path / "scifact",
        ["q1\td1\t1", "q1\td2\t0", "q2\td3\t2"],
        queries=("q1", "q2", "q3"),
    )
    dataset = datasets.parse("scifact", root)
    assert dataset.name == "scifact"
    assert dataset.qrels == {"q1": {"d1": 1}, "q2": {"d3": 2}}
    assert dataset.queries == {"q1": "question q1", "q2": "question q2"}


def test_parse_reads_titles_and_texts_from_the_corpus(tmp_path):
    root = write_beir(tmp_path / "scifact", ["q1\td1\t1"])
    dataset = datasets.parse("scifact", root)
    assert dataset.corpus["d2"] == Doc(title="Second", text="beta")
    assert len(dataset.corpus) == 3


def test_download_skips_the_network_when_the_dataset_is_already_present(
    tmp_path, monkeypatch
):
    write_beir(tmp_path / "nfcorpus", ["q1\td1\t1"])

    def fail(*args, **kwargs):
        raise AssertionError("requests.get must not be called for a cached dataset")

    monkeypatch.setattr(datasets.requests, "get", fail)
    assert datasets.download("nfcorpus", tmp_path) == tmp_path / "nfcorpus"


def test_download_rejects_an_unknown_dataset_name(tmp_path):
    with pytest.raises(ValueError):
        datasets.download("msmarco", tmp_path)


def test_holdout_is_deterministic_for_the_same_inputs():
    dataset = make_dataset({f"q{i}": {f"d{i}": 1} for i in range(20)})
    assert datasets.holdout(dataset, size=5, seed=3) == datasets.holdout(
        dataset, size=5, seed=3
    )


def test_holdout_excludes_every_relevant_doc_of_the_unanswerable_queries():
    qrels = {"q1": {"d1": 1, "d2": 2}, "q2": {"d3": 1}, "q3": {"d4": 1}}
    split = datasets.holdout(make_dataset(qrels), size=1, seed=0)
    picked = split.unanswerable[0]
    assert split.excluded_docs == set(qrels[picked])


def test_holdout_drops_queries_that_share_a_gold_doc_with_the_holdout():
    # q1 and q2 both have d1 as gold, q3 is independent of them
    qrels = {"q1": {"d1": 1}, "q2": {"d1": 1}, "q3": {"d3": 1}}
    for seed in range(10):
        split = datasets.holdout(make_dataset(qrels), size=1, seed=seed)
        (picked,) = split.unanswerable
        if picked in ("q1", "q2"):
            assert set(split.answerable) == {"q3"}
        else:
            assert set(split.answerable) == {"q1", "q2"}


def test_answerable_queries_never_touch_an_excluded_doc():
    qrels = {f"q{i}": {f"d{i % 4}": 1, f"d{(i + 1) % 7}": 2} for i in range(12)}
    for seed in range(10):
        split = datasets.holdout(make_dataset(qrels), size=4, seed=seed)
        assert len(split.unanswerable) == 4
        for rels in split.answerable.values():
            assert split.excluded_docs.isdisjoint(rels)
        assert not set(split.answerable) & set(split.unanswerable)


def test_holdout_takes_every_query_when_fewer_than_size_are_available():
    qrels = {"q1": {"d1": 1}, "q2": {"d2": 1}}
    split = datasets.holdout(make_dataset(qrels), size=50, seed=0)
    assert split.unanswerable == ["q1", "q2"]
    assert split.answerable == {}
