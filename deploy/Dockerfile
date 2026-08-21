# Custos control plane.
#
# Two stages so the runtime image carries no build tooling. The result is small
# enough to audit and boring enough to deploy anywhere — which matters because
# the customers who buy this run their own infrastructure and will ask what is
# in the image.

FROM python:3.11-slim AS build

WORKDIR /build
COPY controlplane/ ./controlplane/

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir ./controlplane[api]


FROM python:3.11-slim

# Runs as a non-root user. The control plane needs to write one SQLite file and
# nothing else, so root buys nothing and costs a finding in every container
# scan a customer runs.
RUN useradd --create-home --uid 10001 custos \
 && mkdir -p /data && chown custos:custos /data

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CUSTOS_DB=/data/custos.db

USER custos
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status == 200 else 1)"

# One worker by default. SQLite with WAL handles concurrent reads during a
# write, but multiple writer processes would contend on the same file for no
# benefit at pilot volume. Scaling past that is the move to Postgres, not more
# workers.
CMD ["uvicorn", "custos.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
