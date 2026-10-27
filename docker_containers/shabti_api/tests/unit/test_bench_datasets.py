"""Benchmark datasets parsed from a tiny BEIR-shaped fixture, with no network access.

The holdout invariants are checked across several seeds, so they do not depend on which query one
seed happens to pick.
"""

import json
import re

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


class FakeRows:
    """The dataset viewer's rows endpoint over in-memory tables, paged as the real one is."""

    def __init__(self, tables, throttled=0, status=429):
        self.tables = tables
        self.throttled = throttled
        self.status = status
        self.calls = []

    def __call__(self, url, params, timeout):
        assert url == datasets.ROWS_URL
        assert params["dataset"] == datasets.NANO_REPO
        self.calls.append((params["config"], params["split"], params["offset"]))
        throttle = self.throttled > 0
        self.throttled -= 1
        status = self.status
        rows = self.tables[(params["config"], params["split"])]
        page = rows[params["offset"] : params["offset"] + params["length"]]
        payload = {"rows": [{"row": row} for row in page], "num_rows_total": len(rows)}

        class Response:
            status_code = status if throttle else 200
            headers = {"Retry-After": "3"} if throttle and status == 429 else {}

            def raise_for_status(self):
                if throttle:
                    raise datasets.requests.HTTPError(str(status))

            def json(self):
                return payload

        return Response()


def test_download_nano_pages_through_the_viewer_into_a_parseable_beir_layout(
    tmp_path, monkeypatch
):
    corpus = [{"_id": f"d{i}", "text": f"texte {i}"} for i in range(5)]
    fake = FakeRows(
        {
            ("NanoSciFact_fr", "corpus"): corpus,
            ("NanoSciFact_fr", "queries"): [{"_id": "q1", "text": "question ?"}],
            ("NanoSciFact_fr_qrels", "qrels"): [
                {"query-id": "q1", "corpus-id": "d3"},
                {"query-id": "q1", "corpus-id": "d4"},
            ],
        }
    )
    monkeypatch.setattr(datasets.requests, "get", fake)
    monkeypatch.setattr(datasets, "ROWS_PAGE", 2)

    dataset = datasets.load("nano-scifact-fr", tmp_path)

    # five corpus rows at two a page is three requests
    assert [c for c in fake.calls if c[1] == "corpus"] == [
        ("NanoSciFact_fr", "corpus", 0),
        ("NanoSciFact_fr", "corpus", 2),
        ("NanoSciFact_fr", "corpus", 4),
    ]
    assert dataset.corpus["d2"] == Doc(title="", text="texte 2")
    assert len(dataset.corpus) == 5
    assert dataset.queries == {"q1": "question ?"}
    # the judgements are ungraded, so every listed pair is relevant at grade 1
    assert dataset.qrels == {"q1": {"d3": 1, "d4": 1}}


def test_a_failed_nano_download_leaves_nothing_that_looks_cached(tmp_path, monkeypatch):
    fake = FakeRows({("NanoNFCorpus_de", "corpus"): [{"_id": "d1", "text": "x"}]})
    monkeypatch.setattr(datasets.requests, "get", fake)

    # the queries table is missing, so the download fails part way through
    with pytest.raises(KeyError):
        datasets.download("nano-nfcorpus-de", tmp_path)
    assert not (tmp_path / "nano-nfcorpus-de" / "corpus.jsonl").exists()


def test_a_throttled_page_is_retried_after_the_wait_the_viewer_asks_for(
    tmp_path, monkeypatch
):
    fake = FakeRows(
        {
            ("NanoSciFact_en", "corpus"): [{"_id": "d1", "text": "x"}],
            ("NanoSciFact_en", "queries"): [{"_id": "q1", "text": "q"}],
            ("NanoSciFact_en_qrels", "qrels"): [{"query-id": "q1", "corpus-id": "d1"}],
        },
        throttled=2,
    )
    sleeps = []
    monkeypatch.setattr(datasets.requests, "get", fake)
    monkeypatch.setattr(datasets.time, "sleep", sleeps.append)

    dataset = datasets.load("nano-scifact-en", tmp_path)

    assert sleeps == [3.0, 3.0]
    assert dataset.qrels == {"q1": {"d1": 1}}


def test_a_bad_gateway_is_retried_with_a_growing_wait(tmp_path, monkeypatch):
    fake = FakeRows(
        {
            ("NanoSciFact_en", "corpus"): [{"_id": "d1", "text": "x"}],
            ("NanoSciFact_en", "queries"): [{"_id": "q1", "text": "q"}],
            ("NanoSciFact_en_qrels", "qrels"): [{"query-id": "q1", "corpus-id": "d1"}],
        },
        throttled=2,
        status=502,
    )
    sleeps = []
    monkeypatch.setattr(datasets.requests, "get", fake)
    monkeypatch.setattr(datasets.time, "sleep", sleeps.append)

    dataset = datasets.load("nano-scifact-en", tmp_path)

    assert sleeps == [1.0, 2.0]
    assert dataset.qrels == {"q1": {"d1": 1}}


def test_nano_sets_hold_nothing_out_by_default_and_beir_sets_hold_out_50():
    assert datasets.default_holdout("nano-nfcorpus-ja") == 0
    assert datasets.default_holdout("scifact") == 50


def test_default_holdout_is_zero_for_nano_and_custom_sets():
    assert datasets.default_holdout("scifact") == 50
    assert datasets.default_holdout("nano-scifact-en") == 0
    assert datasets.default_holdout("/custom/mine") == 0


def test_check_layout_accepts_a_folder_with_the_three_beir_files(tmp_path):
    datasets.check_layout(write_beir(tmp_path / "mine", ["q1\td1\t1"]))


def test_check_layout_names_the_missing_qrels_file(tmp_path):
    root = write_beir(tmp_path / "mine", ["q1\td1\t1"])
    (root / "qrels" / "test.tsv").unlink()
    with pytest.raises(datasets.DatasetLayoutError, match="qrels/test.tsv"):
        datasets.check_layout(root)


def test_check_layout_rejects_a_path_that_is_not_a_folder(tmp_path):
    with pytest.raises(datasets.DatasetLayoutError, match="not a folder"):
        datasets.check_layout(tmp_path / "missing")


def test_corpus_fingerprint_is_stable_eight_hex_characters_and_follows_the_corpus(
    tmp_path,
):
    root = write_beir(tmp_path / "mine", ["q1\td1\t1"])
    first = datasets.corpus_fingerprint(root)
    assert first == datasets.corpus_fingerprint(root)
    assert re.fullmatch(r"[0-9a-f]{8}", first)

    (root / "corpus.jsonl").write_text(
        '{"_id": "d9", "text": "new"}\n', encoding="utf-8"
    )
    assert datasets.corpus_fingerprint(root) != first


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
