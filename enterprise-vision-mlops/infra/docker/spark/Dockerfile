FROM apache/spark:3.5.5-python3

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/evm/src

WORKDIR /opt/evm

COPY src /opt/evm/src

RUN mkdir -p /opt/spark/work-dir /mnt/evm-data \
    && chown -R spark:spark /opt/evm /opt/spark/work-dir /mnt/evm-data

USER spark
