FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir docker pyyaml

COPY docker_sre_agent/ docker_sre_agent/
COPY config.yaml .

RUN mkdir -p /app/data

ENTRYPOINT ["docker-sre"]
CMD ["--config", "/app/config.yaml"]
