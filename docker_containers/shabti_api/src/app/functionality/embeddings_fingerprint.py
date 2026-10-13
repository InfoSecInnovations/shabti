"""Noticing when the embeddings model starts making different vectors under the same name.

Nothing promises that a model ID keeps meaning the same vectors: a provider can retire a model or
move an alias, an aggregator can route to a differently quantised host, and a local tag can change
when it is pulled again. New vectors would then not be comparable with the stored ones, and nothing
else would notice - the dimension usually still matches, so retrieval just quietly gets worse.

So a few fixed texts are embedded when the first collection is created and the vectors kept. They
are embedded again at startup and at most hourly after that, and if they no longer match, nothing
more is embedded until the documents are re-ingested. This is the same for every provider,
llama.cpp included.
"""

import asyncio
import logging
import time
from contextlib import suppress
import numpy as np
from opensearchpy.exceptions import NotFoundError
from .embeddings import create_embeddings, get_embeddings_model_id
from .opensearch import get_client

INDEX = "shabti_embeddings_fingerprint"
DOC_ID = "fingerprint"

PROBES = [
    "Shabti answers questions about the documents it has been given.",
    "The quarterly report was filed with the regulator on the fourteenth of March.",
    "Photosynthesis converts light energy into chemical energy stored in glucose.",
]

# the same model on a GPU doesn't make bit-identical vectors run to run, but stays far above this
THRESHOLD = 0.999
RECHECK_SECONDS = 3600
# a check that couldn't reach the model is tried again sooner than one that could
FAILED_RECHECK_SECONDS = 60

_state = {"drifted": False, "next_check": 0.0}
# per loop for the same reason as the OpenSearch client, see `opensearch.get_client`
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def lock() -> asyncio.Lock:
    return _locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())


def is_drifted() -> bool:
    return _state["drifted"]


def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def has_drifted(fingerprint: dict, model_id: str, vectors: list) -> bool:
    stored = fingerprint.get("vectors") or []
    if fingerprint.get("model") != model_id or len(stored) != len(vectors):
        return True
    if any(len(vector) != fingerprint.get("dimension") for vector in vectors):
        return True
    return any(cosine(a, b) < THRESHOLD for a, b in zip(stored, vectors))


async def stored() -> dict | None:
    client = get_client()
    if not await client.indices.exists(index=INDEX):
        return None
    try:
        return (await client.get(index=INDEX, id=DOC_ID))["_source"]
    except NotFoundError:
        return None


async def probe() -> tuple[str, list]:
    model_id = await asyncio.to_thread(get_embeddings_model_id)
    return model_id, await asyncio.to_thread(create_embeddings, PROBES, model_id)


async def record():
    model_id, vectors = await probe()
    client = get_client()
    if not await client.indices.exists(index=INDEX):
        await client.indices.create(
            index=INDEX,
            # the vectors are only ever read back whole, so nothing in them is indexed
            body={
                "mappings": {
                    "dynamic": False,
                    "properties": {
                        "model": {"type": "keyword"},
                        "dimension": {"type": "integer"},
                    },
                }
            },
        )
    await client.index(
        index=INDEX,
        id=DOC_ID,
        body={"model": model_id, "dimension": len(vectors[0]), "vectors": vectors},
        refresh=True,
    )
    _state.update(drifted=False, next_check=time.monotonic() + RECHECK_SECONDS)


async def ensure():
    """Records the fingerprint when the first collection is created, and checks it after that."""
    async with lock():
        if await stored() is None:
            await record()
            return
    await verify()


async def verify(force: bool = False):
    """Embeds the probes again and compares them, if the last check is old enough or `force`.

    Not being able to reach the model leaves the state as it was: whether the model is up is the
    health check's to report, and an outage is not a changed model.
    """
    async with lock():
        if not force and time.monotonic() < _state["next_check"]:
            return
        try:
            fingerprint = await stored()
            # nothing has been indexed yet, so there is nothing to drift from
            if fingerprint is None:
                _state.update(
                    drifted=False, next_check=time.monotonic() + RECHECK_SECONDS
                )
                return
            model_id, vectors = await probe()
        except Exception as e:
            logging.getLogger("shabti").warning(
                "could not check the embeddings model for changes: %s", type(e).__name__
            )
            _state.update(next_check=time.monotonic() + FAILED_RECHECK_SECONDS)
            return
        drifted = has_drifted(fingerprint, model_id, vectors)
        if drifted and not _state["drifted"]:
            logging.getLogger("shabti").error(
                "the embeddings model %s no longer makes the vectors it did when the first "
                "collection was created; documents must be re-ingested",
                model_id,
            )
        _state.update(drifted=drifted, next_check=time.monotonic() + RECHECK_SECONDS)


async def reset_if_unused():
    """Forgets the fingerprint once no collection holds vectors it describes.

    Deleting every collection is how documents are re-ingested after the model changed, and the
    next collection created then records the model as it is now.
    """
    client = get_client()
    mappings = await client.indices.get_mapping(index="*")
    if any(
        mapping.get("mappings", {}).get("_meta", {}).get("embeddings_model")
        for mapping in mappings.values()
    ):
        return
    async with lock():
        # the document rather than the index, which would be a change to the cluster's state for
        # every time the collections run out
        with suppress(NotFoundError):
            await client.delete(index=INDEX, id=DOC_ID, refresh=True)
        _state.update(drifted=False, next_check=0.0)
