ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REFINER_HOST=0.0.0.0 \
    REFINER_FRONTEND_HOST=0.0.0.0

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
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
    && if apt-cache show nvidia-utils >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends nvidia-utils; \
    elif apt-cache show nvidia-utils-535 >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends nvidia-utils-535; \
    elif apt-cache show nvidia-utils-525 >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends nvidia-utils-525; \
    fi \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/container/entrypoint.sh \
    && install -m 0755 /app/container/nvidia-smi /usr/local/bin/nvidia-smi

EXPOSE 5001
EXPOSE 8080

ENTRYPOINT ["/app/container/entrypoint.sh"]
CMD ["full"]
