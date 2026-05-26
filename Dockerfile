FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    nodejs \
    npm \
    fonts-noto-cjk \
    fontconfig \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY fonts ./fonts
COPY main.py ./main.py

RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

ENTRYPOINT ["/app/.venv/bin/y2b"]
CMD ["--help"]
