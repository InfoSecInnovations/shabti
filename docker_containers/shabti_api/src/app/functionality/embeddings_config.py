"""What the installer recorded about the model this installation embeds with.

llama.cpp tells us *which* model carries the embeddings tag, but not how to split text for it: the
GGUF repositories it loads from carry no token config, and a chunk size is a property of how the
model was trained rather than something the file declares. The configurator writes what it knows
next to the preset file llama.cpp reads, and it is mounted here.

This is deliberately not passed through the environment: the model selection is written in one
place, by `writeModelsIni`, which the installer, the post-install model management page and the
test harness all already call. An environment variable would have to be plumbed separately through
each of those, and could then disagree with the preset file.
"""

import json
import os
from functools import cache
from shabti_types import EmbeddingsConfigError

CONFIG_PATH = os.getenv("SHABTI_MODELS_CONFIG", "/opt/shabti_models/shabti-models.json")


@cache
def embeddings_config(model_id: str) -> dict:
    """The settings for one model, by the id llama.cpp lists it under.

    Cached because it is read per document and the model cannot change under a running
    installation. `cache` doesn't cache exceptions, so a container which started before the file
    was written retries rather than failing for ever.
    """
    try:
        with open(CONFIG_PATH) as config_file:
            config = json.load(config_file)
    except (OSError, ValueError) as e:
        raise EmbeddingsConfigError(
            model=model_id,
            message=f"could not read the embeddings model settings at {CONFIG_PATH}: {e}",
        ) from e
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


def chunk_size(model_id: str) -> int:
    """How many tokens of this model's input one chunk may take up."""
    size = embeddings_config(model_id).get("chunk_size")
    if not size:
        raise EmbeddingsConfigError(
            model=model_id,
            message=f"the settings for {model_id} in {CONFIG_PATH} carry no chunk_size",
        )
    return int(size)
