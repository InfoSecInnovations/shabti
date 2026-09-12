"""Talking to the embeddings endpoint, without one to talk to.

`create_embeddings` is a blocking round trip to llama.cpp, so everything here stubs `requests` and
asserts on what would have gone over the wire. The live server is covered by the smoke test in
`security_disabled`; this file is about the branches that a working server never reaches.
"""

import pytest
from shabti_types import EmbeddingsError, ModelNotFoundError

from ...src.app.functionality import embeddings
from ...src.app.functionality.embeddings import (
    create_embeddings,
    get_embeddings_model_id,
    get_json,
)

LLM_HOST = "llm-host-under-test"


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def model(model_id: str, *tags: str):
    return {"id": model_id, "tags": list(tags)}


def vectors(*values):
    """An embeddings response body, one entry per requested input."""
    return {"data": [{"embedding": list(value)} for value in values]}


class Calls:
    """The requests that were made, so a test can assert on one or on there being none."""

    def __init__(self):
        self.gets = []
        self.posts = []
        self.get_response = None
        self.post_response = None

    def get(self, url, **kwargs):
        self.gets.append(url)
        return self.get_response

    def post(self, url, json=None, **kwargs):
        self.posts.append({"url": url, "json": json})
        return self.post_response


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("LLM_HOST", LLM_HOST)
    recorder = Calls()
    # the model listing and the embeddings call have separate canned responses, so a test that
    # cares about only one of them doesn't have to set up the other
    recorder.get_response = FakeResponse({"data": [model("embed-1", "embeddings")]})
    recorder.post_response = FakeResponse(vectors([0.1, 0.2]))
    monkeypatch.setattr(embeddings.requests, "get", recorder.get)
    monkeypatch.setattr(embeddings.requests, "post", recorder.post)
    return recorder


def test_get_json_returns_the_body_of_a_good_response():
    assert get_json(FakeResponse({"data": [1]}), "listing models") == {"data": [1]}


def test_get_json_reports_an_error_status():
    with pytest.raises(EmbeddingsError) as raised:
        get_json(FakeResponse("upstream exploded", 503), "creating embeddings")
    assert raised.value.upstream_status == 503
    assert "creating embeddings failed" in raised.value.message
    assert "upstream exploded" in raised.value.message
    # the caller failed us, the caller of the API did not: a 502 rather than a 4xx
    assert raised.value.status == 502


def test_get_json_reports_a_body_with_no_data():
    # the endpoint can report errors with a 200 status, so the status alone is not enough
    with pytest.raises(EmbeddingsError) as raised:
        get_json(FakeResponse({"error": "no model loaded"}), "creating embeddings")
    assert raised.value.upstream_status == 200
    assert "returned no data" in raised.value.message


def test_the_embeddings_model_is_chosen_by_tag(calls):
    calls.get_response = FakeResponse(
        {"data": [model("chat-1", "chat", "default"), model("embed-1", "embeddings")]}
    )
    assert get_embeddings_model_id() == "embed-1"
    assert calls.gets == [f"http://{LLM_HOST}:11434/models"]


def test_no_embeddings_model_is_an_error(calls):
    calls.get_response = FakeResponse({"data": [model("chat-1", "chat")]})
    with pytest.raises(ModelNotFoundError):
        get_embeddings_model_id()


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_blank_text_embeds_to_nothing(calls, text):
    # a page that came out of parsing empty would otherwise cost a round trip to be told so
    assert create_embeddings(text) is None
    assert not calls.posts and not calls.gets


def test_an_empty_list_embeds_to_an_empty_list(calls):
    assert create_embeddings([]) == []
    assert not calls.posts and not calls.gets


def test_a_string_embeds_to_one_vector(calls):
    calls.post_response = FakeResponse(vectors([0.1, 0.2, 0.3]))
    assert create_embeddings("some text", "embed-1") == [0.1, 0.2, 0.3]
    assert calls.posts == [
        {
            "url": f"http://{LLM_HOST}:11434/v1/embeddings",
            "json": {
                "model": "embed-1",
                "input": "some text",
                "encoding_format": "float",
            },
        }
    ]


def test_a_list_embeds_to_a_vector_each_in_order(calls):
    calls.post_response = FakeResponse(vectors([0.1], [0.2], [0.3]))
    assert create_embeddings(["a", "b", "c"], "embed-1") == [[0.1], [0.2], [0.3]]
    assert calls.posts[0]["json"]["input"] == ["a", "b", "c"]


def test_a_list_of_one_still_embeds_to_a_list(calls):
    # the return shape follows the input's type, not the number of results, or a caller zipping
    # chunks against vectors would silently pair a chunk with a float
    calls.post_response = FakeResponse(vectors([0.1, 0.2]))
    assert create_embeddings(["only"], "embed-1") == [[0.1, 0.2]]


def test_a_given_model_id_is_not_looked_up_again(calls):
    create_embeddings("some text", "embed-1")
    # the listing is a round trip of its own, and an ingest passes the id in precisely so that it
    # pays for one per document rather than one per page
    assert not calls.gets


def test_a_missing_model_id_is_looked_up(calls):
    create_embeddings("some text")
    assert calls.gets == [f"http://{LLM_HOST}:11434/models"]
    assert calls.posts[0]["json"]["model"] == "embed-1"


def test_a_failed_embeddings_call_is_reported(calls):
    calls.post_response = FakeResponse("context length exceeded", 500)
    with pytest.raises(EmbeddingsError) as raised:
        create_embeddings("some text", "embed-1")
    assert raised.value.upstream_status == 500
    assert "creating embeddings failed" in raised.value.message
