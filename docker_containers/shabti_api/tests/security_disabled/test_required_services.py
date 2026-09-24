from ...src.app.functionality import status


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
    monkeypatch.setattr(status, "check_llm", offline)
    response = shabti_client.post("/collections", json={"collection_name": "missing"})
    assert_unavailable(response, "opensearch", "llm")
    assert "OpenSearch" in response.json()["message"]
    assert "LLM" in response.json()["message"]
