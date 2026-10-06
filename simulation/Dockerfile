FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV SUMO_HOME=/usr/share/sumo
ENV SUMO_BINARY=sumo
ENV PYTHONPATH=/usr/share/sumo/tools

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       python3 \
       python3-pip \
       sumo \
       sumo-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY simulation/requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY simulation /app/simulation
WORKDIR /app/simulation

RUN chmod +x sumo/network/build_network.sh \
    && ./sumo/network/build_network.sh

EXPOSE 8001

CMD ["sh", "-c", "exec python3 -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]
