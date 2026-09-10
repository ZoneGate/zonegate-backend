"""Development-only stand-in for the Nokia / CAMARA gateway.

The configured NOKIA_BASE_URL points at a placeholder domain, so without a real
carrier endpoint every request dies at evidence collection and the pipeline can
only answer DENY. This server answers the five CAMARA endpoints the Evidence
Gateway calls, so the full pipeline can be exercised offline.

    python scripts/mock_camara.py          # listens on 0.0.0.0:8899

Point the API at it:

    NOKIA_BASE_URL=http://host.docker.internal:8899   # from Docker
    NOKIA_BASE_URL=http://127.0.0.1:8899              # running locally

Responses are driven by scripts/camara_state.json, which can be edited while
the server runs to stage a scenario:

    {"location_verified": false}   # presence attack: device outside the zone
    {"sim_swapped": true}          # recent SIM swap on the subscriber line
    {"number_verified": false}     # subscriber number does not match

NEVER deploy this. It asserts network facts without verifying anything.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MOCK_CAMARA_PORT", "8899"))
STATE_FILE = os.path.join(os.path.dirname(__file__), "camara_state.json")

DEFAULTS = {
    "number_verified": True,
    "location_verified": True,
    "sim_swapped": False,
    "device_swapped": False,
    "reachability": "CONNECTED_DATA",
}


def current_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            return {**DEFAULTS, **json.load(handle)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


class CamaraHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console readable
        pass

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        state = current_state()
        path = self.path

        if path.endswith("/number-verification/v1/verify"):
            body = {"devicePhoneNumberVerified": state["number_verified"]}
        elif path.endswith("/location-verification/v1/verify"):
            body = {"verificationResult": "TRUE" if state["location_verified"] else "FALSE"}
        elif path.endswith("/sim-swap/v1/check"):
            body = {"swapped": state["sim_swapped"]}
        elif path.endswith("/device-swap/v1/check"):
            body = {"swapped": state["device_swapped"]}
        elif path.endswith("/device-reachability/v1/reachability-status"):
            body = {"reachabilityStatus": state["reachability"]}
        else:
            self.send_response(404)
            self.end_headers()
            return

        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    print(f"Mock CAMARA gateway listening on 0.0.0.0:{PORT}")
    print(f"Scenario state: {STATE_FILE}")
    HTTPServer(("0.0.0.0", PORT), CamaraHandler).serve_forever()
