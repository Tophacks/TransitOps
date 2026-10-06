FROM ghcr.io/eclipse-sumo/sumo:latest

ENV PYTHONUNBUFFERED=1
ENV SUMO_HOME=/usr/share/sumo
ENV SUMO_BINARY=sumo
ENV PYTHONPATH=/usr/share/sumo/tools

WORKDIR /app

COPY simulation/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY simulation /app/simulation
WORKDIR /app/simulation

# Build the small deterministic Waterloo Corridor network.
RUN chmod +x sumo/network/build_network.sh \
    && ./sumo/network/build_network.sh

EXPOSE 8001

CMD ["sh", "-c", "exec python3 -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]
