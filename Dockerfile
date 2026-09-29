# syntax=docker/dockerfile:1
# ---- stage 1: build the web app -------------------------------------------
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Optional: override the map tiles at build time (--build-arg VITE_TILE_URL=...)
ARG VITE_TILE_URL=
ARG VITE_TILE_ATTRIBUTION=
ENV VITE_TILE_URL=$VITE_TILE_URL VITE_TILE_ATTRIBUTION=$VITE_TILE_ATTRIBUTION
RUN npm run build

# ---- stage 2: API + built SPA ---------------------------------------------
# 3.12: the OCR dependency (rapidocr-onnxruntime) has no 3.13 wheels.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY nwis ./nwis
COPY api ./api
COPY models ./models
COPY data ./data
COPY scripts ./scripts
COPY docs ./docs
COPY --from=web /web/dist ./web/dist

# Non-root (uid 1000, same as Hugging Face Spaces). The app dir must be writable:
# review-queue decisions go to data/nwis.sqlite and the demo cache to models/.
RUN useradd -m -u 1000 user && chown -R 1000:1000 /app
USER 1000
ENV HOME=/home/user

EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
