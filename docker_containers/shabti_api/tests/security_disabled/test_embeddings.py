"""Embeddings against the real model server.

Ingesting anything at all depends on this working, and it is the one part of an ingest with no
fallback: a document whose chunks cannot be embedded is rolled back entirely. The unit tests cover
the error handling; this file is here to say that the normal path still produces usable vectors.

Nothing here skips. The harness writes `my-models.ini` itself before every end-to-end run and
refuses to build a selection without an embeddings model, so one is always installed - and if that
ever stops being true, a silent skip would report the same green summary as a working stack.
"""

import asyncio
import math

from ...src.app.functionality.embeddings import (
    create_embeddings,
    get_embeddings_model_id,
)
from ...src.app.functionality.opensearch import VECTOR_DIMENSION


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def embeddings_model_id():
    return await asyncio.to_thread(get_embeddings_model_id)


async def embed(text, model_id):
    return await asyncio.to_thread(create_embeddings, text, model_id)


async def test_an_embeddings_model_is_installed(shabti_client):
    assert await embeddings_model_id()


async def test_text_embeds_to_a_vector_of_the_indexed_dimension(shabti_client):
    vector = await embed("Shabti ingests documents.", await embeddings_model_id())
    assert len(vector) == VECTOR_DIMENSION
    assert all(isinstance(value, (int, float)) for value in vector)
    # an all-zero vector has no direction to compare against, which is what a model that loaded but
    # produced nothing would return
    assert any(value for value in vector)


async def test_a_batch_embeds_to_one_vector_each(shabti_client):
    # ingesting embeds a page's chunks in a single call, so the batched path is the one that runs
    chunks = ["the first chunk", "the second chunk", "the third chunk"]
    vectors = await embed(chunks, await embeddings_model_id())
    assert len(vectors) == len(chunks)
    assert all(len(vector) == VECTOR_DIMENSION for vector in vectors)


async def test_related_text_embeds_closer_than_unrelated_text(shabti_client):
    # the shape being right doesn't mean the vectors mean anything: a mis-loaded model returns
    # well formed noise, and search would quietly return whatever happened to be nearest
    model_id = await embeddings_model_id()
    subject, related, unrelated = await embed(
        [
            "The cat sat on the mat.",
            "A cat is sitting on a rug.",
            "Quarterly revenue exceeded the forecast.",
        ],
        model_id,
    )
    assert cosine(subject, related) > cosine(subject, unrelated)
