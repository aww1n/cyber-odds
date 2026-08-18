FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra telegram

COPY alembic.ini ./
COPY config ./config
COPY research/output ./research/output

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/data/raw \
    && chown -R app:app /app

USER app

CMD ["python", "-m", "app", "--help"]
