FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
RUN mkdir -p /srv/data && useradd -r -u 10001 appuser && chown -R appuser /srv/data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"
# If you put a reverse proxy in front, add: --proxy-headers --forwarded-allow-ips <proxy ip>
# Do NOT use "*": that lets any client spoof its IP and bypass the per-client rate limit.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
