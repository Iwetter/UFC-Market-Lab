FROM node:22-bookworm-slim AS dashboard
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UFC_SOURCE=kalshi UFC_DATABASE=/app/data/kalshi.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd --gid 10001 research && useradd --uid 10001 --gid research --no-create-home research
COPY backend/ ./backend/
COPY config/default.json ./config/default.json
COPY docs/ ./docs/
COPY artifacts/research/ ./artifacts/research/
COPY --from=dashboard /frontend/dist ./frontend/dist/
RUN mkdir -p /app/data && chown -R research:research /app/data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD ["python","-c","import json,time; p=json.load(open('/app/data/collector-health.json')); assert p['ok'] and time.time()*1000-p['updated_ms']<90000"]
CMD ["python","-m","backend.server"]
