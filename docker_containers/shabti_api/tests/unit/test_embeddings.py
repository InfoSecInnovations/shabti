"""Talking to the embeddings endpoint, without one to talk to.

`create_embeddings` is a blocking round trip to llama.cpp, so everything here stubs `requests` and
asserts on what would have gone over the wire. The live server is covered by the smoke test in
`security_disabled`; this file is about the branches that a working server never reaches.
"""

import pytest
import requests
from shabti_types import (
    EmbeddingsConfigError,
    EmbeddingsError,
    ModelNotFoundError,
)

from ...src.app.functionality import embeddings
from ...src.app.functionality.embeddings import (
    context_limit,
    count_tokens,
    create_embeddings,
    get_embeddings_model_id,
    get_json,
    get_vector_dimension,
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
    # the session is what the module actually calls through, and it is held per thread so that
    # the splitter can keep one connection open across a document's worth of /tokenize calls
    monkeypatch.setattr(embeddings, "session", lambda: recorder)
    get_vector_dimension.cache_clear()
    embeddings._limits.clear()
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


def test_counting_tokens_asks_the_model_itself(calls):
    # the only tokenizer guaranteed to match the loaded model, which is what makes the embeddings
    # model swappable: the GGUF repositories carry no token config to download instead
    calls.post_response = FakeResponse({"tokens": [1, 2, 3, 4]})
    assert count_tokens("some text", "embed-1") == 4
    assert calls.posts == [
        {
            "url": f"http://{LLM_HOST}:11434/tokenize",
            "json": {
                "content": "some text",
                "model": "embed-1",
                # /v1/embeddings adds the special tokens before measuring an input against the
                # batch size, so a chunk sized without them here would not fit there
                "add_special": True,
            },
        }
    ]


def test_a_failed_token_count_is_reported(calls):
    calls.post_response = FakeResponse("no such model", 404)
    with pytest.raises(EmbeddingsError) as raised:
        count_tokens("some text", "embed-1")
    assert raised.value.upstream_status == 404
    assert "counting tokens failed" in raised.value.message


def test_the_vector_dimension_is_measured_from_a_real_embedding(calls):
    # llama.cpp only reports n_embd for a model it is currently running, and a collection can be
    # created before anything has been embedded, so the model is asked rather than told
    calls.post_response = FakeResponse(vectors([0.1] * 384))
    assert get_vector_dimension() == 384
    assert calls.posts[0]["url"] == f"http://{LLM_HOST}:11434/v1/embeddings"


def test_the_vector_dimension_is_only_measured_once(calls):
    calls.post_response = FakeResponse(vectors([0.1] * 384))
    assert get_vector_dimension() == get_vector_dimension()
    # one listing and one embedding, not one of each per caller: every collection creation asks
    assert len(calls.posts) == 1


def test_a_connection_closed_by_the_server_is_retried(monkeypatch):
    """llama.cpp closes a connection after a fixed number of requests on it.

    Sizing one page's chunks asks for several times that, so this isn't a rare recovery path: it
    is what happens partway through any document big enough to matter.
    """
    monkeypatch.setenv("LLM_HOST", LLM_HOST)
    sessions = []

    class DeadFirstTime:
        def __init__(self, fails):
            self.fails = fails
            self.closed = False

        def post(self, url, json=None, **kwargs):
            if self.fails:
                self.fails = False
                raise requests.exceptions.ConnectionError(
                    "Remote end closed connection"
                )
            return FakeResponse({"tokens": [1, 2, 3]})

        def close(self):
            self.closed = True

    def session():
        if not sessions or sessions[-1].closed:
            sessions.append(DeadFirstTime(fails=not sessions))
        return sessions[-1]

    monkeypatch.setattr(embeddings, "session", session)
    monkeypatch.setattr(embeddings._local, "session", None, raising=False)
    assert count_tokens("some text", "embed-1") == 3
    # the dead one is closed rather than left to the garbage collector, and a new one takes over
    assert len(sessions) == 2
    assert sessions[0].closed


def test_a_connection_that_stays_dead_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_HOST", LLM_HOST)

    class AlwaysDead:
        def post(self, url, json=None, **kwargs):
            raise requests.exceptions.ConnectionError("llama.cpp is gone")

        def close(self):
            pass

    monkeypatch.setattr(embeddings, "session", lambda: AlwaysDead())
    # one retry, not a loop: a model server that is actually down should say so rather than have
    # every chunk of every document wait on it
    with pytest.raises(requests.exceptions.ConnectionError):
        count_tokens("some text", "embed-1")


def test_the_context_limit_comes_from_the_model_listing(calls):
    # llama.cpp refuses an input longer than the model was trained on, so this is the ceiling a
    # configured chunk size has to stay under
    calls.get_response = FakeResponse(
        {"data": [dict(model("embed-1", "embeddings"), meta={"n_ctx_train": 512})]}
    )
    assert context_limit("embed-1") == 512


def test_an_unloaded_model_has_no_context_limit_yet(calls):
    # the router only reports `meta` for a model it currently has running, which is a "not known
    # yet" rather than an error: there is simply nothing to check against
    calls.get_response = FakeResponse({"data": [model("embed-1", "embeddings")]})
    assert context_limit("embed-1") is None


def test_not_knowing_the_context_limit_is_not_remembered(calls):
    calls.get_response = FakeResponse({"data": [model("embed-1", "embeddings")]})
    assert context_limit("embed-1") is None
    # caching the miss would turn the check off for the life of the process, so a model that has
    # since been loaded has to be able to answer
    calls.get_response = FakeResponse(
        {"data": [dict(model("embed-1", "embeddings"), meta={"n_ctx_train": 512})]}
    )
    assert context_limit("embed-1") == 512


def test_a_known_context_limit_is_only_looked_up_once(calls):
    calls.get_response = FakeResponse(
        {"data": [dict(model("embed-1", "embeddings"), meta={"n_ctx_train": 512})]}
    )
    assert context_limit("embed-1") == 512
    assert context_limit("embed-1") == 512
    assert len(calls.gets) == 1


def test_a_vector_of_nulls_names_the_model_rather_than_being_indexed(calls):
    # llama.cpp writes NaN as JSON null, which is what a quantisation the hardware cannot compute
    # produces. it answers 200 and the length is right, so nothing downstream notices until
    # OpenSearch refuses the vector part way through an ingest
    calls.post_response = FakeResponse(vectors([None, None]))
    with pytest.raises(EmbeddingsConfigError) as raised:
        create_embeddings("some text", "embed-1")
    assert "embed-1" in raised.value.message
    assert raised.value.model == "embed-1"
    # the installation is pointed at a model it cannot use; nothing the caller sent is wrong
    assert raised.value.status == 500


def test_one_bad_value_in_a_batch_is_enough(calls):
    # a whole page embeds in one call, and a vector that is only partly numbers is no more
    # indexable than one that is none of them
    calls.post_response = FakeResponse(vectors([0.1, 0.2], [0.3, None]))
    with pytest.raises(EmbeddingsConfigError):
        create_embeddings(["first chunk", "second chunk"], "embed-1")


def test_an_infinite_value_is_refused_too(calls):
    calls.post_response = FakeResponse(vectors([0.1, float("inf")]))
    with pytest.raises(EmbeddingsConfigError):
        create_embeddings("some text", "embed-1")


def test_the_model_is_named_even_when_the_caller_did_not(calls):
    # the ingest path passes the id in, the dimension probe does not, and a message that said
    # "None returned a vector that is not made of numbers" would name nothing to go and change
    calls.post_response = FakeResponse(vectors([None, None]))
    with pytest.raises(EmbeddingsConfigError) as raised:
        create_embeddings("some text")
    assert "embed-1" in raised.value.message


def test_a_good_vector_is_returned_unchanged(calls):
    calls.post_response = FakeResponse(vectors([0.1, -0.2, 0.0]))
    assert create_embeddings("some text", "embed-1") == [0.1, -0.2, 0.0]
