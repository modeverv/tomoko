FROM groonga/pgroonga:4.0.6-debian-17

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*
