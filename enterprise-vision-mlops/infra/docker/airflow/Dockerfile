FROM apache/airflow:2.10.5-python3.11

USER root

RUN apt-get update \
    && apt-get install --no-install-recommends -y openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

USER airflow

RUN pip install --no-cache-dir \
    boto3==1.35.76 \
    opentelemetry-api==1.44.0 \
    opentelemetry-exporter-otlp-proto-http==1.44.0 \
    opentelemetry-sdk==1.44.0 \
    pyarrow==18.1.0 \
    pyspark==3.5.8
