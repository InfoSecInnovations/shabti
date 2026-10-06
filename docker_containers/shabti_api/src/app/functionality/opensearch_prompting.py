import asyncio
from .embeddings import (
    count_tokens,
    create_embeddings,
    get_embeddings_model_id,
    mean_vector,
)
from .embeddings_config import chunk_size, query_prefix
from .opensearch import get_client, get_document_counts, get_ingesting_document_ids
from .opensearch_ingesting import get_splitter

# the similarity floor a chunk has to clear to be a reference at all. this is quite a magic number,
# tweak as needed!
MIN_SCORE = 0.6


async def get_context_from_opensearch(
    collection_id: str, reference_limit: int, user_input: str
):
    client = get_client()

    def embed_query():
        """The question as the model expects to be asked it.

        The model is looked up here rather than left to `create_embeddings` to find, which it would
        have done anyway: an asymmetric model wants an instruction in front of a query, and what
        that instruction is can only be known once we know which model is answering.

        A question has no length limit, but the model does: its whole input has to fit one physical
        batch, and it was never trained on more than its context. One longer than a chunk is split
        the way a document is and stands for the average of its pieces, rather than being cut short
        or refused. `chunk_size` is the budget because the installer sizes the model's batch from
        it, so a piece that fits it fits whatever that batch turned out to be.
        """
        model_id = get_embeddings_model_id()
        # blank input is left alone: `create_embeddings` returns nothing for it, and prefixing it
        # would turn "nothing to search for" into a search for the instruction itself
        if not user_input.strip():
            return create_embeddings(user_input, model_id)
        prefix = query_prefix(model_id)
        text = f"{prefix}{user_input}"
        if count_tokens(text, model_id) <= chunk_size(model_id):
            return create_embeddings(text, model_id)
        pieces = [
            piece
            for piece in get_splitter(model_id, prefix, "query_prefix").chunks(
                user_input
            )
            if piece.strip()
        ]
        # one call for every piece, each asked the way the model expects a query to be
        vectors = create_embeddings([f"{prefix}{piece}" for piece in pieces], model_id)
        # weighted by length so that the short remainder the splitter leaves at the end counts for
        # what it is. characters rather than tokens, which would cost a round trip per piece to be
        # only slightly more right
        return mean_vector(vectors, [len(piece) for piece in pieces])

    # the embeddings server is reached with blocking requests, so it goes in a thread, and the
    # documents to keep out of the answer are read while it is in there rather than after it
    embedding, ingesting = await asyncio.gather(
        asyncio.to_thread(embed_query),
        get_ingesting_document_ids(collection_id),
    )

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

    if ingesting:
        # excluded in the query rather than dropped from its results, so a document being written
        # costs an answer no references: `size` is how many an answer gets, and hits thrown away
        # afterwards would quietly shrink it. `filter` is applied as the kNN collects, and is
        # supported alongside `min_score` on the lucene engine this index uses. left off entirely
        # when nothing is ingesting, which is the ordinary case
        query["query"]["knn"]["document_vector"]["filter"] = {
            "bool": {"must_not": [{"terms": {"doc_id": ingesting}}]}
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
        # the filter above is what keeps the reference window full; this is what makes never
        # quoting an unfinished document true rather than merely likely, for one whose first
        # vectors became searchable in between the two reads
        if doc.get("found") and not doc["_source"].get("ingesting")
    }

    texts = []
    sources = []

    for hit in hits:
        page = page_metadata.get(hit["page_id"])
        doc = doc_metadata.get(hit["child_item_to_document"]["parent"])
        # a document deleted between the search and these lookups, or one that turned out to still
        # be ingesting, leaves its chunk with nothing to attribute it to, and an answer is better
        # one reference short than failed outright
        if page is None or doc is None:
            continue
        texts.append(hit["text"])
        sources.append(
            {
                "page_metadata": page,
                "doc_metadata": {**doc, "document_id": doc["id"]},
                "text": hit["text"],
            }
        )

    return {
        "context": "\n".join(texts),
        "sources": sources,
    }
