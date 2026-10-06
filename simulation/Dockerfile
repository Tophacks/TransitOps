FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY simulation/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY simulation /app/simulation

WORKDIR /app/simulation

# Build the small deterministic Waterloo Corridor SUMO network into the image.
RUN export SUMO_HOME=$(python -c 'import sumo; print(sumo.SUMO_HOME)') \
    && chmod +x sumo/network/build_network.sh \
    && ./sumo/network/build_network.sh

EXPOSE 8001

CMD ["sh", "-c", "export SUMO_HOME=$(python -c 'import sumo; print(sumo.SUMO_HOME)') && exec python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]
