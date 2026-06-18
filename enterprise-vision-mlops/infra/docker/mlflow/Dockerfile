FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    mlflow==2.18.0 \
    boto3==1.35.76 \
    psycopg2-binary==2.9.10

EXPOSE 5000
