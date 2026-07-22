FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --retries 10 \
        --timeout 120 \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.4.1+cpu torchvision==0.19.1+cpu

COPY requirements.txt ./requirements.txt
RUN python -m pip install --retries 10 --timeout 120 -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /app/logs /app/models /app/datasets /app/runs /app/storage

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
