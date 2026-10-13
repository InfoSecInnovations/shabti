"""Starting up when the embeddings model can't be used.

A provider refusing its key, or llama.cpp not answering yet, is something to report on the status
page rather than a reason for the API to refuse to start.
"""

import logging
from types import SimpleNamespace

from shabti_types import ProviderAuthError

from ...src.app import app as app_module


class Registry:
    async def shutdown(self):
        pass


async def nothing(*args, **kwargs):
    return []


async def test_a_refused_key_does_not_stop_startup(monkeypatch, caplog):
    def refused():
        raise ProviderAuthError("acme/embed-1")

    verified = []

    async def verify(force=False):
        verified.append(force)

    monkeypatch.setattr(app_module, "get_embeddings_model_id", lambda: "acme/embed-1")
    monkeypatch.setattr(app_module, "get_vector_dimension", refused)
    monkeypatch.setattr(app_module.embeddings_fingerprint, "verify", verify)
    monkeypatch.setattr(app_module, "sweep_ingesting_documents", nothing)
    monkeypatch.setattr(app_module, "close_client", nothing)
    app = SimpleNamespace(state=SimpleNamespace(ingest_registry=Registry()))
    started = False
    with caplog.at_level(logging.WARNING, logger="shabti"):
        async with app_module.lifespan(app):
            started = True
    assert started
    assert "ProviderAuthError" in caplog.text
    # it copes with an unreachable model itself, so it is still asked
    assert verified == [True]


async def test_startup_checks_the_model_has_not_changed(monkeypatch):
    verified = []

    async def verify(force=False):
        verified.append(force)

    monkeypatch.setattr(app_module, "get_embeddings_model_id", lambda: "acme/embed-1")
    monkeypatch.setattr(app_module, "get_vector_dimension", lambda: 3)
    monkeypatch.setattr(app_module, "context_limit", lambda model_id: None)
    monkeypatch.setattr(app_module.embeddings_fingerprint, "verify", verify)
    monkeypatch.setattr(app_module, "sweep_ingesting_documents", nothing)
    monkeypatch.setattr(app_module, "close_client", nothing)
    app = SimpleNamespace(state=SimpleNamespace(ingest_registry=Registry()))
    async with app_module.lifespan(app):
        pass
    assert verified == [True]
