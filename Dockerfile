FROM python:3.12-slim

# Install Docker CLI (connects to host via mounted socket)
RUN apt-get update && \
    apt-get install -y --no-install-recommends docker.io && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

EXPOSE 6700

ENTRYPOINT ["docker-sre"]
CMD ["web", "--port", "6700"]
