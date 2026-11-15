# TrialRisk: website + API. Render builds this file and runs it on the free plan (512 MB of memory).
FROM python:3.13-slim

# LightGBM needs libgomp1. The settings keep memory low, show logs right away, and keep the AI model inside the image.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MALLOC_ARENA_MAX=2 \
    FASTEMBED_CACHE_PATH=/app/model/fastembed_cache

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python build.py

EXPOSE 10000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
