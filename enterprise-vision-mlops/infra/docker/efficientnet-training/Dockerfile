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
      "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
    && pip install --no-cache-dir \
      boto3==1.35.76 \
      matplotlib==3.9.2 \
      optuna==4.9.0 \
      pillow==12.2.0 \
      pydantic==2.10.3 \
      requests==2.32.3 \
      scikit-learn==1.5.2

RUN useradd --create-home --uid 10001 evm

COPY pyproject.toml docker-compose.yml /app/
COPY src /app/src
COPY configs/w7_efficientnet_kubernetes.toml /app/configs/w7_efficientnet_kubernetes.toml

USER 10001:10001

CMD ["python", "-m", "evm.pipelines.efficientnet_training.run", "/app/configs/w7_efficientnet_kubernetes.toml"]
