import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from network_manager import ensure_network, network_file, network_ready, route_file
from traci_controller.runner import SumoSession


app = FastAPI(
    title="TransitOps SUMO Service",
    version="0.2.0",
    description="Cloud-hosted SUMO + TraCI simulation service for TransitOps.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "TransitOps SUMO",
        "engine": "SUMO + TraCI",
        "network_ready": network_ready(),
    }


@app.get("/scenario")
def scenario():
    return {
        "id": "waterloo-kitchener",
        "name": "Waterloo–Kitchener regional traffic sandbox",
        "network_source": "OpenStreetMap imported through SUMO netconvert",
        "network_ready": network_ready(),
        "network_asset": str(network_file()),
        "route_asset": str(route_file()),
        "storage": "gzip-compressed generated assets",
    }


@app.post("/network/ensure")
async def build_network():
    result = await asyncio.to_thread(ensure_network)
    return {"status": "ready", **result}


@app.websocket("/ws/simulation")
async def simulation_socket(websocket: WebSocket):
    await websocket.accept()
    session = SumoSession()

    try:
        if not network_ready():
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "building_network",
                    "message": "Building compressed Waterloo–Kitchener SUMO network from OpenStreetMap. First run may take several minutes.",
                }
            )
            await asyncio.to_thread(ensure_network)

        await websocket.send_json(
            {
                "type": "status",
                "status": "starting",
                "message": "Starting SUMO + TraCI simulation",
            }
        )

        async for snapshot in session.stream():
            await websocket.send_json({"type": "simulation", "data": snapshot})

        await websocket.send_json(
            {
                "type": "status",
                "status": "complete",
                "message": "SUMO simulation complete",
            }
        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(exc),
                }
            )
        except Exception:
            pass
    finally:
        session.close()
