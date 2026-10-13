"""Talking to the embeddings endpoint, without one to talk to.

`create_embeddings` is a blocking round trip to llama.cpp or a provider, so everything here stubs
`requests` and asserts on what would have gone over the wire. The live server is covered by the
smoke test in `security_disabled`; this file is about the branches that a working server never
reaches.
"""

import json
import math
import threading
import time

import pytest
import requests
from shabti_types import (
    EmbeddingsConfigError,
    EmbeddingsError,
    ModelNotFoundError,
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

from ...src.app.functionality import embeddings, embeddings_config
from ...src.app.functionality.embeddings import (
    MAX_TRIES,
    RETRY_AFTER_CAP,
    context_limit,
    count_tokens,
    create_embeddings,
    get_embeddings_model_id,
    get_json,
    get_vector_dimension,
    mean_vector,
)
from ...src.app.functionality.providers import config as config_module
from ...src.app.functionality.providers import models_dev as models_dev_module
from ...src.app.functionality.providers import secret_files

LLM_HOST = "llm-host-under-test"
REMOTE = "acme/text-embed"
REMOTE_URL = "https://api.acme.test/v1"


class FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self.status_code = status_code
        self._body = body
        self.text = str(body)
        self.headers = headers or {}

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
        # a response, or something that makes one from the request body
        self.post_response = None

    def get(self, url, **kwargs):
        self.gets.append(url)
        return self.get_response

    def post(self, url, json=None, **kwargs):
        self.posts.append({"url": url, "json": json})
        self.headers = kwargs.get("headers")
        if callable(self.post_response):
            return self.post_response(json)
        return self.post_response


@pytest.fixture(autouse=True)
def installation(tmp_path, monkeypatch):
    """No providers, keys or model settings beyond what a test writes for itself."""
    monkeypatch.setenv("LLM_HOST", LLM_HOST)
    monkeypatch.setattr(config_module, "CONFIG_PATH", str(tmp_path / "providers.json"))
    monkeypatch.setattr(secret_files, "SECRETS_DIR", str(tmp_path))
    monkeypatch.setattr(
        embeddings_config, "CONFIG_PATH", str(tmp_path / "shabti-models.json")
    )
    get_vector_dimension.cache_clear()
    embeddings._limits.clear()
    embeddings._semaphores.clear()

    def write(providers=None, settings=None):
        if providers is not None:
            (tmp_path / "providers.json").write_text(json.dumps(providers))
            (tmp_path / "shabti_provider_keys.json").write_text(
                json.dumps({"acme": "sk-1"})
            )
        if settings is not None:
            (tmp_path / "shabti-models.json").write_text(json.dumps(settings))

    return write


def remote_provider(**embeddings_config_fields):
    return {
        "providers": [
            {
                "id": "acme",
                "name": "Acme",
                "base_url": REMOTE_URL,
                "headers": {"X-Org": "org-1"},
                "models": {"overrides": {}},
            }
        ],
        "embeddings": {"model": REMOTE, **embeddings_config_fields},
    }


@pytest.fixture
def calls(monkeypatch):
    recorder = Calls()
    # the model listing and the embeddings call have separate canned responses, so a test that
    # cares about only one of them doesn't have to set up the other
    recorder.get_response = FakeResponse({"data": [model("embed-1", "embeddings")]})
    recorder.post_response = FakeResponse(vectors([0.1, 0.2]))
    # the session is what the module actually calls through, and it is held per thread so that
    # the splitter can keep one connection open across a document's worth of /tokenize calls
    monkeypatch.setattr(embeddings, "session", lambda: recorder)
    return recorder


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(embeddings.time, "sleep", waited.append)
    return waited


@pytest.fixture
def remote(installation, calls):
    installation(
        providers=remote_provider(),
        settings={REMOTE: {"chunk_size": 512}},
    )
    return calls


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
    assert get_embeddings_model_id() == "local/embed-1"
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
    assert create_embeddings("some text", "local/embed-1") == [0.1, 0.2, 0.3]
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
    assert create_embeddings(["a", "b", "c"], "local/embed-1") == [[0.1], [0.2], [0.3]]
    assert calls.posts[0]["json"]["input"] == ["a", "b", "c"]


def test_a_list_of_one_still_embeds_to_a_list(calls):
    # the return shape follows the input's type, not the number of results, or a caller zipping
    # chunks against vectors would silently pair a chunk with a float
    calls.post_response = FakeResponse(vectors([0.1, 0.2]))
    assert create_embeddings(["only"], "local/embed-1") == [[0.1, 0.2]]


def test_a_given_model_id_is_not_looked_up_again(calls):
    create_embeddings("some text", "local/embed-1")
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
        create_embeddings("some text", "local/embed-1")
    assert raised.value.upstream_status == 500
    assert "creating embeddings failed" in raised.value.message


def test_counting_tokens_asks_the_model_itself(calls):
    # the only tokenizer guaranteed to match the loaded model, which is what makes the embeddings
    # model swappable: the GGUF repositories carry no token config to download instead
    calls.post_response = FakeResponse({"tokens": [1, 2, 3, 4]})
    assert count_tokens("some text", "local/embed-1") == 4
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
        count_tokens("some text", "local/embed-1")
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
    assert count_tokens("some text", "local/embed-1") == 3
    # the dead one is closed rather than left to the garbage collector, and a new one takes over
    assert len(sessions) == 2
    assert sessions[0].closed


def test_a_connection_that_stays_dead_is_reported(monkeypatch):
    class AlwaysDead:
        def post(self, url, json=None, **kwargs):
            raise requests.exceptions.ConnectionError("llama.cpp is gone")

        def close(self):
            pass

    monkeypatch.setattr(embeddings, "session", lambda: AlwaysDead())
    # one retry, not a loop: a model server that is actually down should say so rather than have
    # every chunk of every document wait on it
    with pytest.raises(requests.exceptions.ConnectionError):
        count_tokens("some text", "local/embed-1")


def test_the_context_limit_comes_from_the_model_listing(calls):
    # llama.cpp refuses an input longer than the model was trained on, so this is the ceiling a
    # configured chunk size has to stay under
    calls.get_response = FakeResponse(
        {"data": [dict(model("embed-1", "embeddings"), meta={"n_ctx_train": 512})]}
    )
    assert context_limit("local/embed-1") == 512


def test_an_unloaded_model_has_no_context_limit_yet(calls):
    # the router only reports `meta` for a model it currently has running, which is a "not known
    # yet" rather than an error: there is simply nothing to check against
    calls.get_response = FakeResponse({"data": [model("embed-1", "embeddings")]})
    assert context_limit("local/embed-1") is None


def test_not_knowing_the_context_limit_is_not_remembered(calls):
    calls.get_response = FakeResponse({"data": [model("embed-1", "embeddings")]})
    assert context_limit("local/embed-1") is None
    # caching the miss would turn the check off for the life of the process, so a model that has
    # since been loaded has to be able to answer
    calls.get_response = FakeResponse(
        {"data": [dict(model("embed-1", "embeddings"), meta={"n_ctx_train": 512})]}
    )
    assert context_limit("local/embed-1") == 512


def test_a_known_context_limit_is_only_looked_up_once(calls):
    calls.get_response = FakeResponse(
        {"data": [dict(model("embed-1", "embeddings"), meta={"n_ctx_train": 512})]}
    )
    assert context_limit("local/embed-1") == 512
    assert context_limit("local/embed-1") == 512
    assert len(calls.gets) == 1


def test_a_vector_of_nulls_names_the_model_rather_than_being_indexed(calls):
    # llama.cpp writes NaN as JSON null, which is what a quantisation the hardware cannot compute
    # produces. it answers 200 and the length is right, so nothing downstream notices until
    # OpenSearch refuses the vector part way through an ingest
    calls.post_response = FakeResponse(vectors([None, None]))
    with pytest.raises(EmbeddingsConfigError) as raised:
        create_embeddings("some text", "local/embed-1")
    assert "embed-1" in raised.value.message
    assert raised.value.model == "local/embed-1"
    # the installation is pointed at a model it cannot use; nothing the caller sent is wrong
    assert raised.value.status == 500


def test_one_bad_value_in_a_batch_is_enough(calls):
    # a whole page embeds in one call, and a vector that is only partly numbers is no more
    # indexable than one that is none of them
    calls.post_response = FakeResponse(vectors([0.1, 0.2], [0.3, None]))
    with pytest.raises(EmbeddingsConfigError):
        create_embeddings(["first chunk", "second chunk"], "local/embed-1")


def test_an_infinite_value_is_refused_too(calls):
    calls.post_response = FakeResponse(vectors([0.1, float("inf")]))
    with pytest.raises(EmbeddingsConfigError):
        create_embeddings("some text", "local/embed-1")


def test_the_model_is_named_even_when_the_caller_did_not(calls):
    # the ingest path passes the id in, the dimension probe does not, and a message that said
    # "None returned a vector that is not made of numbers" would name nothing to go and change
    calls.post_response = FakeResponse(vectors([None, None]))
    with pytest.raises(EmbeddingsConfigError) as raised:
        create_embeddings("some text")
    assert "embed-1" in raised.value.message


def test_a_good_vector_is_returned_unchanged(calls):
    calls.post_response = FakeResponse(vectors([0.1, -0.2, 0.0]))
    assert create_embeddings("some text", "local/embed-1") == [0.1, -0.2, 0.0]


def test_the_mean_of_vectors_is_their_average_direction():
    assert mean_vector([[1.0, 0.0], [0.0, 1.0]], [1, 1]) == pytest.approx(
        [math.sqrt(0.5), math.sqrt(0.5)]
    )


def test_a_longer_vector_does_not_count_for_more():
    # the vectors are compared by angle, so one that happens to be longer says nothing more and
    # must not pull the average towards itself
    assert mean_vector([[10.0, 0.0], [0.0, 1.0]], [1, 1]) == pytest.approx(
        [math.sqrt(0.5), math.sqrt(0.5)]
    )


def test_a_heavier_piece_pulls_the_mean_towards_it():
    x, y = mean_vector([[1.0, 0.0], [0.0, 1.0]], [3, 1])
    assert x > y > 0


def test_the_mean_is_unit_length_like_any_other_embedding():
    vector = mean_vector(
        [[0.3, -0.4, 1.2], [0.5, 0.1, -0.2], [2.0, 2.0, 2.0]], [5, 2, 1]
    )
    assert math.isclose(math.hypot(*vector), 1.0)


def test_the_mean_is_a_plain_list_of_floats():
    # it goes into an OpenSearch request body, which can't serialise numpy's types
    vector = mean_vector([[1.0, 0.0], [0.0, 1.0]], [1, 1])
    assert isinstance(vector, list)
    assert all(isinstance(value, float) for value in vector)


def test_a_configured_model_is_used_without_asking_llama_cpp(remote):
    assert get_embeddings_model_id() == REMOTE
    assert not remote.gets


def test_without_llama_cpp_or_a_configured_model_there_is_nothing_to_embed_with(
    calls, monkeypatch
):
    monkeypatch.delenv("LLM_HOST")
    with pytest.raises(ModelNotFoundError):
        get_embeddings_model_id()
    assert not calls.gets


def test_a_remote_model_is_asked_at_its_provider_with_its_key(remote):
    create_embeddings("some text", REMOTE)
    assert remote.posts == [
        {
            "url": f"{REMOTE_URL}/embeddings",
            "json": {
                "model": "text-embed",
                "input": "some text",
                "encoding_format": "float",
            },
        }
    ]
    assert remote.headers == {"X-Org": "org-1", "Authorization": "Bearer sk-1"}


def test_the_configured_dimensions_and_extra_fields_are_sent(installation, calls):
    installation(
        providers=remote_provider(dimensions=256),
        settings={
            REMOTE: {"chunk_size": 512, "extra_body": {"input_type": "document"}}
        },
    )
    create_embeddings("some text", REMOTE)
    sent = calls.posts[0]["json"]
    assert sent["dimensions"] == 256
    assert sent["input_type"] == "document"


def test_extra_fields_cannot_replace_the_request(installation, calls):
    installation(
        providers=remote_provider(),
        settings={REMOTE: {"chunk_size": 512, "extra_body": {"model": "other"}}},
    )
    create_embeddings("some text", REMOTE)
    assert calls.posts[0]["json"]["model"] == "text-embed"


def test_a_remote_model_is_sent_its_chunks_in_batches(installation, calls):
    installation(
        providers=remote_provider(),
        settings={REMOTE: {"chunk_size": 512, "batch_size": 2}},
    )
    calls.post_response = lambda body: FakeResponse(
        vectors(*([float(len(text))] for text in body["input"]))
    )
    assert create_embeddings(["a", "bb", "ccc"], REMOTE) == [[1.0], [2.0], [3.0]]
    assert [x["json"]["input"] for x in calls.posts] == [["a", "bb"], ["ccc"]]


def test_a_remote_model_batches_by_default(remote):
    remote.post_response = lambda body: FakeResponse(
        vectors(*([0.1] for _ in body["input"]))
    )
    create_embeddings(["chunk"] * 100, REMOTE)
    assert [len(x["json"]["input"]) for x in remote.posts] == [64, 36]


def test_llama_cpp_takes_a_page_in_one_request(calls):
    calls.post_response = lambda body: FakeResponse(
        vectors(*([0.1] for _ in body["input"]))
    )
    create_embeddings(["chunk"] * 100, "local/embed-1")
    assert len(calls.posts) == 1


def test_vectors_come_back_in_the_order_they_were_asked_for(remote):
    remote.post_response = FakeResponse(
        {"data": [{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}]}
    )
    assert create_embeddings(["first", "second"], REMOTE) == [[0.1], [0.2]]


@pytest.mark.parametrize(
    "status, error",
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (402, ProviderQuotaError),
        (500, ProviderUnavailableError),
    ],
)
def test_a_remote_failure_is_reported_as_the_providers(remote, status, error):
    remote.post_response = FakeResponse("key sk-1 is not valid", status)
    with pytest.raises(error) as raised:
        create_embeddings("some text", REMOTE)
    # a provider can echo the key back, so nothing it said is passed on
    assert "sk-1" not in raised.value.message


