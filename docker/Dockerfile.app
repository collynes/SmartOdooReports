# Single image used by both the Flask reports webapp (app.py)
# and the FastAPI mobile API (mobile_api.py).
# The compose file picks which entrypoint runs per service.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OS deps:
#   tesseract-ocr     - receipt_ocr.py subprocess
#   libgl1, libglib2  - opencv-python-headless runtime libs
#   curl              - healthcheck
#   build-essential, libpq-dev - safety net if any dep falls back to source build
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
        curl \
        ca-certificates \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python deps first to leverage layer cache
COPY docker/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Copy the rest of the repo
COPY . /app

# Ensure log dir exists (matches ecosystem.config.js layout, but inside container)
RUN mkdir -p /app/logs

# Default port = Flask app; mobile-api service overrides via env + command
ENV PORT=1989

EXPOSE 1989 8800

# Default command runs the Flask reports webapp; the mobile-api service
# overrides this with `python mobile_api.py` in docker-compose.yml.
CMD ["python", "app.py"]
