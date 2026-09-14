import json
import logging
import logging.config

import pytest
from shabti_types import IngestInfo

from ..logging_config import logging_config


@pytest.fixture(scope="session")
def ingest_and_wait(shabti_client):
    """POST an ingest and block until it is over, returning the response and terminal state.

    An ingest runs on the server now, so a POST returns as soon as it is accepted and a test that
    asserted on documents straight afterwards would race it. Draining `GET /ingests/{id}` is the
    wait: the stream ends when the ingest does. A non-2xx is handed straight back undrained, so the
    tests asserting a 403 or a 404 still get what they expect.

    Both this and the follow-up listing rely on a finished ingest staying queryable - a small text
    file is done well before either request lands.

    Session scoped because it holds no per-test state: a fixture that builds a collection once for
    a whole module needs to ingest into it, and could not ask for a function scoped fixture.
    """

    def run(method: str, url: str, **kwargs) -> tuple[object, IngestInfo | None, list]:
        response = shabti_client.request(method, url, **kwargs)
        if response.status_code // 100 != 2:
            return response, None, []
        ingest_id = response.json()["ingest_id"]
        lines = []
        with shabti_client.stream("GET", f"/ingests/{ingest_id}") as stream:
            for line in stream.iter_lines():
                if line.strip():
                    lines.append(json.loads(line))
        listed = shabti_client.get("/ingests").json()
        terminal = next(
            IngestInfo(**item) for item in listed if item["ingest_id"] == ingest_id
        )
        return response, terminal, lines

    return run


@pytest.fixture(scope="session")
def document_ids_of():
    """The document ids an ingest produced, in the order its items were queued."""

    def ids(info: IngestInfo) -> list[str]:
        return [item.info.document_id for item in info.items if item.info]

    return ids


@pytest.fixture(scope="module")
def log_file(tmp_path_factory):
    """Logging on, and the real formatter and handler writing somewhere disposable.

    Only the `shabti` logger is configured: applying the whole `logging_config()` would take
    uvicorn's loggers, and a running TestClient's output with them. It still turns off propagation
    for as long as it is up, so a module that also reads `caplog` has to do that before asking for
    this.

    Module scoped because the fixtures that drive a whole collection lifecycle to produce entries
    are, and a module scoped fixture cannot ask for a function scoped one.
    """
    log_dir = tmp_path_factory.mktemp("logs")
    # that scope also rules out the function scoped `monkeypatch` fixture
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SHABTI_LOGGING_ENABLED", "True")
        patch.setenv("SHABTI_LOG_DIR", str(log_dir))
        config = logging_config()
        logger = logging.getLogger("shabti")
        handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {"shabti": config["formatters"]["shabti"]},
                "handlers": {"shabti": config["handlers"]["shabti"]},
                "loggers": {"shabti": config["loggers"]["shabti"]},
            }
        )
        yield log_dir / "shabti_log.json"
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.level, logger.propagate = handlers, level, propagate
