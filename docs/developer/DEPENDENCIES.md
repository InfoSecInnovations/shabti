# Third party dependencies

Shabti pins third party dependencies in four kinds of file: `pyproject.toml`, `package.json`, the
docker compose `image:` lines, and the Dockerfile `FROM` and `COPY --from` lines. A `COPY --from`
is only read when it names a tag or digest, since a bare name may be a build context.
`dependencies/` reports what is pinned against what has been published, and sets a pin to a version
you choose.

Only pins the repo declares directly are read. Nothing here reads a lockfile except
[Migrating](#migrating), so a transitive dependency can never turn up in the report.

## Checking

```sh
bun run deps
```

Reports every dependency that is behind, not pinned exactly, or pinned at different versions in
different files, along with the latest stable and prerelease upstream. Being behind exits 0 — it is
information, not a failure. A lookup that could not be made exits 1.

Rows are grouped by the kind of mismatch, since that is what you act on, and sub-grouped by ecosystem
under each heading. Each dependency appears under one heading only — the most serious one it qualifies
for. A range pin that is also behind is filed under `not pinned exactly`, because tightening it is the
thing to do, and the row shows the newer version anyway. The table is name, versions and where the pin lives; the
reason is the heading it is under, and the detail a row cannot hold without running off the edge of the
terminal — which tag a docker search held constant, why a lookup failed — is in `--json`. A failed
lookup also prints to stderr.

| flag | |
| --- | --- |
| `--all` | list every dependency, not only the ones worth acting on |
| `--json` | one line of JSON instead of the table, with the notes the table omits |
| `--no-latest-check` | skip resolving what each image's `latest` tag points at, saving a request per image |
| `--fail-if-behind` | exit non zero when anything is behind, for a scheduled job |

## Setting

```sh
bun run deps:set -- fastapi@0.137.0 astral/uv@0.9.7 @types/semver@7.8.0
```

Writes the version as an exact pin in every file that names the dependency, then regenerates whichever
lockfiles that invalidated. Any version is accepted as long as it exists upstream, older included:
downgrading out of a bad release is the point, so the current pin is never consulted.

A name given without a version takes the latest stable release — the same version `deps` reports as
the newer one, so `bun run deps:set -- fastapi` moves the pin to what the report was pointing at.

| flag | |
| --- | --- |
| `--ecosystem <python\|node\|docker\|system>` | when the same name is pinned in more than one |
| `--tag` | write the version as the whole image tag, for a tag with no version to substitute into |
| `--no-lock` | rewrite the pins but print the lockfile commands instead of running them |
| `--json` | one line of JSON instead of the table |

Nothing is written until every occurrence of every dependency named has been resolved, so one
occurrence that cannot be set — or one name that does not exist — leaves the tree untouched. That is
deliberate: a partial write would leave exactly the disagreement between files this is meant to
remove. Refusals are reported together rather than one run at a time, and the lockfiles are
regenerated once at the end however many dependencies moved.

A docker `set` needs no extra step for the configurator — `zipDockerCompose.ts` runs inside every
`build_*` script, so the zipped compose files pick the change up.

## System tools

The `bun` and `uv` on your PATH are listed under the `system` ecosystem, with the executable in place
of a file. Their latest versions come from npm's `bun` package and PyPI's `uv`. A tool that is not
installed is listed under `not supported`.

`bun run deps:set -- bun uv@0.11.7` upgrades them before any file is written, so the lockfiles are
regenerated with the new versions:

- `bun` at the latest runs `bun upgrade`; any other version runs Bun's installer. On Windows the
  installer refuses while bun is open, which it always is here, so the command is printed to run
  yourself instead.
- `uv` runs `uv self update <version>`, which only works for a uv installed with the standalone
  installer.

Setting `bun` also sets the `oven/bun` image that
[testing/images/Dockerfile.bun](../../testing/images/Dockerfile.bun) copies bun from to the same
version, so the tests run the Bun you develop with. It is checked like any other pin, so a Bun version
with no matching image is refused before anything runs. `bun run deps:set -- bun@<installed version>`
upgrades nothing and only moves the image, for when Bun was upgraded some other way.

## Migrating

```sh
bun run deps:migrate -- --dry-run
```

Rewrites every range pin as the exact version it currently resolves to, read from `uv.lock` and
`node_modules`, so nothing that is installed today changes. A dependency whose occurrences resolve to
different versions is reported and left alone — picking one would be an upgrade for one file and a
downgrade for another, which is a decision for `deps:set`.

| flag | |
| --- | --- |
| `--dry-run` | list what would change without writing anything |
| `--no-lock` | rewrite the pins but print the lockfile commands instead of running them |
| `--json` | one line of JSON instead of the table |

The `skipped` rows say `not installed` or `resolves differently`; the sentence explaining which files
are involved goes to stderr, so the table stays a table.

[Exceptions](#exceptions) are honoured pin by pin, not dependency by dependency: `pydantic` floats in
the library that publishes it and is pinned in the app that installs it, so the exempt occurrence is
dropped before anything is planned and only the other one is rewritten. The exempt pins are listed
under `intentionally floating, not migrated` with the pin they keep and no version beside it — what
they would have been bumped to is the one thing that must not happen to them.

## Exceptions

[dependencies/exceptions.json](../../dependencies/exceptions.json) lists the dependencies that are
deliberately not pinned exactly. Both `deps` and `deps:migrate` read it: the first reports them in
their own section rather than as warnings, so the warnings stay worth reading, and the second refuses
to touch them. Neither prints the reasons — this file is where a reason is read and changed, and eight
sentences would be the longest lines in the report for the rows needing the least attention.

Each entry needs an `ecosystem` (`python`, `node` or `docker`), a `name` spelled the way that ecosystem
spells it, and a reason; `files` narrows it to particular files, so a library can float what an
application pins.

Today:

- `@types/bun`, which tracks whichever Bun the repo is built with.
- `@std/ini`, aliased through the JSR registry, so there is no npm version to pin.
- the unpinned dependencies of the published `python_packages` libraries, where an exact pin would be
  forced on anything installing them.
- `javieraviles/zip`, which only zips the Keycloak policies and is never deployed, so its tag has no
  bearing on what runs.

An unrecognised `ecosystem` is an error rather than an entry that quietly matches nothing, since a
misspelling exempts no pin and looks from the outside exactly like the entry working.

## Images

Container tags are not versions. A tag is treated as a template with one varying slot —
`0.11.1-python3.14-trixie-slim` varies the version and holds `-python3.14-trixie-slim` constant,
`server-cuda-b9843` varies a build counter — and only tags on the same template are considered. Every
image result records which label it held constant, because that is what it did **not** search: a newer
Python base or an `-alpine` variant is out of view by design. The whole tag is on both sides of the
table row, so the label is visible there; `--json` names it outright.

Image rows show whole tags on both sides — `0.11.1-python3.14-trixie-slim -> 0.12.5-python3.14-trixie-slim`
— so what the report prints can be handed straight to `deps:set`, which accepts either the whole tag or
just the version inside it.

The newest version is found through the unlabelled version stream rather than by comparing labelled
tags. `apache/tika` is why: pinned at `3.3.0.0-full`, the labelled tags alone make `3.3.1.0-full` look
like the answer, because `4.0.0-full` has three release segments where the pin has four. Asking what
the newest plain version is gives `4.0.0`, and `4.0.0-full` exists.

`astral/uv` is pinned in four files — three Dockerfile `FROM` lines and
[docker-compose-uv-lock.yml](../../docker_containers/docker-compose-uv-lock.yml). One `deps:set`
changes all four, which is why Dockerfiles are in scope at all: that invariant used to be held
together only by a comment.

## Out of scope

GitHub Actions `uses:` pins, the `CodeSignTool` release URL, `.python-version`, the `ruff-pre-commit`
rev in `.pre-commit-config.yaml`, and the `biome.json` `$schema` URL. The last two mirror pins that
are in scope and have both already drifted from them.

## Tests

```sh
bun run test unit dependencies
```

Every test is offline: HTTP is injected, and a request to a URL the fixture does not cover throws with
that URL in the message, which is how an accidental live call is caught. The lockfile runner is
injected the same way, so no test starts Docker. The suite runs in a container along with the rest of
the `unit` test type -- see [testing/README.md](../../testing/README.md).
