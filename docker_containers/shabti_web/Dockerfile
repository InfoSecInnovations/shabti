FROM astral/uv:0.12.18-python3.14-trixie-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/
WORKDIR /app/shabti_web/
ENV UV_LINK_MODE=copy

EXPOSE 15130

# these must match the paths in [tool.uv.sources] in pyproject.toml
COPY --from=python_packages ./shabti_util /app/python_packages/shabti_util/
COPY --from=python_packages ./shabti_types /app/python_packages/shabti_types/
COPY --from=python_packages ./shabti_keycloak /app/python_packages/shabti_keycloak/
COPY --from=python_packages ./shabti_api_client /app/python_packages/shabti_api_client/

FROM base AS local
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=bind,source=pyproject.toml,target=pyproject.toml --mount=type=bind,source=uv.lock,target=uv.lock uv sync --locked --no-install-project
COPY . /app/shabti_web/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

CMD [ "uv", "run", "docker_run.py", "--development" ]

FROM base AS online
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=bind,source=pyproject.toml,target=pyproject.toml --mount=type=bind,source=uv.lock,target=uv.lock uv sync --locked --no-install-project --no-dev --no-editable
COPY . /app/shabti_web/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

CMD [ "uv", "run", "docker_run.py" ]
