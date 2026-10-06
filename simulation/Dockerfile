FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV SUMO_NETWORK_DIR=/data/network
ENV SUMO_ROUTE_DIR=/data/routes

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY simulation/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY simulation /app/simulation

WORKDIR /app/simulation

RUN mkdir -p /data/network /data/routes
VOLUME ["/data"]

EXPOSE 8001

CMD ["sh", "-c", "export SUMO_HOME=$(python -c 'import sumo; print(sumo.SUMO_HOME)') && exec python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]
