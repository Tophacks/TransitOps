# TransitOps Python Simulator

A lightweight discrete-event transit operations simulator for testing intelligent dispatch and connection-management strategies.

## Current features
- Train arrivals with configurable delays
- Passenger surges from train arrivals
- Bus fleet states: YARD, STAGED, EN_ROUTE, AT_STATION
- Traffic-dependent bus travel times
- Pre-staging logic
- Train-connection holding logic
- Passenger boarding and queue tracking
- Event log and KPI summary
- Baseline vs intelligent-controller comparison

## Run

```bash
python main.py
```

## Structure

- `models.py` — domain objects
- `controller.py` — transit control logic
- `simulation_engine.py` — minute-by-minute simulation
- `main.py` — scenario runner and comparison

## Roadmap

Next steps include:
- Route and stop-level bus movement
- Passenger origins and destinations
- Signal-priority requests
- Schedule adherence and bus bunching
- Interactive web visualization
- Demand forecasting with scikit-learn
- GTFS / GTFS-Realtime integration
- SUMO + TraCI integration

This version intentionally starts with deterministic control logic before adding machine learning, so the operational behavior can be validated independently of the prediction model.

## Live web app (Vercel)

The repository is structured as a Vercel multi-service project:

```text
TransitOps/
├── vercel.json
├── frontend/        # Next.js operations dashboard
│   └── app/
└── backend/         # FastAPI + Python simulation engine
    ├── main.py
    ├── controller.py
    ├── models.py
    └── simulation_engine.py
```

### Deploy

1. Import `Tophacks/TransitOps` into Vercel.
2. Keep the project root at the repository root.
3. Vercel reads `vercel.json` and deploys the Next.js frontend and FastAPI backend together.
4. The simulator API is exposed at `/api/simulate`.
5. Pushes to `main` can automatically create new deployments once the GitHub repository is connected.

### Local connected development

Install the current Vercel CLI and run from the repository root:

```bash
npm install -g vercel@latest
vercel dev -L
```

Then open the local URL printed by Vercel.

## Live 2D operations simulator

The Vercel demo now includes a game-like schematic operations view backed by the Python simulation engine.

- Passenger agents with origin, destination, arrival, boarding, and completion state
- Passenger queues visualized as pixel-style crowd sprites
- Moving buses with occupancy, target stop, progress, and schedule deviation
- Four-stop demo corridor plus a staging yard
- Signalized intersections with live red/green state
- Transit signal-priority decisions for delayed occupied buses
- Background passenger demand plus rail-transfer demand spikes
- Play, pause, timeline scrubbing, and 1x / 2x / 5x / 10x playback
- Clickable buses, stations, intersections, and yard
- Baseline-versus-TransitOps operational metrics

The 2D world is deliberately schematic and not to scale. It is an operations proof-of-concept, not a vehicle-dynamics or autonomous-driving perception model.

## Target digital-twin architecture

TransitOps is intended to evolve into a control layer that can operate on top of a microscopic traffic simulator rather than attempting to reproduce autonomous-driving physics itself.

```text
GTFS / demand / operational inputs
              ↓
      TransitOps controller
              ↓
  dispatch · staging · holding
  route assignment · signal priority
              ↓
          TraCI API
              ↓
            SUMO
  lanes · traffic lights · vehicles
  intersections · road congestion
              ↓
      browser visualization
              ↓
   optional future 3D / WebGL layer
```

The important architectural boundary is that SUMO is responsible for the traffic environment and vehicle interactions, while TransitOps remains responsible for network-level transit decisions.

### Development stages

**Prototype — current**
- Python discrete-event simulation
- Web-based 2D operational digital twin
- Passenger queues, buses, stops, intersections, and control events
- Baseline-versus-controller comparison

**Microscopic traffic integration — next major milestone**
- Import a real or synthetic street network into SUMO
- Use TraCI to read vehicle positions, queue lengths, travel times, and signal state
- Replace simplified segment travel times with SUMO telemetry
- Allow TransitOps to request bus dispatch, holding, rerouting, and signal priority

**Data integration**
- GTFS static schedules
- GTFS-Realtime service updates
- Historical passenger demand and transfer counts
- Weather/event features for demand prediction

**Optional high-fidelity visualization**
- A richer WebGL or 3D digital-twin frontend may be added later
- 3D rendering is intentionally not a prerequisite for validating the transit-control algorithms

### Design principle

The project is not primarily a traffic-visualization exercise. The core research and engineering question is whether network-wide operational decisions can improve service outcomes under changing demand, congestion, delays, and transfer conditions.

The visualization exists to make those decisions observable and explainable.


## Waterloo–Kitchener regional sandbox

The browser demo now uses the Waterloo–Kitchener ION corridor as a larger geographic frame for the simulator. The current prototype includes major stations from Conestoga through the University of Waterloo, Uptown Waterloo, Grand River Hospital, Central Station, downtown Kitchener, Kitchener Market, and Fairway.

This is intentionally **not** presented as a reproduction of live GRT operations. Real station names and corridor geography provide recognizable context, while vehicle dispatch, passenger demand, staging decisions, and signal-priority behaviour remain experimental TransitOps scenarios.

The next data-integration step is to replace hand-authored network geometry and demand assumptions with GRT open data / GTFS and, later, SUMO/TraCI.


## MapLibre + deck.gl visualization

The web visualization layer now uses a real Waterloo-Kitchener street map instead of a hand-drawn SVG city.

- MapLibre GL JS renders the interactive base map.
- OpenFreeMap provides OpenStreetMap-derived vector tiles with no API key.
- deck.gl renders the simulation overlays: transit spine, passenger-demand halos, stations, smart intersections, vehicles, and labels.
- Python/FastAPI remains the simulation and control backend.
- Simulation snapshots now include geographic longitude/latitude so the browser can render system state in real map space.

The current transit path is still a simplified station-to-station operational corridor. A later GTFS/SUMO integration should replace that simplified geometry with route shapes and microscopic traffic movement.
