# syntax=docker/dockerfile:1
#
# Refiner Containerfile
# ---------------------
# Goal:
#   Build a lean runtime image for the Refiner API/runtime stack.
#
# Workflow:
#   1. Use a Python slim Debian base image.
#   2. Install build-time packages only in the builder stage.
#   3. Create and populate a Python virtual environment under /opt/venv.
#   4. Copy the source tree, generate build metadata, and create the Refiner
#      capabilities report at build time.
#   5. Remove development-only files from the image context.
#   6. Optionally compile Python files to legacy .pyc files and remove .py
#      source files, matching the original image-hardening/size-reduction intent.
#   7. Copy only the prepared application and virtual environment into a smaller
#      non-root runtime stage.
#
# Notes:
#   - Comments use UK English.
#   - The image defaults to Europe/London timezone settings.
#   - Package lists are intentionally split between build and runtime stages to
#     avoid carrying compiler toolchains into production.
#   - This file avoids BuildKit-only cache mounts so that it remains friendly to
#     Docker, Podman and Buildah-style workflows.

ARG BASE_IMAGE=python:3.11-slim-bookworm

# -----------------------------------------------------------------------------
# Shared defaults
# -----------------------------------------------------------------------------
# Keep these ARG values before each FROM if they need to be reused across stages.
ARG APP_HOME=/app
ARG APP_USER=refiner
ARG APP_UID=10001
ARG APP_GID=10001
ARG TZ=Europe/London

# -----------------------------------------------------------------------------
# Builder stage: install dependencies, prepare source and generate metadata.
# -----------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS builder

ARG BUILD_NUMBER=0
ARG GIT_COMMIT=unknown
ARG APP_HOME
ARG TZ

# Set Python and Debian defaults early so every following command inherits them.
# PYTHONDONTWRITEBYTECODE avoids stray __pycache__ creation during the build.
# PYTHONUNBUFFERED keeps container logs immediate and Kubernetes-friendly.
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=${TZ} \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /src

# Build-only packages are deliberately kept out of the final runtime image.
# Runtime-linked development headers are needed here because several Python
# packages may compile native extensions during pip installation.
ARG BUILD_PACKAGES="\
    bash \
    ca-certificates \
    curl \
    ffmpeg \
    git \
    tini \
    tzdata \
    build-essential \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libfreetype6-dev \
    libpng-dev \
    pkg-config \
    ocl-icd-libopencl1 \
    ocl-icd-opencl-dev \
    opencl-headers \
    clinfo"

# Copy dependency metadata before the full source tree to maximise layer reuse.
COPY requirements.txt /tmp/requirements.txt

