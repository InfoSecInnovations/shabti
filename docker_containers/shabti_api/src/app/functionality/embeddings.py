import math
import os
import numpy as np
import requests
import threading
import tiktoken
import time
from contextlib import nullcontext, suppress
from functools import cache
from shabti_types import (
    EmbeddingsConfigError,
    EmbeddingsError,
    ModelNotFoundError,
    ProviderUnavailableError,
)
from tokenizers import Tokenizer
from . import embeddings_config
from .embeddings_config import (
    batch_size,
    context_limit_setting,
    extra_body,
    max_concurrency,
    tokenizer,
)
from .providers import models_dev
from .providers.config import LOCAL, Provider, providers_config
from .providers.discovery import request_headers
from .providers.errors import provider_error, upstream_detail
from .providers.keys import shared_key
from .providers.registry import get_provider, local_url, qualify, split_model_id

# the splitter sizes every candidate chunk through /tokenize, which is hundreds of calls for one
# page, so the connection is held open across them rather than dialled per call. one session per
# thread because `requests.Session` isn't thread safe and several documents ingest at once
_local = threading.local()

# what each local model's trained context turned out to be, once something has had it loaded
_limits: dict[str, int] = {}

# finite, so a provider that stops answering fails the page rather than holding the ingest for
# ever. the read allows for a CPU-only llama.cpp working through a large batch
TIMEOUT = (10, 300)

# a provider that is rate limiting or briefly overloaded says so, and is waited out rather than
# failing the document. llama.cpp answers 503 while it loads a model, which is the same thing
RETRY_STATUSES = (429, 503)
MAX_TRIES = 5
BACKOFF_BASE = 1
BACKOFF_CAP = 30
RETRY_AFTER_CAP = 60

# what `estimate` takes a token to be. on the short side for English, so a chunk sized by it is
# smaller than it needed to be rather than too long for the model
ESTIMATE_CHARS_PER_TOKEN = 3

# every ingest embeds at once, so a provider's concurrency limit is held across all their threads
_semaphores: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_semaphores_lock = threading.Lock()


def session() -> requests.Session:
    existing = getattr(_local, "session", None)
    if existing is None:
        existing = _local.session = requests.Session()
    return existing


def send(method: str, url: str, **kwargs) -> requests.Response:
    """A call over the thread's kept-alive connection, retried once if it was closed.

    llama.cpp closes a connection after a fixed number of requests on it, and sizing one page's
    chunks can ask for several times that, so finding a dead connection in the pool is an ordinary
    part of using it rather than a failure. `requests` won't retry a POST itself, since in general
    one isn't safe to repeat; these are reads whatever the method says.
    """
    # never redirected: a provider's key would follow the request wherever it was sent
    kwargs = {"timeout": TIMEOUT, "allow_redirects": False, **kwargs}
    try:
        return getattr(session(), method)(url, **kwargs)
    except requests.exceptions.ConnectionError:
        with suppress(Exception):
            session().close()
        _local.session = None
        return getattr(session(), method)(url, **kwargs)


def resolve(model_id: str) -> tuple[Provider, str]:
    """The provider a qualified model id names, and the model's id at that provider."""
    provider_id, provider_model = split_model_id(model_id)
    return get_provider(provider_id), provider_model


def get_json(
    response: requests.Response,
    action: str,
    provider: Provider | None = None,
    model_id: str = "",
    key: str | None = None,
):
    if response.status_code >= 400:
        if provider is not None and provider.id != LOCAL:
            raise provider_error(
                response.status_code,
                model_id,
                purpose="embeddings",
                # without the key, which a provider can echo back
                detail=upstream_detail(response.text, key),
            )
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


def get_embeddings_model_id() -> str:
    """The qualified id of the model this installation embeds with.

    The installer names a provider's model in the providers file. Without one it is whichever
    local model carries the embeddings tag.
    """
    configured = providers_config().embeddings
    if configured:
        return configured.model
    # raises for an installation with no llama.cpp, which then has nothing to embed with
    get_provider(LOCAL)
    models_data = get_json(send("get", local_url("/models")), "listing models")
    embeddings_model_data = next(
        (x for x in models_data["data"] if "embeddings" in x["tags"]), None
    )
    if not embeddings_model_data:
        raise ModelNotFoundError()
    return qualify(LOCAL, embeddings_model_data["id"])


