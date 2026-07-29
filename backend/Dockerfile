FROM ghcr.zhizhenzhihe.com/astral-sh/uv:python3.11-bookworm-slim@sha256:4f5d923c9dcea037f57bda425dd209f3ec643da2f0b74227f68d09dab0b3bb36

WORKDIR /app/

ENV DEBIAN_FRONTEND=noninteractive
ENV DEBCONF_NONINTERACTIVE_SEEN=true

RUN set -eux; \
    . /etc/os-release; \
    rm -rf /etc/apt/sources.list /etc/apt/sources.list.d/*; \
    if [ "$ID" = "ubuntu" ]; then \
      C="main restricted universe multiverse"; \
      printf "%s\n" \
        "deb http://mirrors.ivolces.com/ubuntu/ $VERSION_CODENAME $C" \
        "deb http://mirrors.ivolces.com/ubuntu/ $VERSION_CODENAME-updates $C" \
        "deb http://mirrors.ivolces.com/ubuntu/ $VERSION_CODENAME-security $C" \
        > /etc/apt/sources.list; \
    else \
      C="main contrib non-free"; \
      printf "%s\n" \
        "deb http://mirrors.ivolces.com/debian/ $VERSION_CODENAME $C" \
        "deb http://mirrors.ivolces.com/debian/ $VERSION_CODENAME-updates $C" \
        "deb http://mirrors.ivolces.com/debian-security/ $VERSION_CODENAME-security $C" \
        > /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends gcc supervisor curl; \
    rm -rf /var/lib/apt/lists/*

ARG UV_DEFAULT_INDEX=https://nexus.zhizhenzhihe.com/repository/pypi-group/simple/

ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_HTTP_TIMEOUT=300

# Setup supervisord.
COPY supervisord.conf /usr/etc/supervisord.conf

# Install dependencies.
COPY pyproject.toml uv.lock /app/

RUN PYTHONDONTWRITEBYTECODE=1 uv sync --frozen --no-dev
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
