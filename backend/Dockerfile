FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app/

ENV DEBIAN_FRONTEND=noninteractive
ENV DEBCONF_NONINTERACTIVE_SEEN=true

RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian-security/ trixie-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gcc supervisor curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

# Setup supervisord.
COPY supervisord.conf /usr/etc/supervisord.conf

# Install dependencies.
COPY pyproject.toml uv.lock /app/

RUN PYTHONDONTWRITEBYTECODE=1 uv sync --frozen
ENV PATH="/app/.venv/bin:$PATH"

# Copy pre-downloaded nltk data tar.gz and extract it.
RUN mkdir -p /usr/local/lib/python3.11/site-packages/llama_index/core/_static/nltk_cache
COPY nltk_data.tar.gz /tmp/nltk_data.tar.gz
RUN cd /usr/local/lib/python3.11/site-packages/llama_index/core/_static/nltk_cache && \
    mkdir -p corpora tokenizers && \
    tar -xzf /tmp/nltk_data.tar.gz && \
    mv stopwords corpora/ && \
    mv punkt tokenizers/ && \
    rm -f /tmp/nltk_data.tar.gz

ENV PYTHONPATH=/app

COPY . /app/

# Default number of workers
ENV WEB_CONCURRENCY=1

CMD ["sh", "-c", "fastapi run app/api_server.py --host 0.0.0.0 --port 80 --workers ${WEB_CONCURRENCY}"]
