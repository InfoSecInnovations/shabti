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

    def configure(chunk_size, limit):
        monkeypatch.setattr(opensearch_ingesting, "chunk_size", lambda _: chunk_size)
        monkeypatch.setattr(opensearch_ingesting, "context_limit", lambda _: limit)

    return configure


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
