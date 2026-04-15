ARG BASE_IMAGE=python:3.11-slim-bookworm

FROM ${BASE_IMAGE} AS source-metadata

ARG BUILD_NUMBER=0
ARG GIT_COMMIT=unknown

WORKDIR /src

COPY . /src

RUN case "${BUILD_NUMBER}" in \
        ''|*[!0-9]*) build_number=0 ;; \
        *) build_number="${BUILD_NUMBER}" ;; \
    esac \
    && git_commit="${GIT_COMMIT:-unknown}" \
    && printf '{"build_number":%s,"commit":"%s"}\n' "${build_number}" "${git_commit}" > /src/.refiner-build.json

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
    REFINER_HOST=0.0.0.0 \
    REFINER_PORT=5001 \
    REFINER_FRONTEND_HOST=0.0.0.0 \
    REFINER_FRONTEND_PORT=8080 \
    REFINER_JOB_DIR=/app/job_data

WORKDIR ${APP_HOME}

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
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && groupadd --gid "${APP_GID}" "${APP_USER}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/sh "${APP_USER}"

COPY --from=source-metadata /src ${APP_HOME}
RUN python -m pip install --no-cache-dir -e ${APP_HOME} \
    && chmod +x ${APP_HOME}/container/entrypoint.sh \
    && chmod +x ${APP_HOME}/scripts/start_refiner_stack.sh \
    && install -m 0755 ${APP_HOME}/container/nvidia-smi /usr/local/bin/nvidia-smi \
    && mkdir -p ${REFINER_JOB_DIR} /tmp/refiner \
    && chown -R "${APP_UID}:${APP_GID}" ${APP_HOME} /tmp/refiner

EXPOSE 5001
EXPOSE 8080

VOLUME ["/app/job_data"]

USER ${APP_UID}:${APP_GID}

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD sh -c 'curl -fsS "http://127.0.0.1:${REFINER_PORT:-5001}/api/health" >/dev/null || exit 1'

ENTRYPOINT ["/usr/bin/tini", "--", "/app/container/entrypoint.sh"]
CMD ["full"]
