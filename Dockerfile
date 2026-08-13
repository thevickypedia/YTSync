FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    DENO_INSTALL=/root/.deno \
    PATH="/app/venv/bin:/root/.deno/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        openssh-client \
        rsync \
        unzip && \
    rm -rf /var/lib/apt/lists/*

# Install Deno
RUN curl -fsSL https://deno.land/install.sh | sh

# Verify system dependencies
RUN python --version && \
    pip3 --version && \
    curl --version && \
    deno --version && \
    ffmpeg -version && \
    ffprobe -version && \
    rsync --version && \
    ssh -V

# Application files
COPY LICENSE README.md requirements.txt entrypoint.py /app/
COPY ytsync /app/ytsync

RUN ls -ltrh /app

# Python virtual environment + dependencies
RUN python -m venv /app/venv && \
    /app/venv/bin/python -m pip install --upgrade pip uv && \
    /app/venv/bin/uv pip install \
        --python /app/venv/bin/python \
        -r /app/requirements.txt && \
    rm -rf /root/.cache/pip /root/.cache/uv

# SSH configuration directory
RUN mkdir -p /root/.ssh && \
    chmod 700 /root/.ssh

ENTRYPOINT ["python", "entrypoint.py"]
