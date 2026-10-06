# TransitOps SUMO Service

TransitOps now uses a deliberately small **Waterloo Corridor Test Scenario** for the first working SUMO/TraCI demo.

## Scenario

```text
University of Waterloo
        │
     Signal 1
        │
Waterloo Public Square
        │
     Signal 2
        │
Grand River Hospital
        │
     Signal 3
        │
   Central Station
```

The SUMO network also includes three east/west cross streets, background car flows, and three simulated transit buses.

The corridor is Waterloo-inspired and is **not** a calibrated reproduction of GRT operations.

## Architecture

```text
Small deterministic corridor
        ↓
       SUMO
        ↕ TraCI
Python TransitOps controller
        ↓
 FastAPI WebSocket
        ↓
MapLibre + deck.gl
```

The Python controller watches approaching buses. If a bus is within 70 m of a red signal, TransitOps selects a compatible green phase and grants a short transit-priority window.

## Why the smaller network

The earlier city-scale OSM build required large Overpass downloads and made cloud builds slow and fragile. The current corridor is generated locally from a few node/edge definitions, so:

- no external map download is required,
- Docker builds are deterministic,
- CI can smoke-test the simulator quickly,
- control decisions are easier to inspect,
- the architecture still scales to GTFS/OSM later.

## Local Docker run

From the repository root:

```bash
docker compose build
docker compose up sumo
```

Then use:

```text
http://localhost:8001/
http://localhost:8001/health
ws://localhost:8001/ws/simulation
```

## Web deployment

The Next.js frontend remains on Vercel. The SUMO service is packaged as a long-running Docker web service.

A Render blueprint is included in `render.yaml`.

After deployment, configure the Vercel frontend:

```text
NEXT_PUBLIC_SUMO_WS_URL=wss://YOUR-SUMO-HOST/ws/simulation
```

The dashboard's **Run SUMO twin** button will then stream the microscopic traffic simulation into the browser.

## Automated validation

`.github/workflows/sumo-integration.yml`:

1. builds the production Docker image,
2. builds the compact SUMO corridor,
3. starts SUMO,
4. advances 180 simulated seconds,
5. verifies road traffic appears,
6. verifies transit buses appear,
7. verifies all three traffic lights exist.

## Next iterations

Once this demo is stable, larger geography can be added without changing the overall architecture. Useful next steps are GTFS transit routes, passenger assignment, richer dispatch policies, and eventually a Unity/WebGL renderer.
