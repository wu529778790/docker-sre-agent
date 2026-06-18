FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

EXPOSE 8080

ENTRYPOINT ["docker-sre"]
CMD ["web", "--port", "8080"]
