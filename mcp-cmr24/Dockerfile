FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system cmr24 && useradd --system --gid cmr24 --home-dir /app cmr24
WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN python -m pip install --require-hashes -r requirements.lock

COPY --chown=cmr24:cmr24 . .
USER cmr24
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2).read()"]

CMD ["python", "start_server.py"]
