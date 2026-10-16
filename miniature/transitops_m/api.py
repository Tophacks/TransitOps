"""Localhost-only development API for simulated vehicles. Never directly control hardware."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from json import dumps, loads
from urllib.parse import urlparse
from .core import Dispatcher

engine = Dispatcher()

class Handler(BaseHTTPRequestHandler):
    def _reply(self, status, obj):
        data = dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/dashboard"):
            page = Path(__file__).with_name("dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif path == "/api/state": self._reply(200, engine.snapshot())
        elif path == "/health": self._reply(200, {"ok": True, "simulated": True})
        else: self._reply(404, {"error": "Not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/command": return self._reply(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096: raise ValueError("Invalid command length")
            data = loads(self.rfile.read(length))
            command, identifier = data.get("command"), data.get("bus", "BUS-01")
            if command == "tick": engine.tick(data.get("steps", 1))
            elif command == "return": engine.request_depot(identifier)
            elif command == "dock": engine.confirm_docked(identifier)
            elif command == "dispatch": engine.dispatch(identifier)
            elif command == "stop": engine.emergency_stop()
            elif command == "clear": engine.clear_interlock()
            elif command == "reset": engine.reset_bus(identifier)
            elif command == "add": engine.add_bus(identifier)
            else: raise ValueError("Unknown command")
            self._reply(200, engine.snapshot())
        except (ValueError, KeyError, TypeError) as e: self._reply(400, {"error": str(e)})

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("TransitOps-M simulation: http://127.0.0.1:8765")
    server.serve_forever()

if __name__ == "__main__": main()
