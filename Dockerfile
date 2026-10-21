# syntax=docker/dockerfile:1.7

# Optional extras to install, e.g. `dev` for the test image. Empty for
# the production image.
ARG EXTRAS=""

# ─── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ARG EXTRAS

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Build wheels for everything the project needs. The three packages
# below are the ones that may need to be compiled from source; the
# rest resolve to prebuilt wheels.
RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir=/wheels \
       "bcrypt==4.2.1" \
       "psycopg2-binary==2.9.10" \
       "cryptography==44.0.0"

COPY . /build
# Build the project wheel *and* its dependencies into /wheels, so the
# runtime stage can install everything offline. (The previous
# `--no-deps` install skipped the dependencies entirely, which left
# the image without FastAPI, SQLModel, and friends.)
#
# `rapidocr` depends on the GUI build of OpenCV, which needs X11
# libraries (`libxcb`, `libGL`) that a slim image does not have. Both
# variants install the same `cv2` module, so we drop the GUI build and
# reinstall the headless one - the one this project actually declares.
RUN set -eux; \
    if [ -n "$EXTRAS" ]; then \
        pip wheel --wheel-dir=/wheels ".[$EXTRAS]"; \
        pip install --no-index --find-links=/wheels "auto-pay-ai[$EXTRAS]"; \
    else \
        pip wheel --wheel-dir=/wheels .; \
        pip install --no-index --find-links=/wheels "auto-pay-ai"; \
    fi; \
    pip uninstall -y opencv-python; \
    pip install --no-index --find-links=/wheels --force-reinstall opencv-python-headless

# ─── Stage 2: runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ARG EXTRAS

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 autopay \
    && useradd  --system --uid 1001 --gid autopay --create-home --shell /bin/bash autopay

WORKDIR /code

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=autopay:autopay . /code
# `COPY --chown` sets the ownership of the copied *contents*, not of
# /code itself - which WORKDIR created as root. Without this the
# non-root `autopay` user cannot write to its working directory.
RUN chown -R autopay:autopay /code

USER autopay

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

COPY --chown=autopay:autopay scripts/entrypoint.sh /code/scripts/entrypoint.sh
RUN chmod +x /code/scripts/entrypoint.sh

ENTRYPOINT ["/code/scripts/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
