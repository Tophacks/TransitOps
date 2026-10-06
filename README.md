# TransitOps

TransitOps is a transit-operations control simulator that compares fixed operations with intelligent dispatch and signal-priority decisions.

The current MVP intentionally uses a **small Waterloo-inspired corridor** rather than a city-scale digital twin. This keeps the simulation fast, deterministic, and easy to run locally while preserving the same architecture that can later scale to GTFS and larger SUMO networks.

## Current scenario

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

The SUMO corridor also contains three east/west cross streets, background road traffic, three simulated transit buses, and a TransitOps controller that can grant bus signal priority.

This is a Waterloo-inspired test scenario, **not a calibrated reproduction of GRT operations**.

## Architecture

```text
                 TransitOps

       Python operations controller
          dispatch / staging
          queue response
          signal priority
                 │
                 ↕ TraCI
                 │
               SUMO
       cars / buses / signals
                 │
          FastAPI WebSocket
                 │
                 ▼
       Next.js + MapLibre + deck.gl
```

The repository currently contains two complementary simulation layers:

- `backend/` — lightweight Python baseline vs intelligent-controller simulation used for passenger queues, transfer surges, KPIs, and decision comparison.
- `simulation/` — microscopic SUMO + TraCI corridor used for road traffic, buses, traffic signals, and live browser telemetry.

## Recommended development setup: local laptop

For now, the primary supported workflow is to run SUMO locally rather than deploying the simulation service to the cloud.

### Requirements

- Docker Desktop
- Node.js 20+ / npm

You do **not** need to install SUMO manually. Docker installs and runs it inside the simulation container.

### 1. Start SUMO + FastAPI

From the repository root:

```bash
docker compose up --build
```

The local service will expose:

```text
http://localhost:8001/
http://localhost:8001/health
ws://localhost:8001/ws/simulation
```

### 2. Start the frontend

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

Then open:

```text
http://localhost:3000
```

When the frontend is running on localhost it automatically uses:

```text
ws://localhost:8001/ws/simulation
```

No WebSocket environment variable is required for local development.

### 3. Run the live SUMO twin

In the TransitOps web interface, press **Run SUMO twin**.

The browser receives live vehicle and signal state from:

```text
SUMO → TraCI → Python/FastAPI → WebSocket → MapLibre/deck.gl
```

## What works now

- Compact deterministic SUMO corridor
- Background car traffic
- Three simulated transit buses
- Three signalized intersections
- TraCI vehicle telemetry
- TraCI traffic-light telemetry
- Transit-priority controller for approaching buses
- FastAPI WebSocket streaming
- MapLibre + deck.gl browser visualization
- Passenger queues and transfer surges in the Python operations model
- Baseline vs TransitOps KPI comparison
- Local Docker workflow
- GitHub Actions frontend build validation
- GitHub Actions SUMO smoke test

The SUMO integration smoke test verifies that the production image builds, road traffic appears, transit buses enter the simulation, and all three traffic lights are present.

## Repository layout

```text
TransitOps/
├── backend/                  # Python operational simulation + FastAPI API
├── frontend/                 # Next.js / MapLibre / deck.gl dashboard
├── simulation/
│   ├── Dockerfile
│   ├── server.py             # SUMO WebSocket service
│   ├── smoke_test.py
│   ├── traci_controller/
│   │   ├── runner.py
│   │   ├── control.py
│   │   └── telemetry.py
│   └── sumo/
│       ├── network/
│       │   └── build_network.sh
│       └── routes/
├── docker-compose.yml
├── render.yaml               # optional future cloud deployment
└── vercel.json               # web frontend / lightweight API deployment
```

## Visualization

The browser uses:

- **MapLibre GL JS** for the interactive geographic map
- **deck.gl** for stations, vehicles, queues, signals, and transit overlays
- **Next.js / TypeScript** for the application UI

The map provides Waterloo geographic context while the current SUMO road network is intentionally synthetic and compact.

## Cloud deployment

Cloud SUMO deployment is **optional and not required for development**.

The public frontend can remain on Vercel. If the SUMO service is later deployed to a WebSocket-capable container host, set:

```text
NEXT_PUBLIC_SUMO_WS_URL=wss://<simulation-host>/ws/simulation
```

A `render.yaml` blueprint remains in the repository for that future option.

## Roadmap

Next useful milestones:

1. Improve the small corridor control logic and KPIs.
2. Add explicit bus stops/dwell behavior to the SUMO buses.
3. Synchronize passenger demand from the Python model with SUMO.
4. Compare fixed-signal operation against TransitOps signal priority.
5. Add richer dispatch/pre-staging policies.
6. Import selected GTFS routes once the control logic is proven.
7. Expand to a larger OSM network only when the small scenario is stable.
8. Optionally add a Unity/WebGL visualization layer later.

## Design principle

TransitOps is primarily a **network-level transit operations system**, not a vehicle-autonomy simulator.

SUMO handles traffic and vehicle interactions. TransitOps focuses on decisions such as dispatching, staging, holding, rerouting, passenger-transfer response, and signal priority.
