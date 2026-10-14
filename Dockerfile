# Stage 1: build the web UI
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: API + built UI
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
WORKDIR /srv
COPY requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY app/ app/
COPY scripts/ scripts/
COPY fixtures/ fixtures/
COPY --from=web /web/dist web/dist
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p data \
    && python -m scripts.reset_db --scenario golden \
    && chown -R appuser:appuser /srv
USER appuser
EXPOSE 10000
CMD sh -c "exec uvicorn app.serve:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --no-access-log"
