FROM --platform=linux/amd64 locustio/locust:2.46.5@sha256:d1dd30fdb0940e4623606331bfdc5a37953c1820b49b3e2965badfec1de18338

USER root

COPY --chown=root:root requirements.txt /tmp/container-test-requirements.txt

RUN /opt/venv/bin/python -m pip install \
      --no-cache-dir \
      --index-url https://pypi.org/simple \
      --only-binary=:all: \
      --require-hashes \
      --no-deps \
      --requirement /tmp/container-test-requirements.txt \
    && /opt/venv/bin/python -m pip check \
    && rm -f /tmp/container-test-requirements.txt

USER locust
WORKDIR /home/locust

ENTRYPOINT ["/opt/venv/bin/python"]
CMD ["-m", "locust", "--version"]
