import asyncio
from .embeddings import create_embeddings
from .opensearch import get_client, get_document_counts

# the similarity floor a chunk has to clear to be a reference at all. this is quite a magic number,
# tweak as needed!
MIN_SCORE = 0.6


async def get_context_from_opensearch(
    collection_id: str, reference_limit: int, user_input: str
):
    client = get_client()

    # the embeddings server is reached with blocking requests, so it goes in a thread
    embedding = await asyncio.to_thread(create_embeddings, user_input)

    query = {
        "size": reference_limit,
        "query": {
            "knn": {
                "document_vector": {
                    "vector": embedding,
                    "min_score": MIN_SCORE,
                }
            }
        },
        # identical text embeds to an identical vector, so a document that made it into the
        # collection twice would otherwise fill the whole window with copies of one chunk and push
        # every other source out of the answer. collapsing happens in the collector rather than
        # over the hits it already chose, so `size` still means `size` *distinct* references and
        # there is nothing to over-fetch
        "collapse": {"field": "text_hash"},
        "_source": {"includes": ["page_id", "text", "child_item_to_document"]},
    }

    response = await client.search(body=query, index=collection_id)

    hits = [hit["_source"] for hit in response["hits"]["hits"]]

    if not hits:
        return {"context": "", "sources": []}

    # the vector child carries its document's id as well as its page's, so both lookups can be one
    # batch each. this was a `get` per hit for the pages, another per hit for the documents, and a
    # counts query per document on top of that - up to sixteen round trips for five references
    pages = {hit["page_id"]: hit["child_item_to_document"]["parent"] for hit in hits}
    page_response = await client.mget(
        body={
            "docs": [
                # a page is a routed child, so it cannot be fetched without saying what it was
                # routed on. these indices having a single shard is the only reason leaving the
                # routing off has worked until now
                {"_index": collection_id, "_id": page_id, "routing": parent}
                for page_id, parent in pages.items()
            ]
        }
    )
    page_metadata = {
        doc["_id"]: doc["_source"] for doc in page_response["docs"] if doc.get("found")
    }

    doc_ids = list(dict.fromkeys(pages.values()))
    docs = [{"_index": collection_id, "_id": doc_id} for doc_id in doc_ids]
    doc_response = await client.mget(body={"docs": docs})
    # one aggregation for every document rather than the one query per document `get_document`
    # would have made
    counts = await get_document_counts(collection_id, doc_ids)
    doc_metadata = {
        doc["_id"]: {**doc["_source"], "id": doc["_id"], **counts[doc["_id"]]}
        for doc in doc_response["docs"]
        if doc.get("found")
    }

    texts = []
    sources = []

    for hit in hits:
        page = page_metadata.get(hit["page_id"])
        doc = doc_metadata.get(hit["child_item_to_document"]["parent"])
        # a document deleted between the search and these lookups leaves its chunk with nothing to
        # attribute it to, and an answer is better one reference short than failed outright
        if page is None or doc is None:
            continue
        texts.append(hit["text"])
        sources.append(
            {"page_metadata": page, "doc_metadata": {**doc, "document_id": doc["id"]}}
        )

    return {
        "context": "\n".join(texts),
        "sources": sources,
    }
