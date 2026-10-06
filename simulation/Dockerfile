FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV SUMO_HOME=/usr/share/sumo\nENV SUMO_NETWORK_DIR=/data/network\nENV SUMO_ROUTE_DIR=/data/routes

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip sumo sumo-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY simulation/requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY simulation /app/simulation

WORKDIR /app/simulation\n\nRUN mkdir -p /data/network /data/routes\nVOLUME ["/data"]\n\nEXPOSE 8001

CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