def context_limit(model_id: str) -> int | None:
    """The longest input this model takes, or None if it can't be known yet.

    The admin's setting comes first. For a provider's model it is then the override the admin gave,
    then what models.dev knows.

    llama.cpp asserts that a non-causal model's whole input fits one physical batch, and caps a
    slot's context at its trained context, so a chunk longer than that cannot be embedded at all.
    It is only reported for a model the router currently has running - an unloaded one carries no
    `meta` - which is not an error, just nothing to check against until something has loaded it.

    Cached by hand rather than with `functools.cache` because "not known yet" has to stay askable:
    remembering it would mean a lookup made before anything loaded the model turned the check off
    for the life of the process.
    """
    configured = context_limit_setting(model_id)
    if configured:
        return configured
    provider, provider_model = resolve(model_id)
    if provider.id != LOCAL:
        override = provider.overrides.get(provider_model, {}).get("context_length")
        entry = models_dev.lookup(provider.base_url, provider_model) or {}
        limit = override or entry.get("limit", {}).get("context")
        return int(limit) if limit else None
    known = _limits.get(model_id)
    if known is not None:
        return known
    models_data = get_json(send("get", local_url("/models")), "listing models")
    model = next((x for x in models_data["data"] if x["id"] == provider_model), None)
    limit = (model or {}).get("meta", {}).get("n_ctx_train")
    if limit:
        _limits[model_id] = limit
    return limit


def count_tokens(text: str, model_id: str) -> int:
    """How many tokens this model makes of the text, measured however its settings say.

    - `llama_cpp` asks llama.cpp, the only tokenizer guaranteed to be a local model's own: the GGUF
      repositories the models are loaded from carry no token config to download instead.
    - `tiktoken:<encoding>` uses one of the encodings baked into the image.
    - `hf:<repo>` uses the tokenizer.json the configurator downloaded from that repository.
    - `estimate` counts characters, for a provider whose tokenizer isn't published.
    """
    kind, _, argument = tokenizer(model_id).partition(":")
    if kind == "llama_cpp":
        return llama_cpp_tokens(text, split_model_id(model_id)[1])
    if kind == "tiktoken":
        # special tokens in a document are just text, rather than something to refuse
        return len(tiktoken_encoding(argument).encode(text, disallowed_special=()))
    if kind == "hf":
        return len(hf_tokenizer(model_id, argument).encode(text).ids)
    if kind == "estimate":
        return math.ceil(len(text) / ESTIMATE_CHARS_PER_TOKEN)
    raise EmbeddingsConfigError(
        model=model_id, message=f"{model_id} has an unknown tokenizer: {kind}"
    )


def llama_cpp_tokens(text: str, provider_model: str) -> int:
    # `add_special` matches what /v1/embeddings does to an input before measuring it against the
    # batch size, so a chunk sized to fit here fits there
    response = send(
        "post",
        local_url("/tokenize"),
        json={"content": text, "model": provider_model, "add_special": True},
    )
    if response.status_code >= 400:
        raise EmbeddingsError(
            message=f"counting tokens failed: {response.text}",
            upstream_status=response.status_code,
        )
    return len(response.json()["tokens"])


@cache
def tiktoken_encoding(name: str):
    return tiktoken.get_encoding(name)


def hf_tokenizer_path(repo: str) -> str:
    # next to the settings file, where the configurator downloads it to
    return os.path.join(
        os.path.dirname(embeddings_config.CONFIG_PATH),
        "tokenizers",
        repo.replace("/", "--") + ".json",
    )


def hf_tokenizer(model_id: str, repo: str) -> Tokenizer:
    path = hf_tokenizer_path(repo)
    if not repo or not os.path.isfile(path):
        raise EmbeddingsConfigError(
            model=model_id,
            message=(
                f"the tokenizer for {model_id} isn't at {path}. "
                "Reinstall Shabti to download it."
            ),
        )
    return load_tokenizer(path)


