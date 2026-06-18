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

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:6700/')" || exit 1

ENTRYPOINT ["docker-sre"]
CMD ["web", "--port", "6700"]
