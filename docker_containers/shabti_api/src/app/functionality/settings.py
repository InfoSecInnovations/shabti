"""Integer settings read from the environment, with a default per name.

One dict rather than scattered `os.getenv` calls so the knobs are discoverable in one place and a
typo in a name is a `KeyError` at the call site instead of a silent default. Booleans keep the
`os.getenv(X) == "True"` idiom used elsewhere; only numbers live here.
"""

import os

DEFAULTS = {
    "SHABTI_CRAWL_MAX_DEPTH": 5,
    "SHABTI_CRAWL_MAX_PAGES": 50,
    "SHABTI_CRAWL_MAX_PAGE_BYTES": 1000000,
    "SHABTI_CRAWL_MAX_TOTAL_BYTES": 20000000,
    "SHABTI_CRAWL_CONCURRENCY": 4,
    "SHABTI_CRAWL_REQUESTS_PER_MINUTE": 120,
    "SHABTI_CRAWL_TIMEOUT_SECONDS": 20,
    # a whole crawl, not one navigation: nothing else bounds crawler.run()
    "SHABTI_CRAWL_MAX_SECONDS": 300,
    # crawlee's autoscaler refuses to schedule requests once the *process* RSS exceeds 90% of this
    # budget, and defaults it to a quarter of system RAM. That assumes it owns the process; ours
    # also holds the API, the tokenizer and every in-flight ingest, so RSS says nothing about
    # whether another page can be fetched - and once it is over, nothing brings it back down and
    # the crawl stalls silently for ever. Concurrency is already bounded by the settings above.
    "SHABTI_CRAWL_MEMORY_MB": 16000,
    # documents ingested at once within one ingest. a memory knob as much as a speed one: each slot
    # holds a whole Tika parse or a crawl's extracted text, and the crawl limits above are per
    # crawl, so N concurrent URL ingests multiply all of them
    "SHABTI_INGEST_CONCURRENCY": 3,
    # how long a cancelled job gets to roll itself back before it is cancelled outright
    "SHABTI_INGEST_STOP_GRACE_SECONDS": 30,
    # a finished ingest stays queryable this long, so a client that POSTs a small file and then
    # asks for the ingest gets a terminal snapshot rather than a 404
    "SHABTI_INGEST_RETENTION_SECONDS": 3600,
    "SHABTI_INGEST_MAX_FINISHED": 50,
    # detaching removes the natural limit of a client holding a connection open, so without these
    # a client firing POSTs and disconnecting could put MAX_ACTIVE * CONCURRENCY documents in flight.
    # a cap on how many ingests run at once, not on how many may be submitted: one over the cap waits
    # for a slot rather than being refused
    "SHABTI_INGEST_MAX_ACTIVE": 4,
    "SHABTI_INGEST_MAX_ACTIVE_PER_OWNER": 2,
    # nothing bounds the queue, so this is only where a warning is logged: a client that keeps
    # POSTing files holds every one of them in SHABTI_FILES_DIR until its ingest runs. the real fix,
    # if it is ever needed, is a per-owner staged-byte quota rather than a rejection
    "SHABTI_INGEST_QUEUE_WARN_DEPTH": 20,
    # expanding an archive is bounded by disk now that members stream to files rather than memory
    "SHABTI_INGEST_MAX_ZIP_MEMBERS": 100,
    "SHABTI_INGEST_MAX_ZIP_BYTES": 200000000,
    # how many chunks a prompt is answered from. they are collapsed on their text, so this is a
    # count of distinct references rather than of hits, and raising it costs context window as much
    # as it costs retrieval time
    "SHABTI_PROMPT_REFERENCE_LIMIT": 5,
}


def setting(name: str) -> int:
    value = os.getenv(name)
    return int(value) if value else DEFAULTS[name]
