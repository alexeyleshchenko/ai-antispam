# Stage 1: builder
FROM python:3.14-alpine AS builder

WORKDIR /app

# Install uv first (layer cached separately)
RUN pip install --no-cache-dir uv

# Copy dependency files FIRST (before app code)
# This layer only invalidates when lockfile/pyproject changes
COPY pyproject.toml ./
COPY uv.lock ./

# Install deps strictly from the lockfile for reproducible builds.
# `uv export --no-emit-project` materializes a requirements.txt of pure deps
# from uv.lock (no project build needed), so the deps layer stays cached
# independently of src/ changes.
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-editable --no-emit-project -o /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Stage 2: runner
FROM python:3.14-alpine

RUN apk add --no-cache curl

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY pyproject.toml ./
COPY --chown=appuser:appuser PRD.md config.yaml ./
COPY src/app ./app/

RUN addgroup -S -g 1000 appuser && \
    adduser -S -u 1000 -H appuser && \
    mkdir -p logs && chown -R appuser:appuser /app

USER appuser

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=1 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "-m", "app.main"]
