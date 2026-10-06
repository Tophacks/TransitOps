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


## Large network asset strategy

The Waterloo–Kitchener road network is intentionally **not committed as raw `.net.xml`**.

TransitOps generates and stores:

```text
/data/network/waterloo_kitchener.net.xml.gz
/data/routes/background.rou.xml.gz
```

SUMO can read gzipped XML network and route files directly, so the compressed files are the runtime assets. The first simulation request builds them from OpenStreetMap if the persistent cache is empty; later runs reuse the cached copies.

This keeps Git history small and also lets us rebuild the digital twin when OpenStreetMap changes.

## Frontend live mode

Set this variable on the Vercel frontend after deploying the SUMO service:

```text
NEXT_PUBLIC_SUMO_WS_URL=wss://YOUR-SUMO-HOST/ws/simulation
```

The TransitOps dashboard will then expose **Run SUMO twin**. The existing Python discrete-event simulator remains available as a fallback and for baseline/controller experiments.

## Recommended production layout

A long-running container host with WebSocket support and a persistent volume is required for the SUMO service.

Mount the persistent volume at:

```text
/data
```

The container listens on `$PORT` when supplied by the host, or port `8001` locally.

On a fresh persistent volume:
1. Browser connects to `/ws/simulation`.
2. Service reports `building_network`.
3. `osmGet.py` downloads the Waterloo–Kitchener OSM tiles.
4. `netconvert` creates `waterloo_kitchener.net.xml.gz`.
5. `randomTrips.py` creates `background.rou.xml.gz`.
6. SUMO starts.
7. TraCI streams vehicle coordinates and traffic-light state to the browser.

On later starts, steps 3–5 are skipped because the compressed assets already exist.
