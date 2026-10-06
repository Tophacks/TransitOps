# TransitOps SUMO Service

The SUMO service powers the microscopic traffic portion of the **Waterloo Corridor Test Scenario**.

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

Three east/west cross streets create competing road traffic. The route file generates background cars and three transit buses.

The corridor is Waterloo-inspired and is not a calibrated GRT model.

## How it works

```text
Local node/edge definitions
          ↓
      netconvert
          ↓
         SUMO
          ↕
        TraCI
          ↓
TransitOps Python controller
          ↓
 FastAPI WebSocket :8001
          ↓
 browser visualization
```

There is no OpenStreetMap download during startup or Docker build. The compact SUMO network is generated locally and deterministically.

## Transit-priority behavior

The controller inspects each simulated bus using TraCI. When a bus approaches a red traffic light within the configured distance, TransitOps finds a compatible green phase and requests a short priority window.

The WebSocket snapshot includes:

- vehicle ID and type
- speed
- road and lane IDs
- browser-mapped longitude/latitude
- traffic-light phases and states
- arrivals and departures
- TransitOps priority actions

## Run locally

From the repository root:

```bash
docker compose up --build
```

Available endpoints:

```text
http://localhost:8001/
http://localhost:8001/health
http://localhost:8001/scenario
ws://localhost:8001/ws/simulation
```

The frontend automatically connects to the local WebSocket when served from `localhost`.

## Validation

GitHub Actions runs `.github/workflows/sumo-integration.yml`.

The workflow:

1. builds the simulation Docker image,
2. generates the corridor network,
3. starts SUMO,
4. advances 180 simulated seconds,
5. confirms road traffic is present,
6. confirms transit buses are present,
7. confirms all three signalized intersections are present.

The validated corridor assets are only a few kilobytes, so the old giant Waterloo–Kitchener `.net.xml` approach is no longer needed for the MVP.

## Cloud deployment

Cloud hosting is optional. Local execution is the recommended workflow while the simulator is being developed.

If a public SUMO server is later deployed, the browser can connect through:

```text
NEXT_PUBLIC_SUMO_WS_URL=wss://<simulation-host>/ws/simulation
```

The included `render.yaml` is retained only as an optional deployment path.

## Next simulator work

- add SUMO bus stops and dwell behavior
- add passenger demand to the microscopic model
- measure travel-time effects of signal priority
- compare fixed-control and TransitOps scenarios
- add dispatch/staging commands through TraCI
- later import selected GTFS/OSM data after the controller is stable
