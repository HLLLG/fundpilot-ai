FROM python:3.12-slim

WORKDIR /app

COPY apps/sector-relay/requirements.txt /app/sector-relay-requirements.txt
RUN pip install --no-cache-dir -r /app/sector-relay-requirements.txt

RUN mkdir -p /app/app/services && touch /app/app/__init__.py /app/app/services/__init__.py

COPY apps/api/app/services/eastmoney_spot_client.py /app/app/services/eastmoney_spot_client.py
COPY apps/sector-relay/main.py /app/main.py

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8787

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8787"]
