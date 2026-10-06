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
