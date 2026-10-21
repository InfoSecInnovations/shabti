"""BEIR SciFact and NFCorpus, plus a holdout of queries left unanswerable by construction.

The holdout's relevant docs are kept out of the ingested corpus, so those queries have no
gold answer in the index and any answer the system gives to them is a hallucination.
"""

from __future__ import annotations

import csv
import json
import random
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

DATA_DIR = Path(__file__).parent / "data"
NAMES = ("scifact", "nfcorpus")
URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"


@dataclass
class Doc:
    title: str
    text: str


@dataclass
class Dataset:
    name: str
    corpus: dict[str, Doc]
    queries: dict[str, str]
    qrels: dict[str, dict[str, int]]


@dataclass
class Split:
    answerable: dict[str, dict[str, int]]
    unanswerable: list[str]
    excluded_docs: set[str]


def download(name: str, data_dir: Path = DATA_DIR) -> Path:
    if name not in NAMES:
        raise ValueError(f"unknown dataset {name!r}, expected one of {NAMES}")
    target = data_dir / name
    # the corpus file is the marker: a half-extracted directory without it is fetched again
    if (target / "corpus.jsonl").is_file():
        return target

    data_dir.mkdir(parents=True, exist_ok=True)
    # stream to disk so the zip is never held in memory
    with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
        archive = Path(tmp) / f"{name}.zip"
        with requests.get(URL.format(name=name), stream=True, timeout=120) as response:
            response.raise_for_status()
            with archive.open("wb") as out:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    out.write(chunk)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(data_dir)
    return target


def _jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def parse(name: str, path: Path) -> Dataset:
    corpus = {
        row["_id"]: Doc(title=row.get("title", ""), text=row.get("text", ""))
        for row in _jsonl(path / "corpus.jsonl")
    }
    all_queries = {row["_id"]: row["text"] for row in _jsonl(path / "queries.jsonl")}

    # only the test split has graded judgements, so queries outside qrels are not evaluated
    qrels: dict[str, dict[str, int]] = {}
    with (path / "qrels" / "test.tsv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            score = int(row["score"])
            # a grade of 0 means judged not relevant, so it is not a gold answer
            if score > 0:
                qrels.setdefault(row["query-id"], {})[row["corpus-id"]] = score

    queries = {query_id: all_queries[query_id] for query_id in qrels}
    return Dataset(name=name, corpus=corpus, queries=queries, qrels=qrels)


def load(name: str, data_dir: Path = DATA_DIR) -> Dataset:
    return parse(name, download(name, data_dir))


def holdout(dataset: Dataset, size: int = 50, seed: int = 0) -> Split:
    # sorted before sampling so the same seed picks the same queries on every run
    query_ids = sorted(dataset.qrels)
    picked = set(random.Random(seed).sample(query_ids, min(size, len(query_ids))))

    # a doc relevant to a holdout query is withheld, so every other query sharing it is dropped too
    excluded_docs = {
        doc_id for query_id in picked for doc_id in dataset.qrels[query_id]
    }
    answerable = {
        query_id: rels
        for query_id, rels in dataset.qrels.items()
        if query_id not in picked and excluded_docs.isdisjoint(rels)
    }
    return Split(
        answerable=answerable, unanswerable=sorted(picked), excluded_docs=excluded_docs
    )
