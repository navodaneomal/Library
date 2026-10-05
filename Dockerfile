# Library Management System — production image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 1000 library

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Static files are collected at build time (no secrets needed for this step).
RUN SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput \
    && mkdir -p /app/media /app/backups /app/data \
    && chown -R library:library /app/media /app/backups /app/data \
    && chmod +x /app/deploy/entrypoint.sh /app/deploy/scheduler.sh

USER library
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
