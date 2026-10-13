"""Measuring a chunk with whichever tokenizer the embeddings model's settings name.

The splitter sizes every candidate chunk through `count_tokens`, so a tokenizer that disagreed with
the model would make chunks too long to embed or needlessly short. tiktoken and the Hugging Face
tokenizer are stubbed here: what is under test is which one is asked, and how.
"""

import json
from types import SimpleNamespace

import pytest
from shabti_types import EmbeddingsConfigError

from ...src.app.functionality import embeddings, embeddings_config
from ...src.app.functionality.embeddings import count_tokens

LLM_HOST = "llm-host-under-test"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Writes the model settings file, and points the reader at it."""
    monkeypatch.setenv("LLM_HOST", LLM_HOST)
    path = tmp_path / "shabti-models.json"
    monkeypatch.setattr(embeddings_config, "CONFIG_PATH", str(path))
    embeddings.tiktoken_encoding.cache_clear()
    embeddings.load_tokenizer.cache_clear()

    def write(model_id, **fields):
        path.write_text(json.dumps({model_id: {"chunk_size": 128, **fields}}))
        return tmp_path

    return write


def test_an_estimate_counts_characters(settings):
    settings("acme/embed-1", tokenizer="estimate")
    assert count_tokens("abcdefg", "acme/embed-1") == 3
    assert count_tokens("abcdef", "acme/embed-1") == 2


def test_a_remote_model_is_estimated_by_default(settings):
    settings("acme/embed-1")
    assert count_tokens("abcdefg", "acme/embed-1") == 3


def test_tiktoken_is_asked_for_the_named_encoding(settings, monkeypatch):
    settings("acme/embed-1", tokenizer="tiktoken:cl100k_base")
    asked = []

    class Encoding:
        def encode(self, text, disallowed_special=None):
            # special tokens in a document are just text: tiktoken raises on them otherwise
            assert disallowed_special == ()
            return text.split()

    def get_encoding(name):
        asked.append(name)
        return Encoding()

    monkeypatch.setattr(
        embeddings, "tiktoken", SimpleNamespace(get_encoding=get_encoding)
    )
    assert count_tokens("one two three", "acme/embed-1") == 3
    assert count_tokens("four five", "acme/embed-1") == 2
    # loaded once, rather than per chunk
    assert asked == ["cl100k_base"]


def test_a_hugging_face_tokenizer_is_read_from_where_the_configurator_put_it(
    settings, monkeypatch
):
    directory = settings("acme/embed-1", tokenizer="hf:acme/embed-1")
    (directory / "tokenizers").mkdir()
    expected = directory / "tokenizers" / "acme--embed-1.json"
    expected.write_text("{}")
    loaded = []

    class FakeTokenizer:
        @staticmethod
        def from_file(path):
            loaded.append(path)
            return FakeTokenizer()

        def encode(self, text):
            return SimpleNamespace(ids=list(range(len(text.split()) + 2)))

    monkeypatch.setattr(embeddings, "Tokenizer", FakeTokenizer)
    # the special tokens the tokenizer adds are part of what the model is given
    assert count_tokens("one two three", "acme/embed-1") == 5
    assert loaded == [str(expected)]


def test_a_missing_hugging_face_tokenizer_says_where_it_should_be(settings):
    settings("acme/embed-1", tokenizer="hf:acme/embed-1")
    with pytest.raises(EmbeddingsConfigError) as raised:
        count_tokens("one two three", "acme/embed-1")
    assert "acme--embed-1.json" in raised.value.message


def test_an_unknown_tokenizer_is_an_error(settings):
    settings("acme/embed-1", tokenizer="sentencepiece")
    with pytest.raises(EmbeddingsConfigError) as raised:
        count_tokens("one two three", "acme/embed-1")
    assert "sentencepiece" in raised.value.message


def test_a_local_model_is_measured_by_llama_cpp(settings, monkeypatch):
    settings("local/embed-1")
    posted = []

    class Session:
        def post(self, url, json=None, **kwargs):
            posted.append((url, json["model"]))
            return SimpleNamespace(status_code=200, json=lambda: {"tokens": [1, 2]})

    monkeypatch.setattr(embeddings, "session", lambda: Session())
    assert count_tokens("some text", "local/embed-1") == 2
    assert posted == [(f"http://{LLM_HOST}:11434/tokenize", "embed-1")]
