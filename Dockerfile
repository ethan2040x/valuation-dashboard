# Stage 1: Build frontend (both dynamic bundle and single-file static template)
FROM node:22-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build && npm run build:static

# Stage 2: Python backend runtime serving API + frontend + scheduler
FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VALUATION_DATA_DIR=/app/data \
    VALUATION_DB_PATH=/app/data/valuation.db \
    VALUATION_FRONTEND_DIST=/app/frontend/dist \
    VALUATION_SINGLEFILE_DIST=/app/frontend/dist-singlefile \
    VALUATION_EXPORT_PATH=/app/dist/ndx-valuation-snapshot.html

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
RUN pip install --no-cache-dir /app/backend

COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist
COPY --from=frontend-builder /build/frontend/dist-singlefile /app/frontend/dist-singlefile
COPY data/seed /app/data/seed

EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
