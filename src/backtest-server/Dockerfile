# Backtest MCP Server Docker Image
#
# Multi-stage build: stage 1 builds the Python environment and compiles
# polars-talib from upstream source, stage 2 is the slim runtime image.

# --- Stage 1: Build dependencies and polars-talib from source ---
FROM python:3.12-slim AS builder
ARG POLARS_TALIB_REF=0.1.5
ARG TA_LIB_VERSION=0.4.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    autotools-dev \
    build-essential \
    clang \
    curl \
    file \
    git \
    libclang-dev \
    pkg-config && \
    rm -rf /var/lib/apt/lists/*

# Install Rust (needed to compile polars-talib from source)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:/app/.venv/bin:${PATH}" \
    LIBCLANG_PATH="/usr/lib/llvm-19/lib"

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for better build caching.
COPY pyproject.toml .
COPY VERSION .

# PyPI only ships wheels for polars-talib, so build the package from the
# upstream source tag instead of relying on a prebuilt arm64 wheel.
#
# On Apple Silicon, the bundled TA-Lib 0.4.0 autotools files are too old to
# detect aarch64 correctly. Refreshing them with Debian's autotools-dev copies
# (installed above) avoids that failure and removes a flaky network fetch from
# git.savannah.gnu.org during the build.
RUN git clone --depth 1 --branch "${POLARS_TALIB_REF}" \
    https://github.com/Yvictor/polars_ta_extension.git /tmp/polars_ta_extension && \
    mkdir -p /tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib && \
    curl -fsSL \
    "https://github.com/Yvictor/polars_ta_extension/releases/download/0.1.0/ta-lib-${TA_LIB_VERSION}-src.tar.gz" \
    -o "/tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib-${TA_LIB_VERSION}-src.tar.gz" && \
    tar -xzf "/tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib-${TA_LIB_VERSION}-src.tar.gz" \
    --strip-components=1 \
    -C /tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib && \
    cp /usr/share/misc/config.guess /tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib/config.guess && \
    cp /usr/share/misc/config.sub /tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib/config.sub && \
    cd /tmp/polars_ta_extension/talib-sys/dependencies/tmp/ta-lib && \
    ./configure \
    --prefix=/tmp/polars_ta_extension/talib-sys/dependencies \
    --build="$(gcc -dumpmachine)" && \
    make && \
    make install && \
    cd /app && \
    uv sync --no-dev && \
    uv pip install --reinstall /tmp/polars_ta_extension && \
    rm -rf /tmp/polars_ta_extension

# --- Stage 2: Runtime (no Rust, no build tools) ---
FROM python:3.12-slim

WORKDIR /app

# Copy the fully built virtualenv and project metadata from the builder image.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/VERSION /app/VERSION

# Install uv for the runtime entrypoint.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy source after dependencies to keep rebuilds fast.
COPY src/ ./src/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8007

CMD ["uv", "run", "--no-sync", "python", "-m", "src.server"]
