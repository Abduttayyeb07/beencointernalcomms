FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=4173 \
    HOST=0.0.0.0 \
    PORTAL_DATA_DIR=/data \
    ENABLE_BEDROCK_API=True \
    AWS_BEDROCK_REGION=eu-central-1 \
    AWS_BEDROCK_MODEL=qwen.qwen3-32b-v1:0 \
    LOCAL_FALLBACK_MODEL=qwen.qwen3-32b-v1:0

WORKDIR /app

# Install system dependencies (certificates for secure HTTPS to AWS Bedrock & timezone data)
RUN apk add --no-cache ca-certificates tzdata

# Install python dependencies first for optimal Docker layer caching
COPY apps/api/requirements.txt ./apps/api/
RUN pip install --no-cache-dir -r apps/api/requirements.txt

# Copy application source code
COPY apps/api/portal_server.py apps/api/README.md ./apps/api/
COPY apps/web ./apps/web
COPY packages/types/openapi.yaml ./packages/types/openapi.yaml
COPY run_briefing.py artifact_updater.py bedrock_client.py config.py data_fetcher.py blockchain-briefing-live.html ./

# Create non-root application user and setup permissions
RUN addgroup -S beenco && adduser -S -G beenco beenco \
    && mkdir -p /data \
    && chown -R beenco:beenco /app /data

USER beenco
EXPOSE 4173
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4173/api/health', timeout=3)"

CMD ["python", "apps/api/portal_server.py"]

