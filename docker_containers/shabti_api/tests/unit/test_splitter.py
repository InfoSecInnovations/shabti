"""Building a splitter for whichever model is installed.

The chunk size comes from the installer and the ceiling from llama.cpp, and the two can disagree:
a catalogue entry can ask for chunks longer than the model was ever trained on. llama.cpp would
refuse the first oversized chunk partway through an ingest, with an error about batch sizes that
says nothing about which setting is wrong, so the disagreement is caught here instead.
"""

import pytest
from shabti_types import EmbeddingsConfigError

from ...src.app.functionality import opensearch_ingesting
from ...src.app.functionality.opensearch_ingesting import CHUNK_OVERLAP, get_splitter


@pytest.fixture
def model(monkeypatch):
    """Sets what the installer recorded and what llama.cpp reports, independently."""

    def configure(chunk_size, limit, document_prefix=""):
        monkeypatch.setattr(opensearch_ingesting, "chunk_size", lambda _: chunk_size)
        monkeypatch.setattr(opensearch_ingesting, "context_limit", lambda _: limit)
        monkeypatch.setattr(
            opensearch_ingesting, "document_prefix", lambda _: document_prefix
        )
        # llama.cpp counts the tokens in real use; a word stands in for one here so that what the
        # splitter is given and what comes back can be measured the same way
        monkeypatch.setattr(
            opensearch_ingesting, "count_tokens", lambda text, _: len(text.split())
        )

    return configure


# long enough that the splitter has to fill several chunks rather than returning the lot as one
TEXT = " ".join(f"word{n}" for n in range(500))


def test_a_chunk_size_within_the_model_splits(model):
    model(chunk_size=128, limit=512)
    assert get_splitter("embed-1")


def test_a_chunk_size_the_model_was_never_trained_on_is_refused(model):
    model(chunk_size=1024, limit=512)
    with pytest.raises(EmbeddingsConfigError) as raised:
        get_splitter("embed-1")
    assert "1024" in raised.value.message and "512" in raised.value.message


def test_a_chunk_size_exactly_at_the_limit_is_allowed(model):
    # the chunks are measured with their special tokens counted, so one the size of the limit is
    # what the model takes rather than one token too many
    model(chunk_size=512, limit=512)
    assert get_splitter("embed-1")


def test_an_unknown_limit_doesnt_block_ingesting(model):
    # nothing has loaded the model yet, so there is nothing to check against. refusing here would
    # mean a cold stack couldn't ingest at all
    model(chunk_size=1024, limit=None)
    assert get_splitter("embed-1")


def test_a_chunk_size_too_small_for_its_overlap_is_refused(model):
    # semantic-text-splitter requires overlap < capacity, and would otherwise fail per document
    model(chunk_size=CHUNK_OVERLAP, limit=512)
    with pytest.raises(ValueError):
        get_splitter("embed-1")


def test_a_document_prefix_comes_out_of_the_chunk_budget(model):
    # the prefix is embedded in front of every chunk, so a chunk filling the whole budget would put
    # the pair over the size the installer picked the model's physical batch from
    prefix = " ".join(["instruction"] * 20)
    model(chunk_size=200, limit=512, document_prefix=f"{prefix} ")
    with_prefix = max(len(c.split()) for c in get_splitter("embed-1").chunks(TEXT))
    assert with_prefix + 20 <= 200

    # and it is the prefix doing it rather than the splitter never reaching the budget anyway
    model(chunk_size=200, limit=512)
    assert (
        max(len(c.split()) for c in get_splitter("embed-1").chunks(TEXT)) > with_prefix
    )


def test_a_model_wanting_no_document_prefix_keeps_the_whole_budget(model):
    # the shipped model's case, and every model in the catalogue's
    model(chunk_size=200, limit=512)
    assert max(len(c.split()) for c in get_splitter("embed-1").chunks(TEXT)) <= 200


def test_a_document_prefix_with_no_room_left_for_text_is_refused(model):
    # reported against the prefix rather than falling through to the overlap check, which would
    # blame the chunk size for a number the prefix took
    model(chunk_size=128, limit=512, document_prefix=" ".join(["instruction"] * 130))
    with pytest.raises(EmbeddingsConfigError) as raised:
        get_splitter("embed-1")
    assert "document_prefix" in raised.value.message
