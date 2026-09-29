FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    TZ=Europe/Kiev

WORKDIR /app

# System deps for Pillow / matplotlib fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
        fonts-dejavu-core \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data

# Healthcheck: the bot writes a heartbeat file; if it's stale the container is unhealthy
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m app.healthcheck || exit 1

CMD ["python", "-m", "app.main"]
