# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./

# Install CPU-only torch FIRST, from the PyTorch CPU index. Without this
# pip grabs the GPU build which bundles the entire NVIDIA CUDA stack
# (cublas, cudnn, cuFFT, cuSPARSE, cuSOLVER, nccl, triton, ...) — close
# to 2 GB of wheels we never use. Pre-installing CPU torch first means
# sentence-transformers picks it up as already-satisfied and pip skips
# the GPU pull entirely. Keeps the worker image ~250 MB instead of ~3 GB.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.4.1"

RUN pip install -r requirements.txt

# Pre-download the sentence-transformers model so the image is self-contained
# and the first request doesn't pay the ~80MB download latency. Cached layer
# stays warm across rebuilds when requirements.txt hasn't changed.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .

# Make the entrypoint executable (works even if host bit is missing).
RUN chmod +x scripts/entrypoint.sh

# Uploads volume mount point.
RUN mkdir -p /app/uploads

EXPOSE 8000

ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
