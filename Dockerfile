FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TIMEBOARDAPP_SETTINGS=/data/settings.yml
WORKDIR /app
COPY requirements.txt requirements.in /app/
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt \
    && groupadd --gid 1000 timeboard \
    && useradd --uid 1000 --gid timeboard --no-create-home timeboard \
    && mkdir /data && chown timeboard:timeboard /data && chmod 700 /data
COPY app /app/app
COPY settings.sample.yml README.md /app/
USER 1000:1000
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/healthz', timeout=3)"]
CMD ["python", "-m", "app.run"]
