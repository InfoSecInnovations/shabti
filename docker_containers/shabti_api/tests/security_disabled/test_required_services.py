import math

from ...src.app.functionality import embeddings_fingerprint, status


async def offline():
    return False


def assert_unavailable(response, *services):
    assert response.status_code == 503
    body = response.json()
    assert body["error_type"] == "ServiceUnavailableError"
    assert body["services"] == list(services)


async def test_tika_status(shabti_client):
    response = shabti_client.get("/status/tika")
    assert response.status_code == 200
    assert response.json()["running"]


async def test_file_ingest_needs_tika(shabti_client, shabti_collection_id, monkeypatch):
    monkeypatch.setattr(status, "check_tika", offline)
    response = shabti_client.post(
        f"/collections/{shabti_collection_id}/documents/files",
        files=[("files", ("test_doc.txt", b"some text", "text/plain"))],
    )
    assert_unavailable(response, "tika")


async def test_url_ingest_does_not_need_tika(
    shabti_client, shabti_collection_id, monkeypatch
):
    monkeypatch.setattr(status, "check_tika", offline)
    # refused by the URL guard, which only runs once the service check has let the request through,
    # so nothing is actually crawled
    response = shabti_client.post(
        f"/collections/{shabti_collection_id}/documents/urls",
        json=["http://127.0.0.1:9200/"],
    )
    assert response.status_code == 403
    assert response.json()["error_type"] == "ForbiddenUrlError"


async def test_listing_documents_needs_opensearch(
    shabti_client, shabti_collection_id, monkeypatch
):
    monkeypatch.setattr(status, "check_opensearch", offline)
    response = shabti_client.get(f"/collections/{shabti_collection_id}/documents")
    assert_unavailable(response, "opensearch")


async def test_models_need_the_llm(shabti_client, monkeypatch):
    monkeypatch.setattr(status, "check_llm", offline)
    assert_unavailable(shabti_client.get("/models"), "llm")


async def test_every_missing_service_is_named(shabti_client, monkeypatch):
    monkeypatch.setattr(status, "check_opensearch", offline)
    monkeypatch.setattr(status, "check_embeddings", offline)
    response = shabti_client.post("/collections", json={"collection_name": "missing"})
    assert_unavailable(response, "opensearch", "embeddings")
    assert "OpenSearch" in response.json()["message"]
    assert "Embeddings" in response.json()["message"]


async def test_embeddings_status(shabti_client):
    response = shabti_client.get("/status/embeddings")
    assert response.status_code == 200
    assert response.json()["running"]


async def test_ingest_needs_embeddings_rather_than_the_llm(
    shabti_client, shabti_collection_id, monkeypatch
):
    monkeypatch.setattr(status, "check_embeddings", offline)
    response = shabti_client.post(
        f"/collections/{shabti_collection_id}/documents/files",
        files=[("files", ("test_doc.txt", b"some text", "text/plain"))],
    )
    assert_unavailable(response, "embeddings")


async def test_prompting_needs_embeddings_rather_than_the_llm(
    shabti_client, shabti_collection_id, monkeypatch
):
    # a remote chat model needs no llama.cpp, and a local one is checked by the prompt's validator
    monkeypatch.setattr(status, "check_llm", offline)
    monkeypatch.setattr(status, "check_embeddings", offline)
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "search",
            "user_input": "anything",
        },
    )
    assert_unavailable(response, "embeddings")


async def test_a_changed_embeddings_model_says_so(
    shabti_client, shabti_collection_id, monkeypatch
):
    # not due a check, so the state set here is the one the request sees
    monkeypatch.setitem(embeddings_fingerprint._state, "drifted", True)
    monkeypatch.setitem(embeddings_fingerprint._state, "next_check", math.inf)
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "search",
            "user_input": "anything",
        },
    )
    assert response.status_code == 409
    assert response.json()["error_type"] == "EmbeddingsDriftError"
    assert not shabti_client.get("/status/embeddings").json()["running"]
