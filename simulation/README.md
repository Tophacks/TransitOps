# TransitOps SUMO Service

This directory implements the first cloud-simulation layer for the TransitOps digital twin.

## Architecture

```text
OpenStreetMap
     ↓
SUMO road network
     ↓
SUMO traffic simulation
     ↕ TraCI
Python TransitOps controller
     ↓
FastAPI WebSocket
     ↓
Browser / MapLibre now
Unity WebGL later
```

The web client does not need SUMO installed. SUMO runs in this container and streams simulation state to the browser.

## 1. Build the Waterloo–Kitchener network

The network builder uses SUMO's official `osmGet.py` and `osmBuild.py` tooling.

From a machine/container with SUMO installed:

```bash
cd simulation/sumo/network
chmod +x build_network.sh
./build_network.sh
```

This downloads an OpenStreetMap extract for the Waterloo–Kitchener area, creates `waterloo_kitchener.net.xml`, and generates background vehicle routes in `simulation/sumo/routes/background.rou.xml`.

Generated SUMO XML files are intentionally not committed yet. They can be regenerated from OSM and later replaced by a curated network.

## 2. Run locally with Docker

From the repository root:

```bash
docker compose build
docker compose run --rm sumo bash -lc "cd sumo/network && chmod +x build_network.sh && ./build_network.sh"
docker compose up sumo
```

Health check:

```text
http://localhost:8001/health
```

Simulation WebSocket:

```text
ws://localhost:8001/ws/simulation
```

The WebSocket starts a SUMO session and streams JSON snapshots containing vehicles, geographic positions, lane/road IDs, traffic-light state, arrivals/departures, and TransitOps control metadata.

## 3. Cloud deployment

Deploy `simulation/Dockerfile` to a container host that supports long-running processes and WebSockets. The Vercel frontend remains the public website.

```text
Vercel / Next.js
      ↓ WebSocket
Container host
FastAPI + Python + TraCI
      ↓
SUMO
```

A future frontend environment variable can point the browser at the deployed simulation WebSocket.

## Current scope

This is the infrastructure milestone, not a calibrated GRT model.

Current:
- OSM → SUMO network build pipeline
- generated background road traffic
- SUMO/TraCI Python runner
- geographic vehicle telemetry
- traffic-light telemetry
- controller hooks for holding, rerouting, and signal phases
- FastAPI WebSocket stream
- Docker packaging

Next:
- import GRT GTFS route shapes and stops
- create bus and ION vehicle types/routes
- connect passenger-demand model
- implement real TransitOps control policies against TraCI
- stream SUMO state into the existing MapLibre view
- add Unity/WebGL visualization once the simulation loop is validated
