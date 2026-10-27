"""The env-or-default readers, on their own: no stack, env vars set with monkeypatch."""

import pytest

from ...src.app.functionality.settings import DEFAULTS, setting

INT_NAME = "SHABTI_PROMPT_REFERENCE_LIMIT"


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
