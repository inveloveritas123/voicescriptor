FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno for yt-dlp JS challenge solving
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY frontend/ ./frontend/

RUN mkdir -p /app/data/downloads /app/data/transcripts /app/data/models

ENV HF_HOME=/app/data/models
ENV XDG_CACHE_HOME=/app/data/models

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]
