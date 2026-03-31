ARG BASE_IMAGE=python:3.11-slim-bookworm
ARG RUST_BASE_IMAGE=rust:slim-bookworm

FROM ${RUST_BASE_IMAGE} AS stt-builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        clang \
        cmake \
        libclang-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY stt_rust /build/stt_rust
WORKDIR /build/stt_rust
RUN cargo build --locked --release

FROM ${BASE_IMAGE} AS source-metadata

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . /src

RUN build_number="$(git rev-list --count HEAD 2>/dev/null || echo 0)" \
    && git_commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
    && printf '{"build_number":%s,"commit":"%s"}\n' "${build_number}" "${git_commit}" > /src/.refiner-build.json \
    && rm -rf /src/.git

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
    REFINER_JOB_DIR=/app/job_data \
    STT_BIND=127.0.0.1:7079 \
    STT_MODEL=/app/job_data/models/ggml-tiny.en.bin

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
COPY --from=stt-builder /build/stt_rust/target/release/refiner-stt /tmp/refiner-stt
RUN python -m pip install --no-cache-dir -e ${APP_HOME} \
    && chmod +x ${APP_HOME}/container/entrypoint.sh \
    && chmod +x ${APP_HOME}/scripts/start_refiner_stack.sh \
    && install -m 0755 ${APP_HOME}/container/nvidia-smi /usr/local/bin/nvidia-smi \
    && install -D -m 0755 /tmp/refiner-stt ${APP_HOME}/stt_rust/target/release/refiner-stt \
    && rm -f /tmp/refiner-stt \
    && mkdir -p ${REFINER_JOB_DIR} ${REFINER_JOB_DIR}/models /tmp/refiner \
    && chown -R "${APP_UID}:${APP_GID}" ${APP_HOME} /tmp/refiner

EXPOSE 5001
EXPOSE 8080

VOLUME ["/app/job_data"]

USER ${APP_UID}:${APP_GID}

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD sh -c 'curl -fsS "http://127.0.0.1:${REFINER_PORT:-5001}/api/health" >/dev/null || exit 1'

ENTRYPOINT ["/usr/bin/tini", "--", "/app/container/entrypoint.sh"]
CMD ["full"]
