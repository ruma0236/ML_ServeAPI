FROM apache/airflow:2.10.5-python3.11

USER airflow

RUN pip install --no-cache-dir \
    boto3==1.35.76 \
    opentelemetry-api==1.44.0 \
    opentelemetry-exporter-otlp-proto-http==1.44.0 \
    opentelemetry-sdk==1.44.0 \
    pyarrow==18.1.0 \
    pyspark==3.5.8
