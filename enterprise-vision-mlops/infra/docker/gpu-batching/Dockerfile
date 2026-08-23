FROM enterprise-vision-mlops-efficientnet-training:latest

USER root
ARG SOURCE_REVISION=unknown

LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"

COPY apps/api/requirements.txt /tmp/api-requirements.txt
RUN pip install --no-cache-dir -r /tmp/api-requirements.txt

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY configs /app/configs
COPY domain_packs /app/domain_packs
COPY apps/api /app/apps/api
COPY apps/api/main.py /app/main.py
COPY scripts/dev /app/scripts/dev

ENV EVM_IMAGE_SOURCE_REVISION=${SOURCE_REVISION} \
    PYTHONPATH=/app/src

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
