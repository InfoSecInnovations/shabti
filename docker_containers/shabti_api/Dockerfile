FROM astral/uv:0.12.18-python3.14-trixie-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/
WORKDIR /app/shabti/
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=500

EXPOSE 15131

# these must match the paths in [tool.uv.sources] in pyproject.toml
COPY --from=python_packages ./isi_util /app/python_packages/isi_util/
COPY --from=python_packages ./shabti_util /app/python_packages/shabti_util/
COPY --from=python_packages ./shabti_types /app/python_packages/shabti_types/
COPY --from=python_packages ./shabti_keycloak /app/python_packages/shabti_keycloak/

FROM base AS local
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=bind,source=pyproject.toml,target=pyproject.toml --mount=type=bind,source=uv.lock,target=uv.lock uv sync --locked --no-install-project
COPY . /app/shabti/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

# isi_util's own tests are run from this image by docker-compose-pytest.yml
WORKDIR /app/python_packages/isi_util/
RUN --mount=type=cache,target=/root/.cache/uv uv sync
WORKDIR /app/shabti/

CMD [ "uv", "run", "docker_run.py", "--development" ]

FROM base AS online
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=bind,source=pyproject.toml,target=pyproject.toml --mount=type=bind,source=uv.lock,target=uv.lock uv sync --locked --no-install-project --no-dev --no-editable
COPY . /app/shabti/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

CMD [ "uv", "run", "docker_run.py" ]
