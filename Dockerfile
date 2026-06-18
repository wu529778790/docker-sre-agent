FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

EXPOSE 6668

ENTRYPOINT ["docker-sre"]
CMD ["web", "--port", "6668"]
