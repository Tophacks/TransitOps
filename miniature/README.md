# TransitOps-M — Miniature Fleet Simulator v0.1

Hardware-independent Python module for the TransitOps monorepo. Simulates one 1:87 HO bus, two stations, one reservable automated depot bay, two static parking spaces, events, simulated battery usage and a simulation emergency-stop interlock.

## Run (Python 3.10+, zero third-party dependencies)
```bash
cd miniature
python -m unittest discover -s tests -v
python -m transitops_m.api
```
Open http://127.0.0.1:8765/

API: GET /health, GET /api/state, POST /api/command.
Commands: tick (steps 1..100), return, dock, dispatch, stop, clear, reset, add.

**Safety:** SIMULATION ONLY. Docking is manually confirmed in software. No motor hardware, real emergency-stop circuitry, battery charger, or GPIO interface is implemented. This API must not be exposed to a public network or connected directly to actuators.
