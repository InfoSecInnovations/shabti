"""BEIR SciFact and NFCorpus, their multilingual NanoBEIR versions, and a holdout of queries left
unanswerable by construction.

The holdout's relevant docs are kept out of the ingested corpus, so those queries have no
gold answer in the index and any answer the system gives to them is a hallucination.

The NanoBEIR sets are 50-query samples of the same tasks, machine-translated into each language, so a
`nano-scifact-en` run is the bridge between the full English set and the other languages.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

DATA_DIR = Path(__file__).parent / "data"
BEIR_NAMES = ("scifact", "nfcorpus")
# what a folder needs to be read as a dataset: the corpus, the queries and the judgements
BEIR_FILES = ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv")
URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"

NANO_REPO = "LiquidAI/nanobeir-multilingual-extended"
NANO_TASKS = {"scifact": "NanoSciFact", "nfcorpus": "NanoNFCorpus"}
NANO_LANGUAGES = ("ar", "de", "en", "es", "fr", "it", "ja", "ko", "no", "pt", "sv")
NANO_NAMES = tuple(
    f"nano-{task}-{language}" for task in NANO_TASKS for language in NANO_LANGUAGES
)
# the dataset viewer serves the repo's parquet files as JSON, so no parquet reader is needed
ROWS_URL = "https://datasets-server.huggingface.co/rows"
# the most rows the viewer returns per request
ROWS_PAGE = 100
# the viewer rate limits anonymous clients, a corpus is about 30 pages, and it has the odd bad gateway
ROWS_RETRIES = 8

NAMES = BEIR_NAMES + NANO_NAMES


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
    if name in NANO_NAMES:
        _download_nano(name, target)
        return target
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


def _rows(config: str, split: str) -> Iterator[dict]:
    offset = 0
    while True:
        params = {
            "dataset": NANO_REPO,
            "config": config,
            "split": split,
            "offset": offset,
            "length": ROWS_PAGE,
        }
        for attempt in range(ROWS_RETRIES):
            response = requests.get(ROWS_URL, params=params, timeout=120)
            if response.status_code != 429 and response.status_code < 500:
                break
            time.sleep(float(response.headers.get("Retry-After", 2**attempt)))
        response.raise_for_status()
        payload = response.json()
        for item in payload["rows"]:
            yield item["row"]
        offset += len(payload["rows"])
        if not payload["rows"] or offset >= payload["num_rows_total"]:
            return


def _download_nano(name: str, target: Path) -> None:
    _, task, language = name.split("-")
    config = f"{NANO_TASKS[task]}_{language}"
    # written in BEIR's layout so parse reads it like any other dataset, into a temp dir first so
    # an interrupted download leaves no corpus.jsonl behind to be mistaken for a complete one
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        staged = Path(tmp) / name
        (staged / "qrels").mkdir(parents=True)
        with (staged / "corpus.jsonl").open("w", encoding="utf-8") as f:
            for row in _rows(config, "corpus"):
                # the translations carry no title, only the text
                f.write(json.dumps({"_id": row["_id"], "text": row["text"]}) + "\n")
        with (staged / "queries.jsonl").open("w", encoding="utf-8") as f:
            for row in _rows(config, "queries"):
                f.write(json.dumps({"_id": row["_id"], "text": row["text"]}) + "\n")
        with (staged / "qrels" / "test.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["query-id", "corpus-id", "score"])
            # the judgements are ungraded: a listed pair is relevant
            for row in _rows(f"{config}_qrels", "qrels"):
                writer.writerow([row["query-id"], row["corpus-id"], 1])
        staged.rename(target)


def default_holdout(name: str) -> int:
    # the BEIR sets are big enough to spare 50 queries, but a 50-query nano set has none to spare
    # and a custom set may have only a handful, so holding queries out is opt-in with --holdout
    return 50 if name in BEIR_NAMES else 0


class DatasetLayoutError(ValueError):
    pass


def check_layout(path: Path) -> None:
    if not path.is_dir():
        raise DatasetLayoutError(f"{path} is not a folder")
    for name in BEIR_FILES:
        if not (path / name).is_file():
            raise DatasetLayoutError(
                f"{path} has no {name}, so it is not a dataset in BEIR layout"
            )


def corpus_fingerprint(path: Path) -> str:
    # read in chunks so a large corpus is never held in memory
    digest = hashlib.sha256()
    with (path / "corpus.jsonl").open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


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
