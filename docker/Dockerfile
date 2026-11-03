# Dockerfile for StellarScope Backend
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/src ./src

EXPOSE 8000

CMD ["uvicorn", "stellarapi.main:app", "--host", "0.0.0.0", "--port", "8000"]