"""Noticing that the embeddings model makes different vectors than it did, without a model.

OpenSearch and the embeddings call are both stubbed: what is under test is when the probes are
embedded, what counts as a change, and that an outage is never mistaken for one.
"""

import pytest
from opensearchpy.exceptions import NotFoundError
from shabti_types import ProviderAuthError

from ...src.app.functionality import embeddings_fingerprint as fingerprint

MODEL = "acme/embed-1"
BASE = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


class Indices:
    def __init__(self, client):
        self.client = client

    async def exists(self, index):
        return index in self.client.documents

    async def create(self, index, body):
        self.client.documents[index] = {}

    async def delete(self, index):
        del self.client.documents[index]

    async def get_mapping(self, index):
        return self.client.mappings


class Client:
    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.mappings: dict[str, dict] = {}
        self.indices = Indices(self)

    async def get(self, index, id):
        try:
            return {"_source": self.documents[index][id]}
        except KeyError:
            raise NotFoundError(404, "not_found") from None

    async def index(self, index, id, body, refresh=False):
        self.documents.setdefault(index, {})[id] = body

    async def delete(self, index, id, refresh=False):
        try:
            del self.documents[index][id]
        except KeyError:
            raise NotFoundError(404, "not_found") from None


class Model:
    """What the embeddings model currently makes of the probes, and how often it was asked."""

    def __init__(self):
        self.model_id = MODEL
        self.vectors = BASE
        self.error = None
        self.calls = 0

    def embed(self, texts, model_id):
        self.calls += 1
        if self.error:
            raise self.error
        assert texts == fingerprint.PROBES
        return [list(vector) for vector in self.vectors]


@pytest.fixture
def clock(monkeypatch):
    now = {"value": 1000.0}
    monkeypatch.setattr(fingerprint.time, "monotonic", lambda: now["value"])
    return now


@pytest.fixture
def client(monkeypatch, clock):
    fake = Client()
    monkeypatch.setattr(fingerprint, "get_client", lambda: fake)
    monkeypatch.setattr(fingerprint, "_state", {"drifted": False, "next_check": 0.0})
    monkeypatch.setattr(fingerprint, "_locks", {})
    return fake


@pytest.fixture
def model(monkeypatch, client):
    fake = Model()
    monkeypatch.setattr(fingerprint, "create_embeddings", fake.embed)
    monkeypatch.setattr(fingerprint, "get_embeddings_model_id", lambda: fake.model_id)
    return fake


def stored(client):
    return client.documents[fingerprint.INDEX][fingerprint.DOC_ID]


async def test_the_first_collection_records_the_fingerprint(client, model):
    await fingerprint.ensure()
    assert stored(client) == {"model": MODEL, "dimension": 3, "vectors": BASE}
    assert not fingerprint.is_drifted()


async def test_the_same_vectors_have_not_drifted(client, model, clock):
    await fingerprint.ensure()
    await fingerprint.verify(force=True)
    assert not fingerprint.is_drifted()


async def test_the_noise_of_a_gpu_is_not_drift(client, model):
    await fingerprint.ensure()
    model.vectors = [[x + 1e-4 for x in vector] for vector in BASE]
    await fingerprint.verify(force=True)
    assert not fingerprint.is_drifted()


async def test_different_vectors_have_drifted(client, model):
    await fingerprint.ensure()
    model.vectors = [[1.0, 0.1, 0.0], BASE[1], BASE[2]]
    await fingerprint.verify(force=True)
    assert fingerprint.is_drifted()


async def test_a_different_dimension_has_drifted(client, model):
    await fingerprint.ensure()
    model.vectors = [vector + [0.0] for vector in BASE]
    await fingerprint.verify(force=True)
    assert fingerprint.is_drifted()


async def test_a_different_model_has_drifted(client, model):
    await fingerprint.ensure()
    model.model_id = "acme/embed-2"
    await fingerprint.verify(force=True)
    assert fingerprint.is_drifted()


@pytest.mark.parametrize("drifted", [False, True])
async def test_an_unreachable_model_leaves_the_state_alone(client, model, drifted):
    await fingerprint.ensure()
    fingerprint._state["drifted"] = drifted
    model.error = ProviderAuthError(MODEL)
    await fingerprint.verify(force=True)
    assert fingerprint.is_drifted() == drifted


async def test_nothing_indexed_is_nothing_to_drift_from(client, model):
    await fingerprint.verify(force=True)
    assert not fingerprint.is_drifted()
    assert model.calls == 0


async def test_the_model_is_only_checked_hourly(client, model, clock):
    await fingerprint.ensure()
    asked = model.calls
    await fingerprint.verify()
    assert model.calls == asked
    clock["value"] += fingerprint.RECHECK_SECONDS
    await fingerprint.verify()
    assert model.calls == asked + 1


async def test_a_failed_check_is_tried_again_sooner(client, model, clock):
    await fingerprint.ensure()
    model.error = ProviderAuthError(MODEL)
    await fingerprint.verify(force=True)
    asked = model.calls
    clock["value"] += fingerprint.FAILED_RECHECK_SECONDS
    await fingerprint.verify()
    assert model.calls == asked + 1


async def test_later_collections_check_rather_than_record(client, model, clock):
    await fingerprint.ensure()
    model.vectors = [[1.0, 0.1, 0.0], BASE[1], BASE[2]]
    clock["value"] += fingerprint.RECHECK_SECONDS
    await fingerprint.ensure()
    assert stored(client)["vectors"] == BASE
    assert fingerprint.is_drifted()


async def test_the_fingerprint_is_kept_while_a_collection_holds_vectors(client, model):
    await fingerprint.ensure()
    fingerprint._state["drifted"] = True
    client.mappings = {
        "collection-1": {"mappings": {"_meta": {"embeddings_model": MODEL}}}
    }
    await fingerprint.reset_if_unused()
    assert stored(client)
    assert fingerprint.is_drifted()


async def test_deleting_every_collection_forgets_the_fingerprint(client, model):
    await fingerprint.ensure()
    fingerprint._state["drifted"] = True
    client.mappings = {"collection_mappings": {"mappings": {}}}
    await fingerprint.reset_if_unused()
    assert await fingerprint.stored() is None
    assert not fingerprint.is_drifted()


async def test_forgetting_a_fingerprint_that_was_never_recorded_is_fine(client, model):
    await fingerprint.reset_if_unused()
    assert not fingerprint.is_drifted()


async def test_the_next_collection_records_the_model_as_it_is_now(client, model):
    await fingerprint.ensure()
    await fingerprint.reset_if_unused()
    model.vectors = [[1.0, 0.1, 0.0], BASE[1], BASE[2]]
    await fingerprint.ensure()
    assert stored(client)["vectors"] == model.vectors
    assert not fingerprint.is_drifted()
