"""Talking to the reranking endpoint, without one to talk to.

`rerank` is a blocking round trip to llama.cpp, so these stub `send` and assert on what would have
gone over the wire.
"""

import pytest

from ...src.app.functionality import rerank as rerank_module
from ...src.app.functionality.rerank import RerankError, rerank

LLM_HOST = "llm-host-under-test"
URL = f"http://{LLM_HOST}:11434/v1/rerank"


class FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self.status_code = status_code
        self._body = body
        self.text = str(body)
        self.headers = headers or {}

    def json(self):
        return self._body


class Calls:
    """The requests that were made, and what each one is answered with."""

    def __init__(self):
        self.posts = []
        self.response = None

    def send(self, method, url, json=None, **kwargs):
        self.posts.append({"method": method, "url": url, "json": json})
        if callable(self.response):
            return self.response(json)
        return self.response


@pytest.fixture(autouse=True)
def installation(monkeypatch):
    monkeypatch.setenv("LLM_HOST", LLM_HOST)


@pytest.fixture
def calls(monkeypatch):
    recorder = Calls()
    recorder.response = FakeResponse(
        {"results": [{"index": 0, "relevance_score": 0.5}]}
    )
    monkeypatch.setattr(rerank_module, "send", recorder.send)
    return recorder


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(rerank_module.time, "sleep", waited.append)
    return waited


def responses(*items):
    """Answers each request with the next of these, in order."""
    queue = list(items)
    return lambda body: queue.pop(0)


def test_the_documents_are_sent_to_the_reranker_with_the_query(calls):
    calls.response = FakeResponse(
        {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.1},
            ]
        }
    )
    assert rerank("what is a shabti", ["a figure", "a tomb"], "local/rerank-1") == [
        0.9,
        0.1,
    ]
    assert calls.posts == [
        {
            "method": "post",
            "url": URL,
            "json": {
                "model": "rerank-1",
                "query": "what is a shabti",
                "documents": ["a figure", "a tomb"],
                "top_n": 2,
            },
        }
    ]


def test_scores_come_back_in_the_order_the_documents_were_given(calls):
    # llama.cpp sorts its results by score, so the first one is the best match, not document 0
    calls.response = FakeResponse(
        {
            "results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.4},
                {"index": 1, "relevance_score": 0.1},
            ]
        }
    )
    assert rerank("query", ["a", "b", "c"], "local/rerank-1") == [0.4, 0.1, 0.9]


def test_empty_documents_make_no_request(calls):
    assert rerank("query", [], "local/rerank-1") == []
    assert not calls.posts


def test_a_remote_model_is_refused(calls):
    with pytest.raises(RerankError, match="only local rerankers are supported"):
        rerank("query", ["a"], "acme/rerank-1")
    assert not calls.posts


def test_a_failed_request_is_reported(calls):
    calls.response = FakeResponse("no such model", 400)
    with pytest.raises(RerankError, match="no such model"):
        rerank("query", ["a"], "local/rerank-1")
    assert len(calls.posts) == 1


def test_a_model_still_loading_is_waited_out(calls, sleeps):
    calls.response = responses(
        FakeResponse("Loading model", 503),
        FakeResponse({"results": [{"index": 0, "relevance_score": 0.7}]}),
    )
    assert rerank("query", ["a"], "local/rerank-1") == [0.7]
    assert len(calls.posts) == 2
    assert sleeps == [1]


def test_a_200_without_results_is_reported(calls):
    calls.response = FakeResponse({"error": "no model loaded"})
    with pytest.raises(RerankError, match="no results"):
        rerank("query", ["a"], "local/rerank-1")


def test_a_200_that_leaves_a_document_unscored_is_reported(calls):
    calls.response = FakeResponse({"results": [{"index": 0, "relevance_score": 0.7}]})
    with pytest.raises(RerankError):
        rerank("query", ["a", "b"], "local/rerank-1")
