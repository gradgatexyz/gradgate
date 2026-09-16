FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY gradgate gradgate
RUN pip install .
COPY engine engine
COPY ui/dist ui/dist
EXPOSE 8765
WORKDIR /app/engine
# inside the container the engine listens on all interfaces; compose publishes it on 127.0.0.1 only
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765", "--timeout-graceful-shutdown", "2"]
