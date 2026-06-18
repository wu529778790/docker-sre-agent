FROM python:3.12-slim

# Install Docker CLI (official binary)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar -xz --strip-components=1 -C /usr/local/bin docker/docker && \
    apt-get purge -y curl && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir .

RUN mkdir -p /app/data

EXPOSE 6700

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:6700/')" || exit 1

ENTRYPOINT ["docker-sre"]
CMD ["web", "--port", "6700"]
