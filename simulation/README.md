# TransitOps SUMO Service

This directory contains the microscopic traffic layer for the TransitOps digital twin.

## Architecture

```text
OpenStreetMap
      ↓
netconvert
      ↓
SUMO
      ↕ TraCI
Python TransitOps controller
      ↓
FastAPI WebSocket
      ↓
MapLibre/deck.gl browser client
      ↓
optional Unity WebGL renderer later
```

The browser does not need SUMO installed.

## Large Waterloo–Kitchener network

The full road network is **not committed to Git**. During the production Docker build, TransitOps:

1. downloads the Waterloo–Kitchener OpenStreetMap extract,
2. converts it with SUMO `netconvert`,
3. generates background traffic with `randomTrips.py`,
4. stores the runtime assets as compressed XML:

```text
simulation/sumo/network/waterloo_kitchener.net.xml.gz
simulation/sumo/routes/background.rou.xml.gz
```

SUMO reads the compressed files directly. This keeps Git history small while ensuring every deployed image already contains the network before it starts.

The network builder is:

```text
simulation/sumo/network/build_network.sh
```

Generated `.osm.xml`, `.net.xml(.gz)`, and `.rou.xml(.gz)` files remain gitignored.

## Local Docker run

From the repository root:

```bash
docker compose build
docker compose up sumo
```

The Docker build generates the Waterloo–Kitchener SUMO network. When the service starts:

```text
http://localhost:8001/
http://localhost:8001/health
ws://localhost:8001/ws/simulation
```

## Automated validation

`.github/workflows/sumo-integration.yml` builds the production Docker image and runs `simulation/smoke_test.py`.

The smoke test starts SUMO with the generated Waterloo–Kitchener assets, advances 180 seconds of simulated traffic, and fails if no vehicles enter the network. The workflow also uploads the compressed SUMO assets as a short-lived GitHub Actions artifact for inspection.

## Web deployment

The public Next.js frontend remains on Vercel. The SUMO service must run on a container host that supports a long-running process and WebSockets.

A Render Blueprint is included at:

```text
render.yaml
```

After the SUMO service has a public HTTPS address, set this on the Vercel frontend:

```text
NEXT_PUBLIC_SUMO_WS_URL=wss://YOUR-SUMO-HOST/ws/simulation
```

The dashboard then exposes **Run SUMO twin** and overlays live SUMO traffic on the Waterloo–Kitchener map.

## Current simulation scope

Implemented:
- real OSM street-network import
- SUMO microscopic road traffic
- geographic vehicle telemetry through TraCI
- traffic-light telemetry
- controller hooks for vehicle holding, rerouting, and signal phases
- FastAPI WebSocket streaming
- live browser rendering of SUMO vehicles
- production Docker packaging
- automated network/simulation smoke testing

Still intentionally future work:
- GRT GTFS schedule/shape import
- calibrated GRT buses and ION service
- passenger assignment into SUMO
- production TransitOps control policies against TraCI
- Unity/WebGL high-fidelity 3D rendering

The current Waterloo–Kitchener model is therefore a real microscopic road-traffic sandbox, not yet a calibrated reproduction of GRT service.