def test_an_unreachable_provider_is_reported_as_unavailable(installation, monkeypatch):
    installation(providers=remote_provider(), settings={REMOTE: {"chunk_size": 512}})

    class Unreachable:
        def post(self, url, **kwargs):
            raise requests.exceptions.ConnectTimeout("no route")

        def close(self):
            pass

    monkeypatch.setattr(embeddings, "session", lambda: Unreachable())
    with pytest.raises(ProviderUnavailableError):
        create_embeddings("some text", REMOTE)


def responses(*items):
    """Answers each request with the next of these, in order."""
    queue = list(items)
    return lambda body: queue.pop(0)


def test_a_rate_limit_waits_as_long_as_the_provider_asks(remote, sleeps):
    remote.post_response = responses(
        FakeResponse("slow down", 429, {"Retry-After": "2"}),
        FakeResponse(vectors([0.1])),
    )
    assert create_embeddings("some text", REMOTE) == [0.1]
    assert sleeps == [2.0]


def test_an_overloaded_provider_is_backed_off_from(remote, sleeps):
    remote.post_response = responses(
        FakeResponse("busy", 503),
        FakeResponse("busy", 503),
        FakeResponse(vectors([0.1])),
    )
    assert create_embeddings("some text", REMOTE) == [0.1]
    assert sleeps == [1, 2]


