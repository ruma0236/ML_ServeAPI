FROM python:3.11-slim

ARG SOURCE_REVISION=unknown

LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    EVM_IMAGE_SOURCE_REVISION=${SOURCE_REVISION}

WORKDIR /app

COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pyproject.toml docker-compose.yml /app/
COPY src /app/src
COPY configs /app/configs
COPY domain_packs /app/domain_packs
COPY apps/api /app/apps/api
COPY apps/api/main.py /app/main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
