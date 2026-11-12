# Dockerfile for StellarScope Backend
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/src ./src
COPY alembic.ini .
COPY alembic ./alembic

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "stellarapi.main:app", "--host", "0.0.0.0", "--port", "8000"]