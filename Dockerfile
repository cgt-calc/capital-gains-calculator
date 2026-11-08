# syntax=docker/dockerfile:1.7

# uv is pinned, so a new release reaches the image through a pull
# request and not on whichever build comes next. Dependabot bumps it.
# It reads only FROM lines, which is why the pin is a stage of its own
# and not an image named in COPY --from.
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.14-slim-trixie AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1

WORKDIR /data
ENTRYPOINT ["/bin/bash"]

# What the build stages below share. They build in parallel with the
# runtime stage's system packages.
FROM base AS build

# Copy uv static binary
COPY --from=uv /uv /uvx /bin/

# Ship the venv with compiled bytecode. A container run as a non-root
# user cannot write it later, and would recompile every dependency on
# each start.
ENV UV_COMPILE_BYTECODE=1

WORKDIR /build

# List the runtime dependencies. This stage reruns whenever a manifest
# changes, but the list it writes changes only when a runtime
# dependency does.
FROM build AS requirements

COPY pyproject.toml uv.lock /build/

# The deps stage installs from the list alone, so it would silently
# ignore a [tool.uv] setting. Stop the build if one is added, until
# this file either passes the setting on or allows it here. cache-keys
# only decides when uv rebuilds a checkout's editable install, and the
# image has none.
RUN python3 -c 'import sys, tomllib; extra = sorted(set(tomllib.load(open("pyproject.toml", "rb")).get("tool", {}).get("uv", {})) - {"cache-keys"}); sys.exit(f"pyproject.toml has [tool.uv] settings the dependency stage would ignore: {extra}" if extra else 0)'

RUN uv export --frozen --no-dev --no-emit-project --quiet -o requirements.txt

# Install the dependencies into a virtual environment. Only the list
# is copied in, and a copy is cached by its content, so this stage is
# rebuilt when the list changes and not on every edit to a manifest.
# pyproject.toml is not here, so a [tool.uv] setting would not apply
# to this install; the requirements stage refuses to build with one.
FROM build AS deps

COPY --from=requirements /build/requirements.txt /build/

RUN --mount=type=cache,target=/root/.cache \
    uv venv \
 && uv pip install --require-hashes -r requirements.txt

# Build the package's wheel from the source.
FROM build AS wheel

# README.md is required by the build backend (project.readme).
COPY pyproject.toml README.md LICENSE /build/
COPY cgt_calc /build/cgt_calc

# Package version: a release tag such as "v2.1.0", or git describe's
# "v2.0.0-127-gabc1234", which becomes 2.0.0.post127+gabc1234 as a
# build from Git history would name it. The image is built without
# .git, so the build cannot read the version itself. Declared in this
# stage only, so a new version invalidates the wheel build below and
# none of the dependency stages.
ARG VERSION

RUN --mount=type=cache,target=/root/.cache \
    if [ -z "$VERSION" ]; then \
      echo "Pass the version: --build-arg VERSION=\"\$(git describe --tags --match 'v[0-9]*')\"" >&2; \
      exit 1; \
    fi \
 && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CGT_CALC="$(echo "${VERSION#v}" | sed -E 's/-([0-9]+)-g([0-9a-f]+)$/.post\1+g\2/')" \
    uv build --wheel

FROM base AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
    && rm -rf /var/lib/apt/lists/*

# The dependencies and the package are separate layers. The first is
# large and is rebuilt only when the deps stage is. The second is
# small, so a source change rewrites little.
COPY --from=deps /build/.venv /build/.venv

# The bind mount lends this step uv and the wheel, so neither stays
# in the image. PYTHONDONTWRITEBYTECODE stops Python caching the
# standard-library modules it imports while installing, which would
# land in this layer. The package's own bytecode is still compiled.
RUN --mount=type=bind,from=wheel,target=/mnt \
    PYTHONDONTWRITEBYTECODE=1 \
    /mnt/bin/uv pip install --python /build/.venv --no-deps --no-cache \
      --compile-bytecode /mnt/build/dist/*.whl

# Simple CLI shim
RUN printf '%s\n' 'exec /build/.venv/bin/cgt-calc "$@"' > /bin/cgt-calc \
 && chmod +x /bin/cgt-calc

# CI runs the test suite from a workspace mounted over /build, which
# needs uv inside the container. This stage is last so plain builds
# (CI, local) get it by default; publishing targets the runtime stage.
FROM runtime AS test

# --link makes this layer independent of the ones below, so it is
# reused when they change. It cannot follow the /bin symlink, hence
# /usr/local/bin, which is on the PATH.
COPY --link --from=uv /uv /uvx /usr/local/bin/

# The tests of the version a build gets make Git repositories of their
# own.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
    && rm -rf /var/lib/apt/lists/*
