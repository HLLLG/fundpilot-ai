FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ocr.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir -r /app/requirements-ocr.txt

COPY app /app/app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV FUND_AI_OCR_PRELOAD=false
ENV DISABLE_MODEL_SOURCE_CHECK=True

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
