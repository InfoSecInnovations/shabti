"""What the installer recorded about the model this installation embeds with.

llama.cpp tells us *which* model carries the embeddings tag, but not how to split text for it: the
GGUF repositories it loads from carry no token config, and a chunk size is a property of how the
model was trained rather than something the file declares. A remote provider says even less. The
configurator writes what it knows next to the preset file llama.cpp reads, keyed by the qualified
model id, and it is mounted here.

This is deliberately not passed through the environment: the model selection is written in one
place, by `writeModelsIni`, which the installer, the post-install model management page and the
test harness all already call. An environment variable would have to be plumbed separately through
each of those, and could then disagree with the preset file.
"""

import json
import os
from shabti_types import EmbeddingsConfigError
from .providers.config import LOCAL
from .providers.files import read_cached

CONFIG_PATH = os.getenv("SHABTI_MODELS_CONFIG", "/opt/shabti_models/shabti-models.json")

# a remote provider is metered and rate limited, where llama.cpp takes whatever it is given
REMOTE_BATCH_SIZE = 64
REMOTE_MAX_CONCURRENCY = 4


def read_config(model_id: str) -> dict | None:
    """The whole file, or None when there is none.

    Re-read whenever it changes rather than cached for good, so a container which started before
    the file was written picks it up without a restart.
    """
    try:
        return read_cached(CONFIG_PATH, json.loads)
    except (OSError, ValueError) as e:
        raise EmbeddingsConfigError(
            model=model_id,
            message=f"could not read the embeddings model settings at {CONFIG_PATH}: {e}",
        ) from e


def embeddings_config(model_id: str) -> dict:
    """The settings for one model, by its qualified id."""
    config = read_config(model_id)
    if config is None:
        raise EmbeddingsConfigError(
            model=model_id,
            message=(
                f"could not read the embeddings model settings at {CONFIG_PATH}: no such file"
            ),
        )
    settings = config.get(model_id)
    # `is None` rather than falsy, so a model that is listed but carries nothing is reported
    # against the setting that is missing rather than as an unknown model
    if settings is None:
        raise EmbeddingsConfigError(
            model=model_id,
            message=(
                f"{CONFIG_PATH} has no settings for {model_id}, so documents cannot be split for "
                "it. Reinstall Shabti to select an embeddings model."
            ),
        )
    return settings


def optional_settings(model_id: str) -> dict:
    """The settings for one model where every one of them has a default, so none are required.

    Only for what embedding a text needs, which works without the file: the dimension probe runs
    before anything is split.
    """
    return (read_config(model_id) or {}).get(model_id) or {}


def is_local(model_id: str) -> bool:
    return model_id.partition("/")[0] == LOCAL


def chunk_size(model_id: str) -> int:
    """How many tokens of this model's input one chunk may take up."""
    size = embeddings_config(model_id).get("chunk_size")
    if not size:
        raise EmbeddingsConfigError(
            model=model_id,
            message=f"the settings for {model_id} in {CONFIG_PATH} carry no chunk_size",
        )
    return int(size)


def _prefix(model_id: str, setting: str) -> str:
    """One of the strings this model wants in front of its input, or nothing if it wants none.

    Missing is an answer here, unlike a missing chunk size: a symmetric model takes no prefix at
    all, which is the ordinary case and not something to refuse to run over.
    """
    return str(embeddings_config(model_id).get(setting) or "")


def query_prefix(model_id: str) -> str:
    """What goes in front of a search query.

    An asymmetric model was trained with an instruction on the query and nothing on the passages,
    and embedding a query without it costs retrieval quality with nothing to report.
    """
    return _prefix(model_id, "query_prefix")


def document_prefix(model_id: str) -> str:
    """What goes in front of a chunk being stored.

    Rarer than the query prefix - it is empty for every model in the catalogue today - but models
    like e5 and nomic want one on both sides, and this has to be in place before such a model is
    selected: it is part of what a stored vector means, so introducing one later would make new
    vectors incomparable to the ones already in an index.
    """
    return _prefix(model_id, "document_prefix")


def tokenizer(model_id: str) -> str:
    """How this model's input is measured, see `embeddings.count_tokens`.

    llama.cpp can be asked for its own model's count. A remote provider has nothing to ask, and
    without a tokenizer configured for it the count is estimated.
    """
    configured = optional_settings(model_id).get("tokenizer")
    return str(configured or ("llama_cpp" if is_local(model_id) else "estimate"))


def context_limit_setting(model_id: str) -> int | None:
    """The longest input the admin says this model takes, ahead of anything discovered."""
    limit = optional_settings(model_id).get("context_limit")
    return int(limit) if limit else None


def batch_size(model_id: str) -> int | None:
    """How many chunks go in one request, or None for every chunk of a page at once."""
    size = optional_settings(model_id).get("batch_size")
    if size:
        return int(size)
    return None if is_local(model_id) else REMOTE_BATCH_SIZE


def max_concurrency(model_id: str) -> int | None:
    """How many requests may be in flight at once across every ingest, or None for no limit."""
    limit = optional_settings(model_id).get("max_concurrency")
    if limit:
        return int(limit)
    return None if is_local(model_id) else REMOTE_MAX_CONCURRENCY


def extra_body(model_id: str) -> dict:
    """Provider-specific fields added to every embeddings request."""
    return dict(optional_settings(model_id).get("extra_body") or {})
