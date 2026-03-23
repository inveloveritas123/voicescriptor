FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ffmpeg weasyprint \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/reports /app/data/uploads /app/data/models

ENV HF_HOME=/app/data/models
ENV XDG_CACHE_HOME=/app/data/models

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
