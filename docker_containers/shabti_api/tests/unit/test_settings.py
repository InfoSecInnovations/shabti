"""The env-or-default readers, on their own: no stack, env vars set with monkeypatch."""

import pytest

from ...src.app.functionality.settings import (
    DEFAULTS,
    TEXT_DEFAULTS,
    setting,
    text_setting,
)

INT_NAME = "SHABTI_RRF_K"
TEXT_NAME = "SHABTI_RETRIEVAL_MODE"


def test_an_integer_setting_falls_back_to_its_default(monkeypatch):
    monkeypatch.delenv(INT_NAME, raising=False)
    assert setting(INT_NAME) == DEFAULTS[INT_NAME]


def test_an_integer_setting_is_overridden_by_the_environment(monkeypatch):
    monkeypatch.setenv(INT_NAME, "7")
    assert setting(INT_NAME) == 7


def test_an_empty_integer_setting_falls_back_to_its_default(monkeypatch):
    monkeypatch.setenv(INT_NAME, "")
    assert setting(INT_NAME) == DEFAULTS[INT_NAME]


def test_an_unknown_integer_setting_raises_key_error(monkeypatch):
    monkeypatch.delenv("SHABTI_NO_SUCH_SETTING", raising=False)
    with pytest.raises(KeyError):
        setting("SHABTI_NO_SUCH_SETTING")


def test_a_text_setting_falls_back_to_its_default(monkeypatch):
    monkeypatch.delenv(TEXT_NAME, raising=False)
    assert text_setting(TEXT_NAME) == TEXT_DEFAULTS[TEXT_NAME]


def test_a_text_setting_is_overridden_by_the_environment(monkeypatch):
    monkeypatch.setenv(TEXT_NAME, "hybrid")
    assert text_setting(TEXT_NAME) == "hybrid"


def test_an_empty_text_setting_falls_back_to_its_default(monkeypatch):
    monkeypatch.setenv(TEXT_NAME, "")
    assert text_setting(TEXT_NAME) == TEXT_DEFAULTS[TEXT_NAME]


def test_an_unknown_text_setting_raises_key_error(monkeypatch):
    monkeypatch.delenv("SHABTI_NO_SUCH_SETTING", raising=False)
    with pytest.raises(KeyError):
        text_setting("SHABTI_NO_SUCH_SETTING")
