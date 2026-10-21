import time
from .embeddings import MAX_TRIES, RETRY_STATUSES, retry_delay, send
from .providers.config import LOCAL
from .providers.registry import local_url, split_model_id


class RerankError(Exception):
    """A reranker that could not score the documents."""


def rerank(query: str, documents: list[str], model_id: str) -> list[float]:
    """One relevance score per document, in the order the documents were given.

    Higher is more relevant. Scores are only comparable within a single call.
    """
    # nothing to score, so nothing worth a round trip to be told so
    if not documents:
        return []
    provider_id, provider_model = split_model_id(model_id)
    # only llama.cpp serves reranking here, and a provider's own rerank API is not spoken to
    if provider_id != LOCAL:
        raise RerankError(f"only local rerankers are supported, not {model_id}")
    # top_n is every document: the caller wants a score for each, not a shortlist
    body = {
        "model": provider_model,
        "query": query,
        "documents": documents,
        "top_n": len(documents),
    }
    url = local_url("/v1/rerank")
    for attempt in range(MAX_TRIES):
        response = send("post", url, json=body)
        if response.status_code not in RETRY_STATUSES or attempt == MAX_TRIES - 1:
            break
        # llama.cpp answers 503 while it loads the reranker, which is waited out like any overload
        time.sleep(retry_delay(response, attempt))
    if response.status_code >= 400:
        raise RerankError(f"reranking failed: {response.text}")
    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise RerankError(f"reranking returned no results: {response.text}")
    # the results come back sorted by score, so they are placed by the index they name
    scores = {}
    for item in results:
        if isinstance(item, dict) and "index" in item and "relevance_score" in item:
            scores[item["index"]] = item["relevance_score"]
    missing = [i for i in range(len(documents)) if i not in scores]
    if missing:
        raise RerankError(
            f"reranking returned no score for documents {missing}: {response.text}"
        )
    return [scores[i] for i in range(len(documents))]
