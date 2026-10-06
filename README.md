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

The 2D world is deliberately schematic and not to scale. It is an operations proof-of-concept, not a vehicle-dynamics or autonomous-driving perception model. SUMO/TraCI and 3D vehicle simulation remain later roadmap stages.
