import json

import pytest
import requests
from ...src.app.functionality.loaders import tika_client
from ...src.app.functionality.loaders.tika_client import TikaError, recursive_metadata


def response(status: int, body: str) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    return r


@pytest.fixture
def put(monkeypatch):
    """Every PUT the client makes, answered with whatever `put.answer` is."""
    calls = []

    def fake(url, **kwargs):
        calls.append((url, kwargs))
        return fake.answer

    fake.calls = calls
    fake.answer = response(200, "[]")
    monkeypatch.setattr(tika_client.requests, "put", fake)
    return fake


def test_the_document_goes_to_rmeta_xml(monkeypatch, put):
    monkeypatch.setenv("TIKA_SERVER_ENDPOINT", "http://tika:9998/")
    recursive_metadata(b"document bytes")
    [(url, kwargs)] = put.calls
    assert url == "http://tika:9998/rmeta/xml"
    assert kwargs["data"] == b"document bytes"


def test_the_entries_come_back_as_tika_sent_them(put):
    entries = [{"Content-Type": "application/zip"}, {"tk:content": "<html/>"}]
    put.answer = response(200, json.dumps(entries))
    assert recursive_metadata(b"") == entries


def test_a_failed_parse_carries_tikas_status(put):
    put.answer = response(
        503, json.dumps({"status": "TIMEOUT", "message": "Task timed out"})
    )
    with pytest.raises(TikaError) as raised:
        recursive_metadata(b"")
    assert raised.value.http_status == 503
    assert raised.value.tika_status == "TIMEOUT"
    assert raised.value.message == "Task timed out"


def test_a_plain_text_refusal_is_still_an_error(put):
    # a body over `maxRequestSizeBytes` is refused by a filter before the JSON error handling
    put.answer = response(413, "Request body too large")
    with pytest.raises(TikaError) as raised:
        recursive_metadata(b"")
    assert raised.value.http_status == 413
    assert raised.value.tika_status is None
    assert raised.value.message == "Request body too large"