RUN set -eux; \
    # Defensive clean-up: avoid accidentally pulling from Debian proposed repos. \
    sed -i '/-proposed/d' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true; \
    apt-get update; \
    apt-get install -y --no-install-recommends ${BUILD_PACKAGES}; \
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime; \
    echo "${TZ}" > /etc/timezone; \
    python -m venv "${VIRTUAL_ENV}"; \
    pip install --upgrade pip setuptools wheel; \
    pip install --requirement /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt; \
    rm -rf /var/lib/apt/lists/*

# Copy the application after dependency installation to avoid reinstalling
# packages whenever application-only files change.
COPY . /src

# Write reproducible build metadata. BUILD_NUMBER is normalised so downstream
# tooling can safely parse it as JSON numeric data even if CI passes a bad value.
RUN set -eux; \
    case "${BUILD_NUMBER}" in \
        ''|*[!0-9]*) build_number=0 ;; \
        *) build_number="${BUILD_NUMBER}" ;; \
    esac; \
    git_commit="${GIT_COMMIT:-unknown}"; \
    printf '{"build_number":%s,"commit":"%s"}\n' "${build_number}" "${git_commit}" > /src/.refiner-build.json

# Generate the application capability report during the image build. This bakes a
# snapshot of repository capability metadata into the runtime image.
RUN set -eux; \
    mkdir -p /src/data; \
    PYTHONPATH=/src python - <<'PY'
import json
from refiner.capability_analyzer import analyse_repo

with open('/src/data/capabilities_report.json', 'w', encoding='utf-8') as handle:
    json.dump(analyse_repo('/src'), handle, ensure_ascii=True)
PY

# Remove files that are useful during development but should not be included in
# the runtime image. Keep this list explicit so accidental future exclusions are
# easier to review during code review.
RUN set -eux; \
    rm -rf \
        /src/.agent \
        /src/.github \
        /src/.idea \
        /src/.pytest_cache \
        /src/.research_cache \
        /src/.venv \
        /src/venv \
        /src/job_data \
        /src/tests; \
    rm -f \
        /src/confluence_report.html \
        /src/gantt_projects.html \
        /src/jira_report.html \
        /src/kpis.html \
        /src/output.md; \
    find /src -maxdepth 1 -type f \( \
        -name '*.md' -o \
        -name 'CHANGELOG' -o \
        -name 'Containerfile' -o \
        -name 'LICENSE' -o \
        -name 'pyproject.toml' -o \
        -name 'requirements.txt' \
    \) -delete

# The original Containerfile compiled Python to legacy .pyc and removed .py
# files. That can reduce source exposure and image size, but it makes debugging
# harder and can break code that expects source files to exist. Keep the original
# behaviour as the default, but allow CI/development builds to opt out with:
#   --build-arg STRIP_PY_SOURCES=0
ARG STRIP_PY_SOURCES=1
RUN set -eux; \
    STRIP_PY_SOURCES="${STRIP_PY_SOURCES}" python - <<'PY'
import compileall
import os
import pathlib
import shutil

if os.environ.get('STRIP_PY_SOURCES') == '1':
    root = pathlib.Path('/src')
    compileall.compile_dir(str(root), force=True, quiet=1, legacy=True)
    for path in root.rglob('*.py'):
        path.unlink()
    for cache_dir in sorted(root.rglob('__pycache__'), reverse=True):
        shutil.rmtree(cache_dir)
PY

# Validate expected executable assets before copying into the runtime image.
RUN set -eux; \
    test -f /src/container/entrypoint.sh; \
    test -f /src/scripts/start_refiner_stack.sh; \
    test -f /src/container/nvidia-smi; \
    chmod 0755 /src/container/entrypoint.sh /src/scripts/start_refiner_stack.sh; \
    install -m 0755 /src/container/nvidia-smi /usr/local/bin/nvidia-smi

# -----------------------------------------------------------------------------
# Runtime stage: install only runtime OS packages and run as a non-root user.
# -----------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

ARG BASE_IMAGE
ARG APP_HOME
ARG APP_USER
ARG APP_UID
ARG APP_GID
ARG TZ

LABEL org.opencontainers.image.title="Refiner" \
      org.opencontainers.image.description="Refiner API/runtime image for Podman and Kubernetes" \
      org.opencontainers.image.source="https://github.com/neuralmimicry" \
      org.opencontainers.image.vendor="NeuralMimicry" \
      org.opencontainers.image.base.name="${BASE_IMAGE}"

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=${TZ} \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    REFINER_HOST=0.0.0.0 \
    REFINER_PORT=5001 \
    REFINER_FRONTEND_HOST=0.0.0.0 \
    REFINER_FRONTEND_PORT=8080 \
    REFINER_JOB_DIR=/app/job_data \
    REFINER_CAPABILITIES_REPORT_PATH=/app/data/capabilities_report.json

WORKDIR ${APP_HOME}

# Runtime packages only. Compiler toolchains and headers remain in the builder
# stage, reducing attack surface and image size.
ARG RUNTIME_PACKAGES="\
    bash \
    ca-certificates \
    curl \
    ffmpeg \
    git \
    tini \
    tzdata \
    libffi8 \
    libssl3 \
    libjpeg62-turbo \
    zlib1g \
    libfreetype6 \
    libpng16-16 \
    ocl-icd-libopencl1 \
    clinfo"

RUN set -eux; \
    sed -i '/-proposed/d' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true; \
    apt-get update; \
    apt-get install -y --no-install-recommends ${RUNTIME_PACKAGES}; \
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime; \
    echo "${TZ}" > /etc/timezone; \
    groupadd --gid "${APP_GID}" "${APP_USER}"; \
    useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --shell /bin/sh \
        "${APP_USER}"; \
    mkdir -p "${REFINER_JOB_DIR}" /tmp/refiner; \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src ${APP_HOME}
COPY --from=builder /usr/local/bin/nvidia-smi /usr/local/bin/nvidia-smi

RUN set -eux; \
    chown -R "${APP_UID}:${APP_GID}" "${APP_HOME}" /tmp/refiner

EXPOSE 5001 8080

# Persist user-created or job-generated data outside the immutable image layer.
VOLUME ["/app/job_data"]

USER ${APP_UID}:${APP_GID}

# Health check the local API endpoint. The long start period allows for slower
# first starts on CPU-only or constrained single-node environments.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD sh -c 'curl -fsS "http://127.0.0.1:${REFINER_PORT:-5001}/api/health" >/dev/null || exit 1'

ENTRYPOINT ["/usr/bin/tini", "--", "/app/container/entrypoint.sh"]
CMD ["full"]
