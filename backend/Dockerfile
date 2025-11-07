# ================================
# Stage 1: Builder
# ================================
FROM docker.1ms.run/python:3.11-slim AS builder

WORKDIR /app/

RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian-security/ trixie-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y gcc supervisor && \
    rm -rf /var/lib/apt/lists/*

# 安装 uv（使用国内镜像源）
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple uv

ARG UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV UV_INDEX_URL=${UV_INDEX_URL}

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

# ================================
# Stage 2: Runtime
# ================================
FROM docker.1ms.run/python:3.11-slim AS runtime

WORKDIR /app/

RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian-security/ trixie-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ivolces.com/debian/ trixie-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y supervisor && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /usr/local/lib/python3.11/site-packages/llama_index/core/_static/nltk_cache /usr/local/lib/python3.11/site-packages/llama_index/core/_static/nltk_cache
COPY . /app/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV WEB_CONCURRENCY=1

COPY supervisord.conf /usr/etc/supervisord.conf

CMD ["sh", "-c", "fastapi run app/api_server.py --host 0.0.0.0 --port 80 --workers ${WEB_CONCURRENCY}"]
