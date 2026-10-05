# syntax=docker/dockerfile:1.7

FROM python:3.14-slim-trixie AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1

WORKDIR /data
ENTRYPOINT ["/bin/bash"]

# Build the virtual environment. This stage doesn't need LaTeX,
# so dependency changes don't invalidate the texlive layer and
# both stages can build in parallel.
FROM base AS builder

# Copy uv static binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Ship the venv with compiled bytecode. A container run as a non-root
# user cannot write it later, and would recompile every dependency on
# each start.
ENV UV_COMPILE_BYTECODE=1

WORKDIR /build

# 1) Copy dependency manifests first (for caching)
COPY pyproject.toml uv.lock /build/

# Install dependencies (no project source yet -> cacheable)
RUN --mount=type=cache,target=/root/.cache \
    uv sync --frozen --no-install-project --no-dev

# 2) Now copy project source and install the package.
# README.md is required by the build backend (project.readme).
COPY README.md LICENSE /build/
COPY cgt_calc /build/cgt_calc

# Package version to stamp, e.g. "v2.1.0" or "2.0.0.post127+gabc1234".
# Declared this late on purpose: changing it only invalidates the
# project install below, not the dependency layers above.
ARG VERSION

# --no-editable installs the package into the venv itself,
# so the runtime stage only needs the venv.
RUN --mount=type=cache,target=/root/.cache \
    if [ -n "$VERSION" ]; then uv version "$VERSION"; fi \
 && uv sync --frozen --no-dev --no-editable

FROM base AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash texlive-latex-base \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/.venv /build/.venv

# Simple CLI shim
RUN printf '%s\n' 'exec /build/.venv/bin/cgt-calc "$@"' > /bin/cgt-calc \
 && chmod +x /bin/cgt-calc

# CI runs the test suite from a workspace mounted over /build, which
# needs uv inside the container. This stage is last so plain builds
# (CI, local) get it by default; publishing targets the runtime stage.
FROM runtime AS test

COPY --from=builder /bin/uv /bin/uvx /bin/
