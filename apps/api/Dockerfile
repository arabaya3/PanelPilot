# PanelPilot API image.
#
# Build context is apps/api (see docker-compose.yml). The API has no workspace
# dependencies, so it does not need the repo root the way apps/web does.
#
# Targets:
#   runtime  production-shaped, no build or dev tooling   (default)
#   dev      runtime + dev extras, for docker compose up
#
# There is no dependency lockfile in this repo, so `pip install` resolves
# versions at build time and two builds of the same commit can differ. Pinning
# is a real gap; it belongs in a lockfile, not in a workaround here.

# ---------------------------------------------------------------------------
# base — settings shared by every stage. No dependencies, no source.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    # The app is not pip-installed into the venv: the source is copied to /app
    # and imported from there. One copy, so a dev volume mount cannot be
    # shadowed by a stale installed copy in site-packages.
    PYTHONPATH=/app

WORKDIR /app

# Fixed uid/gid so a bind-mounted volume has predictable ownership.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

# ---------------------------------------------------------------------------
# builder — resolve and install runtime dependencies into a venv.
# ---------------------------------------------------------------------------
FROM base AS builder

# build-essential is needed only if a dependency has no wheel for this
# platform. It stays in this stage and never reaches runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

# Dependencies are installed from pyproject's [project.dependencies] only —
# never optional-dependencies.dev — so dev tooling cannot leak into the image.
# Copying pyproject alone first keeps this layer cached across source edits.
COPY pyproject.toml ./
RUN python -c "import pathlib, tomllib; print('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt

# ---------------------------------------------------------------------------
# dev — adds the dev extras so `docker compose exec api pytest` works.
# Never use this target for a deployed image.
# ---------------------------------------------------------------------------
FROM builder AS dev

RUN python -c "import pathlib, tomllib; print('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['optional-dependencies']['dev']))" > /tmp/dev-requirements.txt \
 && pip install --no-cache-dir -r /tmp/dev-requirements.txt

COPY --chown=app:app . /app

USER app
EXPOSE 8000

# Compose bind-mounts the source over /app, so --reload picks up host edits.
CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/app"]

# ---------------------------------------------------------------------------
# runtime — production-shaped. Last stage, so it is the default build target.
# ---------------------------------------------------------------------------
FROM base AS runtime

# Only the finished venv, never pip's cache or the build toolchain.
COPY --from=builder /opt/venv /opt/venv

# Source and migrations only. Tests, tooling config, and the venv are excluded
# by .dockerignore.
COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini pyproject.toml ./

USER app
EXPOSE 8000

# Readiness, not liveness: this gates whether the container should receive
# traffic, and it returns 503 until Postgres and OpenSearch both answer.
# urlopen raises on any non-2xx, which exits non-zero and marks it unhealthy.
# start-period covers first boot; failures before it elapses are not counted.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:8000' + os.environ.get('API_V1_PREFIX', '/api/v1') + '/health/ready', timeout=4)"]

# create_app is a factory — there is no module-level `app` object, so --factory
# is required. See apps/api/app/main.py.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
