# One Dockerfile for every Flask service. Build context = repo root.
#   docker build -f services/Dockerfile --build-arg SERVICE=ingestion .
# The report service will get its own stage with Playwright/Chromium in Phase 5.
FROM python:3.12-slim

ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE=${SERVICE}

WORKDIR /srv
COPY shared /srv/shared
RUN pip install --no-cache-dir /srv/shared

COPY services/${SERVICE}/ /srv/service/
WORKDIR /srv/service
# Extra per-service dependencies (e.g. openpyxl + boto3 for ingestion), if the service has any.
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

EXPOSE 8000
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:8000 -k gthread --threads ${GUNICORN_THREADS:-4} wsgi:app"]
