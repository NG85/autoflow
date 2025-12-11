# Use the base image built by Dockerfile_base
FROM registry.cn-beijing.aliyuncs.com/aptsell/aptsell.autoflow-base

WORKDIR /app/

# Setup supervisord.
COPY supervisord.conf /usr/etc/supervisord.conf

ENV PYTHONPATH=/app

COPY . /app/

# Default number of workers
ENV WEB_CONCURRENCY=1

CMD ["sh", "-c", "fastapi run app/api_server.py --host 0.0.0.0 --port 80 --workers ${WEB_CONCURRENCY}"]