def test_a_wait_the_provider_asks_for_is_capped(remote, sleeps):
    remote.post_response = responses(
        FakeResponse("slow down", 429, {"Retry-After": "3600"}),
        FakeResponse(vectors([0.1])),
    )
    create_embeddings("some text", REMOTE)
    assert sleeps == [RETRY_AFTER_CAP]


def test_a_retry_after_date_is_backed_off_from_like_none_at_all(remote, sleeps):
    remote.post_response = responses(
        FakeResponse(
            "slow down", 429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        ),
        FakeResponse(vectors([0.1])),
    )
    create_embeddings("some text", REMOTE)
    assert sleeps == [1]


def test_a_rate_limit_that_does_not_lift_is_reported(remote, sleeps):
    remote.post_response = FakeResponse("slow down", 429)
    with pytest.raises(ProviderRateLimitError):
        create_embeddings("some text", REMOTE)
    assert len(remote.posts) == MAX_TRIES
    assert len(sleeps) == MAX_TRIES - 1


def test_llama_cpp_loading_a_model_is_waited_out_too(calls, sleeps):
    calls.post_response = responses(
        FakeResponse("Loading model", 503), FakeResponse(vectors([0.1]))
    )
    assert create_embeddings("some text", "local/embed-1") == [0.1]
    assert sleeps == [1]


