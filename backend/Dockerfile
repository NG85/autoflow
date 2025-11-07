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
    apt-get install -y --no-install-recommends gcc supervisor && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

# Setup supervisord.
COPY supervisord.conf /usr/etc/supervisord.conf

# Install dependencies.
COPY pyproject.toml uv.lock /app/

RUN PYTHONDONTWRITEBYTECODE=1 uv sync --frozen
ENV PATH="/app/.venv/bin:$PATH"

# Pre-download nltk data with mirror support.
RUN python -c 'import nltk; \
import os; \
os.makedirs("/usr/local/lib/python3.11/site-packages/llama_index/core/_static/nltk_cache", exist_ok=True); \
download_dir = "/usr/local/lib/python3.11/site-packages/llama_index/core/_static/nltk_cache"; \
# Use Tsinghua NLTK mirror source directly.
nltk.downloader._url = "https://mirrors.tuna.tsinghua.edu.cn/nltk_data/packages/"; \
nltk.download("stopwords", download_dir=download_dir, quiet=True); \
nltk.download("punkt", download_dir=download_dir, quiet=True);'

ENV PYTHONPATH=/app

COPY . /app/

# Default number of workers
ENV WEB_CONCURRENCY=1

CMD ["sh", "-c", "fastapi run app/api_server.py --host 0.0.0.0 --port 80 --workers ${WEB_CONCURRENCY}"]
