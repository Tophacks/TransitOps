import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_NETWORK_DIR = ROOT / "sumo" / "network"
DEFAULT_ROUTE_DIR = ROOT / "sumo" / "routes"


def network_dir():
    return Path(os.getenv("SUMO_NETWORK_DIR", DEFAULT_NETWORK_DIR))


def route_dir():
    return Path(os.getenv("SUMO_ROUTE_DIR", DEFAULT_ROUTE_DIR))


def network_file():
    return network_dir() / "waterloo_kitchener.net.xml.gz"


def route_file():
    return route_dir() / "background.rou.xml.gz"


def network_ready():
    return network_file().exists() and route_file().exists()


def ensure_network():
    if network_ready():
        return {
            "network": str(network_file()),
            "routes": str(route_file()),
            "generated": False,
        }

    network_dir().mkdir(parents=True, exist_ok=True)
    route_dir().mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["OUT_NETWORK_DIR"] = str(network_dir())
    env["OUT_ROUTE_DIR"] = str(route_dir())

    script = ROOT / "sumo" / "network" / "build_network.sh"
    subprocess.run(["bash", str(script)], check=True, env=env)

    if not network_ready():
        raise FileNotFoundError(
            "SUMO network build completed without producing the expected compressed assets."
        )

    return {
        "network": str(network_file()),
        "routes": str(route_file()),
        "generated": True,
    }
