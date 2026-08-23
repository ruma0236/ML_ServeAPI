FROM python:3.11-slim

ARG TORCH_VERSION=2.13.0+cu126
ARG TORCHVISION_VERSION=0.28.0+cu126

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu126 \
      "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}"

RUN pip install --no-cache-dir \
      fastapi==0.115.6 \
      pillow==12.2.0 \
      pydantic==2.10.3 \
      "uvicorn[standard]==0.32.1"

RUN pip install --no-cache-dir \
      opentelemetry-api==1.44.0 \
      opentelemetry-exporter-otlp-proto-http==1.44.0 \
      opentelemetry-sdk==1.44.0 \
      prometheus-client==0.21.1

ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
ENV EVM_IMAGE_SOURCE_REVISION=${SOURCE_REVISION}

RUN useradd --create-home --uid 10001 evm

COPY pyproject.toml docker-compose.yml /app/
COPY src /app/src
COPY apps /app/apps
COPY configs/s7_family_admission.toml /app/configs/s7_family_admission.toml

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "apps.api.efficientnet_serving:app", "--host", "0.0.0.0", "--port", "8000"]
