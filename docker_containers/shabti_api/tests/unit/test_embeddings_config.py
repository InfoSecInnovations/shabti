"""The installer's record of the model this installation embeds with.

Everything here is about what happens when that record isn't there or doesn't say what the API
needs, because the failure it replaces was a document splitting against the wrong model's
tokenizer, which nothing would have reported.
"""

import json
import os

import pytest
from shabti_types import EmbeddingsConfigError

from ...src.app.functionality import embeddings_config
from ...src.app.functionality.embeddings_config import (
    REMOTE_BATCH_SIZE,
    REMOTE_MAX_CONCURRENCY,
    batch_size,
    chunk_size,
    context_limit_setting,
    document_prefix,
    extra_body,
    max_concurrency,
    query_prefix,
    tokenizer,
)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Writes a settings file and points the reader at it."""

    writes = iter(range(1, 1000))

    def write(contents):
        path = tmp_path / "shabti-models.json"
        path.write_text(json.dumps(contents) if contents is not None else "not json")
        # a rewrite within one clock tick would otherwise look unchanged to the mtime cache
        stamp = next(writes)
        os.utime(path, (stamp, stamp))
        monkeypatch.setattr(embeddings_config, "CONFIG_PATH", str(path))
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


def test_a_rewritten_file_is_read_again(config):
    config({})
    with pytest.raises(EmbeddingsConfigError):
        chunk_size("embed-1")
    # a container that started before the installer wrote the file has to be able to recover
    # without a restart
    config({"embed-1": {"chunk_size": 128}})
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


def test_a_local_model_is_measured_by_llama_cpp_unless_told_otherwise(config):
    config({"local/embed-1": {"chunk_size": 128}})
    assert tokenizer("local/embed-1") == "llama_cpp"


def test_a_remote_model_is_estimated_unless_told_otherwise(config):
    config({"acme/embed-1": {"chunk_size": 512}})
    assert tokenizer("acme/embed-1") == "estimate"


def test_the_configured_tokenizer_is_used(config):
    config({"acme/embed-1": {"chunk_size": 512, "tokenizer": "tiktoken:cl100k_base"}})
    assert tokenizer("acme/embed-1") == "tiktoken:cl100k_base"


def test_a_local_model_takes_everything_at_once(config):
    config({"local/embed-1": {"chunk_size": 128}})
    assert batch_size("local/embed-1") is None
    assert max_concurrency("local/embed-1") is None


def test_a_remote_model_is_sent_its_chunks_in_limited_batches(config):
    config({"acme/embed-1": {"chunk_size": 512}})
    assert batch_size("acme/embed-1") == REMOTE_BATCH_SIZE
    assert max_concurrency("acme/embed-1") == REMOTE_MAX_CONCURRENCY


def test_the_configured_limits_are_used(config):
    config({"acme/embed-1": {"chunk_size": 512, "batch_size": 8, "max_concurrency": 1}})
    assert batch_size("acme/embed-1") == 8
    assert max_concurrency("acme/embed-1") == 1


def test_the_settings_embedding_needs_have_defaults_without_a_file(
    tmp_path, monkeypatch
):
    # the dimension probe embeds before anything is split, so it can't depend on the file
    monkeypatch.setattr(embeddings_config, "CONFIG_PATH", str(tmp_path / "gone.json"))
    assert tokenizer("acme/embed-1") == "estimate"
    assert extra_body("acme/embed-1") == {}
    assert context_limit_setting("acme/embed-1") is None


def test_extra_fields_and_a_context_limit_can_be_configured(config):
    config(
        {
            "acme/embed-1": {
                "chunk_size": 512,
                "context_limit": "8191",
                "extra_body": {"input_type": "document"},
            }
        }
    )
    assert context_limit_setting("acme/embed-1") == 8191
    assert extra_body("acme/embed-1") == {"input_type": "document"}
