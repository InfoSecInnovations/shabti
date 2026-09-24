# Testing

Everything runs from one command:

```
bun run test
```

That runs three **test types** in order, and stops for nothing:

| test type | what it is | stack |
|---|---|---|
| `unit` | hermetic suites: no services, no network, no Docker state | none |
| `disabled` | end to end against a security-disabled instance | OpenSearch, llama.cpp, Tika, Shabti |
| `enabled` | end to end against a security-enabled instance | the above plus Keycloak and Postgres |

Only the end-to-end types start a stack, so a unit run finishes in seconds. Suites are grouped by
type rather than by package so that each configuration is only ever brought up and torn down once.

## Filtering

```
bun run test --list                    # the test types and suites
bun run test unit                      # one test type
bun run test unit versioning           # one suite
bun run test api                       # one suite in every type it belongs to
bun run test disabled api test_collections_api.py::test_create
bun run test enabled cli -t upload
```

Runner flags go **before** the test type. After the suite name, leading bare words narrow the suite --
each is joined onto the suite's own path, which is how you get down to a single file. From the first
flag onwards everything is passed through to pytest or bun verbatim, so `-k` and `-t` behave
normally.

| flag | |
|---|---|
| `--keep-up` | leave the stack running afterwards; pair with `--no-clean` to actually reuse it |
| `--no-clean` | keep the existing OpenSearch state, certificates and Keycloak realm |
| `--no-lock` | skip the Python lockfile refresh |
| `--bail` | stop the whole run at the first failing suite |
| `--timeout <minutes>` | wall clock limit per suite |

The run exits non-zero if anything failed. The summary is one line per suite, then the counts, then
**why** anything failed — that part goes last on purpose, so the reason is still on screen when a
long run ends rather than a count. Each failure comes with the command that reproduces just that
one. A suite that died before writing any results has no test to blame, so the last few lines it
printed stand in for one; that is the only record of, say, a missing binary or a bad path.

## When a suite hangs

The Python suites carry a per-test `timeout` (`shabti_api/pyproject.toml`), so a stalled ingest
fails one test instead of blocking the run. To see where it stalled rather than just that it did:

```
bun run test disabled api --timeout-method=thread    # every thread's stack, then the run aborts
```

The `pytest-api` container has `SYS_PTRACE`, so a run that is *still* stuck can be opened up from
outside without killing it:

```
docker ps                                            # find the shabti-pytest-api-run-* container
docker exec <container> /app/shabti/.venv/bin/python -m asyncio pstree 11
```

## Where things are

| | |
|---|---|
| `run.ts` | the entry point: selection, orchestration, exit code |
| `suites.ts` | the suite registry, and how each runner is invoked |
| `stack.ts` | the compose plumbing, plus nuke and teardown |
| `security.ts` | certificates, Keycloak, and the generated env for the enabled type |
| `report.ts` | JUnit parsing and the failure-first summary |
| `compose/` | one file per layer, combined with `-f` per test type |
| `images/Dockerfile.bun` | the container every bun suite runs in |
| `test_results/` | per-suite JUnit XML from the last run |
| `processed_test_runs/` | one merged XML per run, kept as history |

Test types are assembled by layering compose files, which is what keeps the unit ones cheap:

- `runners.yml` alone is the unit type. It defines only the one-shot test containers, with no stack
  services and no `depends_on`, so nothing else can be started.
- `stack.yml` adds the long-running services, and `e2e.yml` wires the runners to them.
- `enabled.yml` goes on last for the security-enabled type.

Every suite is invoked as `docker compose run --rm <service> <command>`, which is what makes both
the exit code and the pass-through filtering work. Because `compose run` starts only the target
service's dependency graph, that graph has to be honest — `depends_on` is what decides which
containers a filtered run brings up:

- `shabti` depends on `opensearch-node1`, `llama-cpp` and `tika` being healthy, so anything that
  needs the live API gets the whole stack, and only once all of it is accepting requests. The
  healthchecks come from the configurator's compose fragments.
- `pytest-api` depends on those three being healthy directly and *not* on `shabti`, because it
  builds the app in process with `TestClient(create_app())` rather than talking to the container.
- `pytest-python-client` and `bun-tests` depend on `shabti` being healthy, and reach it over the
  network.
- `enabled.yml` adds Keycloak being healthy to `shabti` and `pytest-api`.

None of the suites wait for a service themselves; this is the only place that decides it is ready.

## The bun container

The bun suites run in an image that has the repository **copied in**, not bind mounted, so they
never see the host's `node_modules`. That means a source edit needs a rebuild, which the runner does
at the start of each test type anyway.

It is built on the same uv image `docker_containers/shabti_api/Dockerfile` pins, with bun's single
static binary copied in, because the suites need more than bun:

- `uv`, because `versioning/versions.ts` shells out to `uv version` to work out the next version of
  a python package. Building on the image that ships uv keeps one uv version in the repo rather than
  two, and brings a Python that satisfies the `requires-python` the fixtures declare.
- `git`, for the throwaway repos `versioning/tests/fixture.ts` builds.

If you change a `package.json`, run `bun install` so `bun.lock` stays in step — the image builds with
`--frozen-lockfile` and will refuse a stale one.

## The security-enabled type

This one runs a mini-install before any test can start: it generates self-signed certificates,
brings Keycloak up, waits for the realm import, and reads back the client secret that the API and
the clients then authenticate with. That is why it is the long one. The secret and the certificate
paths are written to `env/.generated.env`, which is not committed.

It also sets `KEYCLOAK_BACKCHANNEL_DYNAMIC=true`, which matters more than it looks. A real install
reaches Keycloak on `localhost`, so that is the hostname it is configured with and the one it
advertises in its discovery document. The Python client never looks: it builds
`https://${KEYCLOAK_HOST}:8443` itself. The Node client and the CLI *do* discover, so they would take
Keycloak at its word and try `localhost:8443` — which, from inside a test container, is the test
container. Dynamic backchannel resolution makes the token, jwks and introspection URLs follow the
host of the request that asked for them, so a caller on the compose network is told `keycloak:8443`,
while the browser facing URLs stay on `localhost`. Certificates already cover both names.
