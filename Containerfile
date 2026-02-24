ARG BASE_IMAGE=ghcr.io/snakepacker/python/3.11
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REFINER_HOST=0.0.0.0 \
    REFINER_FRONTEND_HOST=0.0.0.0

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN sed -i '/-proposed/d' /etc/apt/sources.list /etc/apt/sources.list.d/*.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        python3 \
        python3-pip \
        libffi-dev \
        libssl-dev \
        libjpeg-dev \
        zlib1g-dev \
        libfreetype6-dev \
        libpng-dev \
        pkg-config \
        ocl-icd-libopencl1 \
        ocl-icd-opencl-dev \
        opencl-headers \
        clinfo \
    && NVIDIA_PKG="$(apt-cache search '^nvidia-utils-[0-9]+' | awk '{print $1}' | sort -V | tail -n1)" \
    && if [ -n "$NVIDIA_PKG" ]; then \
        apt-get install -y --no-install-recommends "$NVIDIA_PKG"; \
    fi \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/container/entrypoint.sh \
    && install -m 0755 /app/container/nvidia-smi /usr/local/bin/nvidia-smi

EXPOSE 5001
EXPOSE 8080

ENTRYPOINT ["/app/container/entrypoint.sh"]
CMD ["full"]
