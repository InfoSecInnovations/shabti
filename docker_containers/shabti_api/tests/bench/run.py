"""Runs retrieval variants over a BEIR dataset ingested into Shabti, and writes a comparison report.

Ingestion goes through the real API so the corpus is chunked, embedded and indexed the way a user's
documents would be. Retrieval goes through the production `get_context_from_opensearch`, with each
variant's settings applied as env overrides, so a variant measures what the app would actually do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient
from shabti_types import IngestInfo

from ...src.app.app import create_app
from ...src.app.functionality.document_collections import (
    create_collection,
    delete_collection,
    get_collections,
)
from ...src.app.functionality.opensearch import close_client, get_client
from ...src.app.functionality.fusion import min_max_fusion, reciprocal_rank_fusion
from ...src.app.functionality.opensearch_prompting import (
    dense_query,
    embed_query,
    get_context_from_opensearch,
    keyword_query,
)
from ...src.load_dotenv import load_env
from .datasets import NAMES, Dataset, Split, holdout, load
from .metrics import (
    hit_at_k,
    mean,
    mrr_at_k,
    ndcg_at_k,
    paired_randomization_test,
    percentile,
    recall_at_k,
)

BATCH_SIZE = 100
# a collection this close to the expected count is reused, since shabti refuses duplicate documents
REUSE_THRESHOLD = 0.99

HYBRID_RRF60 = {
    "SHABTI_RETRIEVAL_MODE": "hybrid",
    "SHABTI_HYBRID_FUSION": "rrf",
    "SHABTI_RRF_K": "60",
}

VARIANTS: dict[str, dict[str, str]] = {
    "B0": {},
    "H-rrf60": HYBRID_RRF60,
    "H-rrf20": {**HYBRID_RRF60, "SHABTI_RRF_K": "20"},
    "H-mm30": {
        "SHABTI_RETRIEVAL_MODE": "hybrid",
        "SHABTI_HYBRID_FUSION": "min_max",
        "SHABTI_HYBRID_DENSE_WEIGHT": "0.3",
    },
    "H-mm50": {
        "SHABTI_RETRIEVAL_MODE": "hybrid",
        "SHABTI_HYBRID_FUSION": "min_max",
        "SHABTI_HYBRID_DENSE_WEIGHT": "0.5",
    },
    "H-mm70": {
        "SHABTI_RETRIEVAL_MODE": "hybrid",
        "SHABTI_HYBRID_FUSION": "min_max",
        "SHABTI_HYBRID_DENSE_WEIGHT": "0.7",
    },
    "R-dense-bge": {"SHABTI_RERANK_MODEL": "local/bge-reranker-v2-m3"},
    "R-hyb-jina": {
        **HYBRID_RRF60,
        "SHABTI_RERANK_MODEL": "local/jina-reranker-v1-tiny-en",
    },
    "R-hyb-bge": {**HYBRID_RRF60, "SHABTI_RERANK_MODEL": "local/bge-reranker-v2-m3"},
    "R-hyb-qwen": {**HYBRID_RRF60, "SHABTI_RERANK_MODEL": "local/Qwen3-Reranker-0.6B"},
}

# every key any variant touches, so variant_env can clear them all before applying overrides
VARIANT_KEYS = sorted({key for overrides in VARIANTS.values() for key in overrides})

# the fusion settings the offline sweep tries, matching what production exposes as tunables
RRF_KS = (10, 20, 40, 60, 100)
MIN_MAX_DENSE_WEIGHTS = tuple(range(10, 100, 10))


@contextmanager
def variant_env(overrides: dict[str, str]) -> Iterator[None]:
    # clearing first stops a variant inheriting a setting from the shell or an earlier variant
    saved = {key: os.environ.get(key) for key in VARIANT_KEYS}
    try:
        for key in VARIANT_KEYS:
            os.environ.pop(key, None)
        os.environ.update(overrides)
        yield
    finally:
        # restore exactly, including keys that were unset before, so later code sees the same env
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ingest_and_wait(client: TestClient, method: str, url: str, **kwargs):
    """POST an ingest and block until it is over, returning the response and terminal state.

    A non-2xx is handed straight back undrained, so the caller can count it as a failed batch.
    """
    response = client.request(method, url, **kwargs)
    if response.status_code // 100 != 2:
        return response, None, []
    ingest_id = response.json()["ingest_id"]
    lines = []
    # the stream ends when the ingest does, so draining it is the wait
    with client.stream("GET", f"/ingests/{ingest_id}") as stream:
        for line in stream.iter_lines():
            if line.strip():
                lines.append(json.loads(line))
    listed = client.get("/ingests").json()
    terminal = next(
        IngestInfo(**item) for item in listed if item["ingest_id"] == ingest_id
    )
    return response, terminal, lines


def corpus_ids(context: dict) -> list[str]:
    # the corpus id is the file stem, since every document was uploaded as <corpus_id>.txt
    return [
        Path(source["doc_metadata"]["filename"]).stem for source in context["sources"]
    ]


def _document_text(title: str, text: str) -> str:
    return f"{title}\n\n{text}" if title else text


async def _count_documents(collection_id: str) -> int:
    response = await get_client().count(
        body={"query": {"term": {"type": "document"}}}, index=collection_id
    )
    return response["count"]


async def _survey(name: str) -> list[tuple[str, int]]:
    try:
        collections = await get_collections(None)
        return [
            (collection.collection_id, await _count_documents(collection.collection_id))
            for collection in collections
            if collection.collection_name == name
        ]
    finally:
        await close_client()


async def _recreate(name: str, stale_ids: list[str]) -> str:
    try:
        for collection_id in stale_ids:
            await delete_collection(None, collection_id)
        collection = await create_collection(None, name)
        return collection.collection_id
    finally:
        await close_client()


def ensure_collection(dataset: Dataset, split: Split, client: TestClient) -> str:
    name = f"bench-{dataset.name}"
    expected = len(dataset.corpus) - len(split.excluded_docs)

    # a collection left from an earlier run is reused when it is complete, so a rerun skips ingestion
    matches = asyncio.run(_survey(name))
    for collection_id, count in matches:
        if count >= REUSE_THRESHOLD * expected:
            print(f"reusing {name} ({collection_id}): {count}/{expected} documents")
            return collection_id

    print(f"creating {name}: ingesting {expected} documents")
    collection_id = asyncio.run(_recreate(name, [cid for cid, _ in matches]))

    documents = [
        (corpus_id, doc)
        for corpus_id, doc in dataset.corpus.items()
        if corpus_id not in split.excluded_docs
    ]
    batches = [
        documents[start : start + BATCH_SIZE]
        for start in range(0, len(documents), BATCH_SIZE)
    ]
    for number, batch in enumerate(batches, start=1):
        # the files only need to exist for the upload, so a temp dir per batch is cleaned up with it
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for corpus_id, doc in batch:
                path = Path(directory) / f"{corpus_id}.txt"
                path.write_text(_document_text(doc.title, doc.text), encoding="utf-8")
                paths.append(path)
            handles = [("files", path.open("rb")) for path in paths]
            try:
                response, ingest, _ = ingest_and_wait(
                    client,
                    "POST",
                    f"/collections/{collection_id}/documents/files",
                    files=handles,
                )
            finally:
                for _, handle in handles:
                    handle.close()
        if ingest is None:
            failed = len(batch)
            print(
                f"batch {number}/{len(batches)}: rejected with {response.status_code}"
            )
        else:
            failed = sum(1 for item in ingest.items if item.error)
            print(f"batch {number}/{len(batches)}: {len(batch)} files, {failed} failed")
    return collection_id


async def _run_variant(
    collection_id: str, queries: dict[str, str]
) -> tuple[dict[str, list[str]], dict[str, float]]:
    ranked: dict[str, list[str]] = {}
    latencies: dict[str, float] = {}
    for query_id, text in queries.items():
        start = time.perf_counter()
        context = await get_context_from_opensearch(collection_id, 10, text)
        latencies[query_id] = (time.perf_counter() - start) * 1000
        ranked[query_id] = corpus_ids(context)
    return ranked, latencies


async def _retrieve(
    collection_id: str, queries: dict[str, str], variant_ids: list[str]
) -> dict[str, tuple[dict[str, list[str]], dict[str, float]]]:
    # one loop for every variant, since the OpenSearch client is cached per loop
    try:
        results = {}
        for variant_id in variant_ids:
            with variant_env(VARIANTS[variant_id]):
                start = time.perf_counter()
                results[variant_id] = await _run_variant(collection_id, queries)
                elapsed = time.perf_counter() - start
            print(f"{variant_id}: {len(queries)} queries in {elapsed:.1f}s")
        return results
    finally:
        await close_client()


async def _parent_corpus_ids(collection_id: str) -> dict[str, str]:
    # parents are the documents; a chunk points back at its parent by OpenSearch id, so this maps
    # that id to the corpus id the file was uploaded under
    query = {"term": {"type": "document"}}
    count = await _count_documents(collection_id)
    response = await get_client().search(
        body={"size": count, "query": query, "_source": ["filename"]},
        index=collection_id,
    )
    return {
        hit["_id"]: Path(hit["_source"]["filename"]).stem
        for hit in response["hits"]["hits"]
    }


async def _fetch_legs(
    collection_id: str, queries: dict[str, str], depth: int
) -> dict[str, dict[str, list[tuple[str, str, float]]]]:
    # one loop for every query, since the OpenSearch client is cached per loop
    try:
        client = get_client()
        parents = await _parent_corpus_ids(collection_id)
        legs = {}
        for query_id, text in queries.items():
            embedding = await asyncio.to_thread(embed_query, text)
            result = await client.msearch(
                body=[
                    {"index": collection_id},
                    dense_query(embedding, depth, []),
                    {"index": collection_id},
                    keyword_query(text, depth, []),
                ]
            )
            responses = result["responses"]
            for response in responses:
                if "error" in response:
                    raise RuntimeError(response["error"])
            legs[query_id] = {
                name: [
                    (
                        hit["_source"]["text_hash"],
                        parents[hit["_source"]["child_item_to_document"]["parent"]],
                        hit["_score"],
                    )
                    for hit in response["hits"]["hits"]
                ]
                for name, response in zip(("dense", "keyword"), responses)
            }
        return legs
    finally:
        await close_client()


def sweep_runs(
    legs: dict[str, dict[str, list[tuple[str, str, float]]]],
    reference_limit: int = 10,
) -> dict[str, dict[str, list[str]]]:
    runs: dict[str, dict[str, list[str]]] = {"S-dense": {}, "S-bm25": {}}
    for k in RRF_KS:
        runs[f"S-rrf{k}"] = {}
    for dense_percent in MIN_MAX_DENSE_WEIGHTS:
        runs[f"S-mm{dense_percent}"] = {}

    for query_id, leg in legs.items():
        dense, keyword = leg["dense"], leg["keyword"]
        # fusion works on text_hash, so the corpus id each chunk maps to is kept alongside it
        corpus: dict[str, str] = {}
        for text_hash, corpus_id, _ in [*dense, *keyword]:
            corpus.setdefault(text_hash, corpus_id)

        runs["S-dense"][query_id] = [corpus_id for _, corpus_id, _ in dense]
        runs["S-bm25"][query_id] = [corpus_id for _, corpus_id, _ in keyword]

        dense_keys = [text_hash for text_hash, _, _ in dense]
        keyword_keys = [text_hash for text_hash, _, _ in keyword]
        dense_scored = [(text_hash, score) for text_hash, _, score in dense]
        keyword_scored = [(text_hash, score) for text_hash, _, score in keyword]
        # mirrors production: with no dense hits and no reranker, the answer is empty
        gated = not dense

        def to_corpus(fused: list[tuple[str, float]]) -> list[str]:
            return [corpus[text_hash] for text_hash, _ in fused[:reference_limit]]

        for k in RRF_KS:
            fused = (
                [] if gated else reciprocal_rank_fusion([dense_keys, keyword_keys], k)
            )
            runs[f"S-rrf{k}"][query_id] = to_corpus(fused)
        for dense_percent in MIN_MAX_DENSE_WEIGHTS:
            dense_weight = dense_percent / 100
            fused = (
                []
                if gated
                else min_max_fusion(
                    [dense_scored, keyword_scored], [dense_weight, 1 - dense_weight]
                )
            )
            runs[f"S-mm{dense_percent}"][query_id] = to_corpus(fused)
    return runs


def candidate_ceilings(
    legs: dict[str, dict[str, list[tuple[str, str, float]]]],
    qrels: dict[str, dict[str, int]],
) -> dict[str, float]:
    # recall at full depth: what a first stage could hand to fusion or a reranker at best
    found: dict[str, list[float]] = {"dense": [], "keyword": [], "union": []}
    for query_id in qrels:
        leg = legs[query_id]
        dense = [corpus_id for _, corpus_id, _ in leg["dense"]]
        keyword = [corpus_id for _, corpus_id, _ in leg["keyword"]]
        union = [*dense, *keyword]
        rel = qrels[query_id]
        # k is the whole list, so recall counts every deduped doc the leg returned
        found["dense"].append(recall_at_k(dense, rel, len(dense)))
        found["keyword"].append(recall_at_k(keyword, rel, len(keyword)))
        found["union"].append(recall_at_k(union, rel, len(union)))
    return {name: mean(values) for name, values in found.items()}


def evaluate(
    run: dict[str, list[str]],
    qrels: dict[str, dict[str, int]],
    unanswerable: list[str],
    latencies_ms: dict[str, float],
) -> dict:
    query_ids = sorted(qrels)
    per_query = {
        "hit@5": [],
        "recall@5": [],
        "ndcg@10": [],
        "mrr@10": [],
    }
    for query_id in query_ids:
        ranked = run.get(query_id, [])
        rel = qrels[query_id]
        per_query["hit@5"].append(hit_at_k(ranked, rel, 5))
        per_query["recall@5"].append(recall_at_k(ranked[:5], rel, 5))
        per_query["ndcg@10"].append(ndcg_at_k(ranked, rel, 10))
        per_query["mrr@10"].append(mrr_at_k(ranked, rel, 10))

    latencies = list(latencies_ms.values())
    return {
        "hit@5": mean(per_query["hit@5"]),
        "recall@5": mean(per_query["recall@5"]),
        "ndcg@10": mean(per_query["ndcg@10"]),
        "mrr@10": mean(per_query["mrr@10"]),
        "per_query": per_query,
        "empty_unanswerable": mean(
            [1.0 if not run.get(query_id) else 0.0 for query_id in unanswerable]
        ),
        "empty_answerable": mean(
            [1.0 if not run.get(query_id) else 0.0 for query_id in query_ids]
        ),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
    }


def markdown_report(
    dataset_name: str,
    results: dict[str, dict],
    baseline: str = "B0",
    heading: bool = True,
) -> str:
    # the sweep table sits under its own section heading, so it leaves the title off
    title = [f"# Retrieval benchmark: {dataset_name}", ""] if heading else []
    lines = [
        *title,
        "| Variant | Hit@5 | Recall@5 | nDCG@10 | MRR@10 | ΔnDCG@10 vs "
        f"{baseline} (p) | Empty on unanswerable | Empty on answerable | p50 ms | p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    base = results.get(baseline)
    for variant_id, result in results.items():
        if base is None or variant_id == baseline:
            delta = "—"
        else:
            ours = result["per_query"]["ndcg@10"]
            theirs = base["per_query"]["ndcg@10"]
            difference = mean(ours) - mean(theirs)
            p_value = paired_randomization_test(ours, theirs)
            delta = f"{difference:+.3f} (p={p_value:.3f})"
        lines.append(
            "| "
            + " | ".join(
                [
                    variant_id,
                    f"{result['hit@5']:.3f}",
                    f"{result['recall@5']:.3f}",
                    f"{result['ndcg@10']:.3f}",
                    f"{result['mrr@10']:.3f}",
                    delta,
                    f"{result['empty_unanswerable'] * 100:.1f}%",
                    f"{result['empty_answerable'] * 100:.1f}%",
                    f"{result['p50_ms']:.0f}",
                    f"{result['p95_ms']:.0f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _limit(split: Split, count: int | None) -> Split:
    if count is None:
        return split
    answerable = {
        query_id: split.answerable[query_id]
        for query_id in sorted(split.answerable)[:count]
    }
    return Split(
        answerable=answerable,
        unanswerable=sorted(split.unanswerable)[:count],
        excluded_docs=split.excluded_docs,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.bench")
    parser.add_argument("dataset", choices=NAMES)
    parser.add_argument("--variants", nargs="+", choices=list(VARIANTS), default=None)
    parser.add_argument(
        "--queries",
        type=int,
        default=None,
        help="use only the first N query ids of each of the answerable and unanswerable sets",
    )
    parser.add_argument(
        "--sweep-depth",
        type=int,
        default=100,
        help="candidates per leg for the offline fusion sweep, 0 disables it",
    )
    parser.add_argument("--holdout", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="/test_results/bench")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    variant_ids = [v for v in VARIANTS if not args.variants or v in args.variants]

    dataset = load(args.dataset)
    split = _limit(holdout(dataset, args.holdout, args.seed), args.queries)
    # answerable queries first, then unanswerable, each in sorted id order
    queries = {
        query_id: dataset.queries[query_id]
        for query_id in [*sorted(split.answerable), *split.unanswerable]
    }

    load_env()
    with TestClient(create_app()) as client:
        collection_id = ensure_collection(dataset, split, client)
        raw = asyncio.run(_retrieve(collection_id, queries, variant_ids))
        legs = (
            asyncio.run(_fetch_legs(collection_id, queries, args.sweep_depth))
            if args.sweep_depth > 0
            else None
        )

    results = {
        variant_id: evaluate(ranked, split.answerable, split.unanswerable, latencies)
        for variant_id, (ranked, latencies) in raw.items()
    }

    sweep = None
    if legs is not None:
        sweep = {
            "depth": args.sweep_depth,
            "ceilings": candidate_ceilings(legs, split.answerable),
            "runs": {
                run_id: evaluate(ranked, split.answerable, split.unanswerable, {})
                for run_id, ranked in sweep_runs(legs).items()
            },
        }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / f"{stamp}-{args.dataset}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = markdown_report(args.dataset, results)
    if sweep is not None:
        ceilings = sweep["ceilings"]
        report += (
            f"\n## Offline fusion sweep (depth {sweep['depth']})\n\n"
            f"Candidate ceilings (recall@{sweep['depth']}): "
            f"dense {ceilings['dense']:.3f}, keyword {ceilings['keyword']:.3f}, "
            f"union {ceilings['union']:.3f}\n\n"
            + markdown_report(
                args.dataset, sweep["runs"], baseline="S-dense", heading=False
            )
        )
    payload = {
        "dataset": args.dataset,
        "args": vars(args),
        "splits": {
            "corpus": len(dataset.corpus),
            "excluded_docs": len(split.excluded_docs),
            "answerable": len(split.answerable),
            "unanswerable": len(split.unanswerable),
        },
        "variants": results,
        "sweep": sweep,
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)
