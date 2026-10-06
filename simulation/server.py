from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from traci_controller.runner import SumoSession


app = FastAPI(
    title="TransitOps SUMO Service",
    version="0.1.0",
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
    }


@app.get("/scenario")
def scenario():
    return {
        "id": "waterloo-kitchener",
        "name": "Waterloo–Kitchener regional traffic sandbox",
        "network_source": "OpenStreetMap imported through SUMO tooling",
        "status": "ready-if-network-generated",
    }


@app.websocket("/ws/simulation")
async def simulation_socket(websocket: WebSocket):
    await websocket.accept()
    session = SumoSession()

    try:
        await websocket.send_json(
            {
                "type": "status",
                "status": "starting",
                "message": "Starting Waterloo–Kitchener SUMO simulation",
            }
        )

        async for snapshot in session.stream():
            await websocket.send_json(
                {
                    "type": "simulation",
                    "data": snapshot,
                }
            )

        await websocket.send_json(
            {
                "type": "status",
                "status": "complete",
                "message": "SUMO simulation complete",
            }
        )

    except FileNotFoundError as exc:
        await websocket.send_json(
            {
                "type": "error",
                "message": str(exc),
            }
        )
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
