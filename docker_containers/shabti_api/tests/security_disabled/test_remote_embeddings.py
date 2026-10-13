"""Embedding through a provider rather than llama.cpp, end to end.

The `stub` provider the harness writes is the test llama.cpp configured as a remote provider would
be, so this runs the remote path - the provider's base URL and key, batching, an estimated token
count and the fingerprint - without any external network. The rest of the suite stays on local
embeddings: an installation embeds with one model, so the switch is made for this module alone, by
pointing the API at copies of the installed settings that name the stub's model instead.
"""

import json
import os
import secrets

import pytest
import pytest_asyncio

from ...src.app.functionality import embeddings, embeddings_config
from ...src.app.functionality import embeddings_fingerprint
from ...src.app.functionality.document_collections import (
    delete_collection,
    get_documents,
)
from ...src.app.functionality.opensearch import get_client
from ...src.app.functionality.opensearch_prompting import get_context_from_opensearch
from ...src.app.functionality.providers import config as config_module

STUB = "stub"
filename = "prompt_test.md"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


@pytest.fixture
def stub_embeddings(shabti_client, tmp_path, monkeypatch):
    """The stub provider's copy of the local embeddings model, for as long as the test runs."""
    local = embeddings.get_embeddings_model_id()
    model = f"{STUB}/{local.partition('/')[2]}"
    with open(config_module.CONFIG_PATH) as f:
        providers = json.load(f)
    with open(embeddings_config.CONFIG_PATH) as f:
        settings = json.load(f)
    providers_path = tmp_path / "shabti-providers.json"
    providers_path.write_text(json.dumps({**providers, "embeddings": {"model": model}}))
    settings_path = tmp_path / "shabti-models.json"
    settings_path.write_text(
        json.dumps(
            {
                **settings,
                # the stub has no tokenizer to ask, as no provider does. a small batch so a page
                # takes more than one request
                model: {**settings[local], "tokenizer": "estimate", "batch_size": 2},
            }
        )
    )
    monkeypatch.setattr(config_module, "CONFIG_PATH", str(providers_path))
    monkeypatch.setattr(embeddings_config, "CONFIG_PATH", str(settings_path))
    # a fingerprint of its own, so the suite's local one is neither checked against the stub's
    # model nor replaced by it
    index = f"test_fingerprint_{secrets.token_hex(4)}"
    monkeypatch.setattr(embeddings_fingerprint, "INDEX", index)
    state = {"drifted": False, "next_check": 0.0}
    monkeypatch.setattr(embeddings_fingerprint, "_state", state)
    embeddings.get_vector_dimension.cache_clear()
    yield model
    embeddings.get_vector_dimension.cache_clear()


@pytest_asyncio.fixture
async def stub_collection_id(shabti_client, stub_embeddings):
    response = shabti_client.post(
        "/collections", json={"collection_name": secrets.token_hex(8)}
    )
    assert response.status_code == 201
    collection_id = response.json()["collection_id"]
    yield collection_id
    try:
        await delete_collection(None, collection_id)
    except Exception:  # the test may have got as far as deleting it
        pass
    client = get_client()
    if await client.indices.exists(index=embeddings_fingerprint.INDEX):
        await client.indices.delete(index=embeddings_fingerprint.INDEX)


async def test_a_collection_is_built_for_the_providers_model(
    stub_embeddings, stub_collection_id
):
    client = get_client()
    mappings = await client.indices.get_mapping(index=stub_collection_id)
    meta = mappings[stub_collection_id]["mappings"]["_meta"]
    assert meta["embeddings_model"] == stub_embeddings
    fingerprint = await embeddings_fingerprint.stored()
    assert fingerprint["model"] == stub_embeddings


async def test_a_document_is_ingested_and_searched_through_the_provider(
    shabti_client, stub_embeddings, stub_collection_id, ingest_and_wait
):
    with open(file_path, "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{stub_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201
    assert ingest.status == "complete"
    docs = await get_documents(None, stub_collection_id)
    doc = next(doc for doc in docs.documents if doc.filename == filename)
    assert doc.vector_count > 0

    context = await get_context_from_opensearch(
        stub_collection_id, 5, "What does the word prompting mean?"
    )
    assert context
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": stub_collection_id,
            "task": "search",
            "user_input": "What does the word prompting mean?",
        },
    )
    assert response.status_code == 200
    assert not embeddings_fingerprint.is_drifted()
