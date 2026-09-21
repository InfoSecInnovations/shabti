"""The installer's record of the model this installation embeds with.

Everything here is about what happens when that record isn't there or doesn't say what the API
needs, because the failure it replaces was a document splitting against the wrong model's
tokenizer, which nothing would have reported.
"""

import json

import pytest
from shabti_types import EmbeddingsConfigError

from ...src.app.functionality import embeddings_config
from ...src.app.functionality.embeddings_config import (
    chunk_size,
    document_prefix,
    query_prefix,
)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Writes a settings file and points the reader at it."""

    def write(contents):
        path = tmp_path / "shabti-models.json"
        path.write_text(json.dumps(contents) if contents is not None else "not json")
        monkeypatch.setattr(embeddings_config, "CONFIG_PATH", str(path))
        embeddings_config.embeddings_config.cache_clear()
        return path

    return write


def test_the_selected_model_carries_its_chunk_size(config):
    config({"embed-1": {"chunk_size": 128}})
    assert chunk_size("embed-1") == 128


def test_a_chunk_size_written_as_text_is_still_a_number(config):
    config({"embed-1": {"chunk_size": "512"}})
    assert chunk_size("embed-1") == 512


def test_a_missing_file_names_the_file(config, tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings_config, "CONFIG_PATH", str(tmp_path / "gone.json"))
    embeddings_config.embeddings_config.cache_clear()
    with pytest.raises(EmbeddingsConfigError) as raised:
        chunk_size("embed-1")
    assert "gone.json" in raised.value.message
    # the installation is incomplete, nothing the caller sent is wrong
    assert raised.value.status == 500


def test_an_unreadable_file_is_an_error_rather_than_a_crash(config):
    config(None)
    with pytest.raises(EmbeddingsConfigError):
        chunk_size("embed-1")


def test_a_model_with_no_settings_names_the_model(config):
    config({"some-other-model": {"chunk_size": 128}})
    with pytest.raises(EmbeddingsConfigError) as raised:
        chunk_size("embed-1")
    assert "embed-1" in raised.value.message


def test_settings_without_a_chunk_size_are_an_error(config):
    # the splitter has nothing to fall back on: the GGUF the model is loaded from carries no token
    # config, so a missing chunk size cannot be guessed
    config({"embed-1": {}})
    with pytest.raises(EmbeddingsConfigError) as raised:
        chunk_size("embed-1")
    assert "chunk_size" in raised.value.message


def test_a_failed_read_is_retried_rather_than_remembered(config):
    path = config({})
    with pytest.raises(EmbeddingsConfigError):
        chunk_size("embed-1")
    # a container that started before the installer wrote the file has to be able to recover, so
    # the file is rewritten without clearing the cache: `functools.cache` does not cache the
    # exception, so the next call reads it again
    path.write_text(json.dumps({"embed-1": {"chunk_size": 128}}))
    assert chunk_size("embed-1") == 128


def test_the_selected_model_carries_the_prefixes_it_wants(config):
    # an asymmetric model was trained with an instruction on the query and nothing on the passages
    config({"embed-1": {"chunk_size": 128, "query_prefix": "Represent this: "}})
    assert query_prefix("embed-1") == "Represent this: "
    assert document_prefix("embed-1") == ""


def test_a_model_that_wants_a_prefix_on_both_sides_gets_both(config):
    # e5 and nomic do, and the document one is part of what a stored vector means
    config(
        {
            "embed-1": {
                "chunk_size": 128,
                "query_prefix": "search_query: ",
                "document_prefix": "search_document: ",
            }
        }
    )
    assert query_prefix("embed-1") == "search_query: "
    assert document_prefix("embed-1") == "search_document: "


def test_no_prefix_is_an_answer_rather_than_an_error(config):
    # unlike a missing chunk size, which cannot be guessed: a symmetric model wants no prefix at
    # all, and that is the shipped model's case
    config({"embed-1": {"chunk_size": 128}})
    assert query_prefix("embed-1") == ""
    assert document_prefix("embed-1") == ""