def in_flight_peak(installation, monkeypatch, settings) -> int:
    """How many embeddings requests were ever in flight at once, across eight threads."""
    installation(providers=remote_provider(), settings={REMOTE: settings})
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    class Slow:
        def post(self, url, **kwargs):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.02)
            with lock:
                state["now"] -= 1
            return FakeResponse(vectors([0.1]))

    monkeypatch.setattr(embeddings, "session", lambda: Slow())
    threads = [
        threading.Thread(target=create_embeddings, args=("some text", REMOTE))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return state["peak"]


def test_the_requests_to_a_provider_are_limited_across_threads(
    installation, monkeypatch
):
    settings = {"chunk_size": 512, "max_concurrency": 2}
    assert 1 <= in_flight_peak(installation, monkeypatch, settings) <= 2


def test_one_request_at_a_time_is_one_at_a_time(installation, monkeypatch):
    settings = {"chunk_size": 512, "max_concurrency": 1}
    assert in_flight_peak(installation, monkeypatch, settings) == 1


def test_the_configured_context_limit_comes_first(installation, calls):
    installation(
        providers=remote_provider(),
        settings={REMOTE: {"chunk_size": 512, "context_limit": 2048}},
    )
    assert context_limit(REMOTE) == 2048
    assert not calls.gets


def test_the_admins_override_is_the_remote_context_limit(installation, calls):
    providers = remote_provider()
    providers["providers"][0]["models"]["overrides"] = {
        "text-embed": {"context_length": 4096}
    }
    installation(providers=providers, settings={REMOTE: {"chunk_size": 512}})
    assert context_limit(REMOTE) == 4096


def test_models_dev_knows_a_remote_context_limit(remote, monkeypatch):
    monkeypatch.setattr(
        models_dev_module,
        "snapshot",
        lambda: {
            "providers": {
                "acme": {
                    "api": REMOTE_URL,
                    "models": {"text-embed": {"limit": {"context": 8191}}},
                }
            }
        },
    )
    assert context_limit(REMOTE) == 8191


def test_an_unknown_remote_context_limit_is_not_checked(remote, monkeypatch):
    monkeypatch.setattr(models_dev_module, "snapshot", lambda: {})
    assert context_limit(REMOTE) is None
    # llama.cpp has nothing to say about a provider's model
    assert not remote.gets


def test_a_local_context_limit_setting_beats_the_trained_context(installation, calls):
    installation(settings={"local/embed-1": {"chunk_size": 128, "context_limit": 256}})
    assert context_limit("local/embed-1") == 256
    assert not calls.gets


def test_an_unqualified_model_id_is_refused(calls):
    with pytest.raises(ModelNotFoundError):
        create_embeddings("some text", "embed-1")


def test_a_rate_limit_says_which_model_and_why(remote, sleeps):
    remote.post_response = FakeResponse(
        json.dumps({"error": {"message": "Rate limit exceeded: free-models-per-day"}}),
        429,
    )
    with pytest.raises(ProviderRateLimitError) as raised:
        create_embeddings("some text", REMOTE)
    assert f"(embeddings model {REMOTE})" in raised.value.message
    assert raised.value.message.endswith(": Rate limit exceeded: free-models-per-day")
