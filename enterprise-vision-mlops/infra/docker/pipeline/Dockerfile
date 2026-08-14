FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN pip install --no-cache-dir \
    boto3==1.35.76 \
    opentelemetry-api==1.44.0 \
    opentelemetry-exporter-otlp-proto-http==1.44.0 \
    opentelemetry-sdk==1.44.0 \
    pyarrow==18.1.0

COPY pyproject.toml docker-compose.yml /app/
COPY src /app/src
COPY scripts /app/scripts
COPY configs /app/configs
COPY domain_packs /app/domain_packs

ENTRYPOINT ["python", "scripts/run_pipeline.py"]
