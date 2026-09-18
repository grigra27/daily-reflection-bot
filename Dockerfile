FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

# Alembic configuration and migrations.
COPY alembic.ini ./
COPY migrations ./migrations

# Run as an unprivileged user and keep the SQLite store writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV DATABASE_URL=sqlite:////app/data/reflection.db \
    DEFAULT_TIMEZONE=Europe/Moscow

CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]
