ARG BASE_IMAGE=python:3.11-slim-bookworm

FROM ${BASE_IMAGE} AS source-metadata

ARG BUILD_NUMBER=0
ARG GIT_COMMIT=unknown

WORKDIR /src

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /tmp/requirements.txt
RUN sed -i '/-proposed/d' /etc/apt/sources.list /etc/apt/sources.list.d/*.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        tini \
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
        clinfo \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY . /src

RUN case "${BUILD_NUMBER}" in \
        ''|*[!0-9]*) build_number=0 ;; \
        *) build_number="${BUILD_NUMBER}" ;; \
    esac \
    && git_commit="${GIT_COMMIT:-unknown}" \
    && printf '{"build_number":%s,"commit":"%s"}\n' "${build_number}" "${git_commit}" > /src/.refiner-build.json

RUN mkdir -p /src/data \
    && PYTHONPATH=/src /opt/venv/bin/python - <<'PY'
import json
from refiner.capability_analyzer import analyse_repo

with open("/src/data/capabilities_report.json", "w", encoding="utf-8") as handle:
    json.dump(analyse_repo("/src"), handle, ensure_ascii=True)
PY

RUN rm -rf \
        /src/.agent \
        /src/.github \
        /src/.idea \
        /src/.pytest_cache \
        /src/.research_cache \
        /src/.venv \
        /src/venv \
        /src/job_data \
        /src/tests \
    && rm -f \
        /src/confluence_report.html \
        /src/gantt_projects.html \
        /src/jira_report.html \
        /src/kpis.html \
        /src/output.md \
    && find /src -maxdepth 1 -type f \( \
        -name '*.md' -o \
        -name 'CHANGELOG' -o \
        -name 'Containerfile' -o \
        -name 'LICENSE' -o \
        -name 'pyproject.toml' -o \
        -name 'requirements.txt' \
      \) -delete

RUN /opt/venv/bin/python - <<'PY'
import compileall
import pathlib
import shutil

root = pathlib.Path("/src")
compileall.compile_dir(str(root), force=True, quiet=1, legacy=True)
for path in root.rglob("*.py"):
    path.unlink()
for cache_dir in sorted(root.rglob("__pycache__"), reverse=True):
    shutil.rmtree(cache_dir)
PY

RUN chmod +x /src/container/entrypoint.sh \
    && chmod +x /src/scripts/start_refiner_stack.sh \
    && install -m 0755 /src/container/nvidia-smi /usr/local/bin/nvidia-smi

FROM ${BASE_IMAGE}

ARG APP_HOME=/app
ARG APP_USER=refiner
ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="Refiner" \
      org.opencontainers.image.description="Refiner API/runtime image for Podman and Kubernetes" \
      org.opencontainers.image.source="https://github.com/neuralmimicry"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}" \
    REFINER_HOST=0.0.0.0 \
    REFINER_PORT=5001 \
    REFINER_FRONTEND_HOST=0.0.0.0 \
    REFINER_FRONTEND_PORT=8080 \
    REFINER_JOB_DIR=/app/job_data \
    REFINER_CAPABILITIES_REPORT_PATH=/app/data/capabilities_report.json

WORKDIR ${APP_HOME}

RUN sed -i '/-proposed/d' /etc/apt/sources.list /etc/apt/sources.list.d/*.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        tini \
        libffi8 \
        libssl3 \
        libjpeg62-turbo \
        zlib1g \
        libfreetype6 \
        libpng16-16 \
        ocl-icd-libopencl1 \
        clinfo \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" "${APP_USER}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/sh "${APP_USER}" \
    && mkdir -p ${REFINER_JOB_DIR} /tmp/refiner

COPY --from=source-metadata /opt/venv /opt/venv
COPY --from=source-metadata /src ${APP_HOME}
COPY --from=source-metadata /usr/local/bin/nvidia-smi /usr/local/bin/nvidia-smi
RUN chown -R "${APP_UID}:${APP_GID}" ${APP_HOME} /tmp/refiner

EXPOSE 5001
EXPOSE 8080

VOLUME ["/app/job_data"]

USER ${APP_UID}:${APP_GID}

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD sh -c 'curl -fsS "http://127.0.0.1:${REFINER_PORT:-5001}/api/health" >/dev/null || exit 1'

ENTRYPOINT ["/usr/bin/tini", "--", "/app/container/entrypoint.sh"]
CMD ["full"]
