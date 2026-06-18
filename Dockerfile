FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

ENTRYPOINT ["docker-sre"]
CMD ["run", "--config", "/app/config.yaml"]
