import math
import requests
import threading
from contextlib import suppress
from functools import cache
from shabti_types import EmbeddingsConfigError, EmbeddingsError, ModelNotFoundError
from .models import llm_url

# the splitter sizes every candidate chunk through /tokenize, which is hundreds of calls for one
# page, so the connection is held open across them rather than dialled per call. one session per
# thread because `requests.Session` isn't thread safe and several documents ingest at once
_local = threading.local()

# what each model's trained context turned out to be, once something has had it loaded
_limits: dict[str, int] = {}


def session() -> requests.Session:
    existing = getattr(_local, "session", None)
    if existing is None:
        existing = _local.session = requests.Session()
    return existing


def send(method: str, path: str, **kwargs) -> requests.Response:
    """A call to llama.cpp over the thread's kept-alive connection, retried once if it was closed.

    llama.cpp closes a connection after a fixed number of requests on it, and sizing one page's
    chunks can ask for several times that, so finding a dead connection in the pool is an ordinary
    part of using it rather than a failure. `requests` won't retry a POST itself, since in general
    one isn't safe to repeat; these are reads whatever the method says.
    """
    try:
        return getattr(session(), method)(llm_url(path), **kwargs)
    except requests.exceptions.ConnectionError:
        with suppress(Exception):
            session().close()
        _local.session = None
        return getattr(session(), method)(llm_url(path), **kwargs)


def get_json(response: requests.Response, action: str):
    if response.status_code >= 400:
        raise EmbeddingsError(
            message=f"{action} failed: {response.text}",
            upstream_status=response.status_code,
        )
    body = response.json()
    if "data" not in body:  # the endpoint can report errors with a 200 status
        raise EmbeddingsError(
            message=f"{action} returned no data: {response.text}",
            upstream_status=response.status_code,
        )
    return body


def get_embeddings_model_id():
    models_data = get_json(
        send("get", "/models"),
        "listing models",
    )
    embeddings_model_data = next(
        (x for x in models_data["data"] if "embeddings" in x["tags"]), None
    )
    if not embeddings_model_data:
        raise ModelNotFoundError()
    return embeddings_model_data["id"]


def context_limit(model_id: str) -> int | None:
    """The longest input this model was trained to take, or None if it can't be known yet.

    llama.cpp asserts that a non-causal model's whole input fits one physical batch, and caps a
    slot's context at this number, so a chunk longer than it cannot be embedded at all. It is only
    reported for a model the router currently has running - an unloaded one carries no `meta` -
    which is not an error, just nothing to check against until something has loaded it.

    Cached by hand rather than with `functools.cache` because "not known yet" has to stay askable:
    remembering it would mean a lookup made before anything loaded the model turned the check off
    for the life of the process.
    """
    known = _limits.get(model_id)
    if known is not None:
        return known
    models_data = get_json(send("get", "/models"), "listing models")
    model = next((x for x in models_data["data"] if x["id"] == model_id), None)
    limit = (model or {}).get("meta", {}).get("n_ctx_train")
    if limit:
        _limits[model_id] = limit
    return limit


def count_tokens(text: str, model_id: str) -> int:
    """How many tokens the loaded model makes of this text, including its special tokens.

    Asking llama.cpp rather than tokenizing here is what lets the embeddings model be swapped: it
    is the only tokenizer that is guaranteed to be the model's own, and the GGUF repositories the
    models are loaded from carry no token config to download instead. `add_special` matches what
    /v1/embeddings does to an input before measuring it against the batch size, so a chunk sized
    to fit here fits there.
    """
    response = send(
        "post",
        "/tokenize",
        json={"content": text, "model": model_id, "add_special": True},
    )
    if response.status_code >= 400:
        raise EmbeddingsError(
            message=f"counting tokens failed: {response.text}",
            upstream_status=response.status_code,
        )
    return len(response.json()["tokens"])


@cache
def get_vector_dimension() -> int:
    """The embeddings model's output size, measured rather than declared.

    An index built at one dimension cannot accept vectors of another, so this has to be the real
    number. llama.cpp reports `n_embd` only for a model it is currently running, and a collection
    can be created on a stack that has not embedded anything yet, so the model is asked to embed
    something instead. `cache` doesn't cache exceptions, so a failure here is retried rather than
    remembered.
    """
    return len(create_embeddings("dimension probe", get_embeddings_model_id()))


def checked(vector, model_id: str):
    """A vector, once it is known to be made of numbers.

    A model can answer with a 200 and hand back a vector of nulls: llama.cpp writes NaN as JSON
    null, which is what a quantisation the hardware cannot compute produces. Nothing downstream
    notices - the dimension probe measures the length, which is right whatever the contents are -
    so it reaches OpenSearch, which rejects it as a malformed knn_vector part way through an
    ingest, and the reason never gets near the person who chose the model.

    The pass costs a few hundred comparisons against a network round trip and a forward pass, and
    `all` stops at the first bad value. The type check comes first because `math.isfinite(None)`
    raises rather than returning False.
    """
    if vector and all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in vector
    ):
        return vector
    raise EmbeddingsConfigError(
        model=model_id,
        # a 500 rather than a 502: the server is answering, it is the model this installation was
        # pointed at that cannot be used, which is the thing to change
        message=(
            f"{model_id} returned a vector that is not made of numbers. The model is loaded but "
            "cannot be computed as quantised - try a different quantisation of it."
        ),
    )


def create_embeddings(text, model_id: str | None = None):
    # don't try to do embeddings on empty values
    if not isinstance(text, list) and not text.strip():
        return None
    if not text:
        return []
    data = {
        # a caller embedding many pages looks the model up once and passes it in: the listing is a
        # round trip of its own, and an ingest would otherwise pay for one per page
        "model": model_id or get_embeddings_model_id(),
        "input": text,
        "encoding_format": "float",
    }
    response = get_json(
        send("post", "/v1/embeddings", json=data),
        "creating embeddings",
    )
    model = data["model"]
    if not isinstance(text, list):
        return checked(response["data"][0]["embedding"], model)
    return [checked(x["embedding"], model) for x in response["data"]]
