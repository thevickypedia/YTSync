FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
        python-is-python3 \
        unzip \
        git \
        ffmpeg \
        rsync \
        openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Install Deno
RUN curl -fsSL https://deno.land/install.sh | sh

ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

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
ADD LICENSE /app
ADD README.md /app
ADD requirements.txt /app
ADD log_config.yml /app
ADD entrypoint.py /app
ADD ytsync /app/ytsync

RUN ls -ltrh /app

# Create venv
RUN python -m venv /app/venv

# Install uv into the venv
RUN /app/venv/bin/python -m pip install --upgrade pip uv

# Install Python dependencies
RUN /app/venv/bin/uv pip install \
    --python /app/venv/bin/python \
    -r /app/requirements.txt

# Make venv the default Python
ENV PATH="/app/venv/bin:${PATH}"

RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

ENTRYPOINT ["python", "entrypoint.py"]
