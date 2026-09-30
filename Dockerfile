# Optional aggregate server. Base digest verified against the official Python image.
FROM python:3.14.7-slim-bookworm@sha256:9ab8d9c8514b44f90cf0029dd42fdd7e9e211e639c8b995304cc04568dee900f

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
# The server package plus the shared stdlib-only core it imports
# (practicegraph.analysis / practicegraph.wire / practicegraph.transport).
COPY src/practicegraph ./src/practicegraph
COPY src/practicegraph_server ./src/practicegraph_server
RUN pip install --no-cache-dir --no-compile . \
    && rm -rf /root/.cache

# Non-root runtime user; /data owned by it so the SQLite DB is writable.
RUN groupadd --gid 10001 practicegraph \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin practicegraph \
    && mkdir -p /data \
    && chown practicegraph:practicegraph /data

# Container networking needs a non-loopback bind; compose maps it to loopback
# on the host for pilots (FR-API-6). NEVER publish it non-loopback without a
# TLS-terminating reverse proxy in front (see deploy/nginx, deploy/Caddyfile).
ENV PG_SERVER_BIND=0.0.0.0 \
    PG_SERVER_PORT=8321 \
    PG_SERVER_DB=/data/practicegraph-server.db

USER practicegraph
VOLUME ["/data"]
EXPOSE 8321

CMD ["practicegraph-server", "serve"]