@cache
def load_tokenizer(path: str) -> Tokenizer:
    return Tokenizer.from_file(path)


@cache
def get_vector_dimension() -> int:
    """The embeddings model's output size, measured rather than declared.

    An index built at one dimension cannot accept vectors of another, so this has to be the real
    number. llama.cpp reports `n_embd` only for a model it is currently running, a provider doesn't
    report it at all, and a collection can be created on a stack that has not embedded anything
    yet, so the model is asked to embed something instead. `cache` doesn't cache exceptions, so a failure here is retried rather than
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


def mean_vector(vectors: list[list[float]], weights: list[float]) -> list[float]:
    """One vector standing for several, as their weighted average direction.

    Each is normalised first so that it counts by its weight rather than by its length, and the
    result is normalised again so it looks like any other embedding. A mean rather than an
    element-wise max: a feature in an embedding is a direction rather than one coordinate, and
    taking each coordinate's largest value throws away every negative one and drags the result
    towards a direction everything shares, which measurably retrieves worse.
    """
    matrix = np.asarray(vectors, dtype=float)
    unit = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    mean = np.average(unit, axis=0, weights=weights)
    # a plain list, because it goes into a JSON request body
    return (mean / np.linalg.norm(mean)).tolist()


def configured_dimensions(model_id: str) -> int | None:
    configured = providers_config().embeddings
    if configured and configured.model == model_id:
        return configured.dimensions
    return None


def limiter(model_id: str):
    limit = max_concurrency(model_id)
    if not limit:
        return nullcontext()
    with _semaphores_lock:
        return _semaphores.setdefault(
            (model_id, limit), threading.BoundedSemaphore(limit)
        )


def retry_delay(response: requests.Response, attempt: int) -> float:
    """How long to wait before trying again: what the provider asked for, within reason.

    A `Retry-After` given as a date rather than seconds is treated as not given.
    """
    try:
        seconds = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        seconds = min(BACKOFF_BASE * 2**attempt, BACKOFF_CAP)
    return min(max(seconds, 0.0), RETRY_AFTER_CAP)


def request_embeddings(provider: Provider, body: dict, model_id: str) -> dict:
    key = shared_key(provider)
    headers = request_headers(provider, key)
    for attempt in range(MAX_TRIES):
        try:
            with limiter(model_id):
                response = send(
                    "post",
                    f"{provider.base_url}/embeddings",
                    json=body,
                    headers=headers,
                )
        except requests.exceptions.RequestException:
            if provider.id == LOCAL:
                raise
            raise ProviderUnavailableError(model_id) from None
        if response.status_code not in RETRY_STATUSES or attempt == MAX_TRIES - 1:
            break
        # outside the limiter, so a request waiting out a rate limit doesn't hold a slot
        time.sleep(retry_delay(response, attempt))
    return get_json(response, "creating embeddings", provider, model_id, key)


def embedded(response: dict, model_id: str) -> list:
    # in the order they were asked for, which the response only promises through `index`
    data = sorted(response["data"], key=lambda x: x.get("index", 0))
    return [checked(x["embedding"], model_id) for x in data]


def create_embeddings(text, model_id: str | None = None):
    # don't try to do embeddings on empty values
    if not isinstance(text, list) and not text.strip():
        return None
    if not text:
        return []
    # a caller embedding many pages looks the model up once and passes it in: the listing is a
    # round trip of its own, and an ingest would otherwise pay for one per page
    model_id = model_id or get_embeddings_model_id()
    provider, provider_model = resolve(model_id)
    # the admin's extra fields can't replace the ones the request is made of
    body = {**extra_body(model_id), "model": provider_model, "encoding_format": "float"}
    if dimensions := configured_dimensions(model_id):
        body["dimensions"] = dimensions
    if not isinstance(text, list):
        response = request_embeddings(provider, {**body, "input": text}, model_id)
        return embedded(response, model_id)[0]
    size = batch_size(model_id) or len(text)
    vectors = []
    for start in range(0, len(text), size):
        batch = text[start : start + size]
        response = request_embeddings(provider, {**body, "input": batch}, model_id)
        vectors.extend(embedded(response, model_id))
    return vectors
