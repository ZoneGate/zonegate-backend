"""ZoneGate Demo Seed Script.

Safely enrolls baseline demo actors and device bindings.
- If the ZoneGate backend HTTP server is running, seeds via REST API (avoids embedded DB file conflicts).
- If the server is offline, seeds directly into the local Zova database file.
"""

from datetime import datetime, timezone
import json
import sys
import urllib.error
import urllib.request
from zonegate.config import get_settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.storage.zova import ZoneGateStore

DEMO_ACTOR = Actor(
    actor_id="usr_cargo_operator_01",
    role="CARGO_OPERATOR",
    permissions=["cargo:release", "cargo:inspect"],
    registered_phone_number="+358501234567",
    registered_device_id="device_cargo_terminal_01",
    enrollment_status="ACTIVE",
)

DEMO_BINDING = DeviceBinding(
    actor_id=DEMO_ACTOR.actor_id,
    phone_number=DEMO_ACTOR.registered_phone_number,
    device_id=DEMO_ACTOR.registered_device_id,
    bound_at=datetime.now(timezone.utc),
    is_active=True,
)


def is_server_running(base_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{base_url}/health")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def seed_via_api(base_url: str) -> None:
    print(f"Connecting to running ZoneGate server at {base_url}...")
    headers = {"Content-Type": "application/json"}

    # 1. Seed actor
    actor_req = urllib.request.Request(
        f"{base_url}/v1/actors",
        data=DEMO_ACTOR.model_dump_json().encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(actor_req) as resp:
        print(f"  [API] Seeded Actor: {DEMO_ACTOR.actor_id} (HTTP {resp.status})")

    # 2. Seed device binding
    binding_req = urllib.request.Request(
        f"{base_url}/v1/device-bindings",
        data=DEMO_BINDING.model_dump_json().encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(binding_req) as resp:
        print(f"  [API] Seeded Device Binding: {DEMO_BINDING.actor_id} -> {DEMO_BINDING.device_id} (HTTP {resp.status})")


def seed_via_direct_store() -> None:
    cfg = get_settings()
    print(f"Server not running. Writing directly to local Zova database at '{cfg.ZOVA_DB_PATH}'...")
    store = ZoneGateStore.open_or_create(cfg.ZOVA_DB_PATH)
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.save_actor(DEMO_ACTOR))
        loop.run_until_complete(store.save_device_binding(DEMO_BINDING))
        loop.close()
        print(f"  [Zova Direct] Seeded Actor: {DEMO_ACTOR.actor_id}")
        print(f"  [Zova Direct] Seeded Device Binding: {DEMO_BINDING.actor_id} -> {DEMO_BINDING.device_id}")
    finally:
        store.close()


def main() -> None:
    cfg = get_settings()
    api_url = f"http://{cfg.APP_HOST if cfg.APP_HOST != '0.0.0.0' else '127.0.0.1'}:{cfg.APP_PORT}"

    if is_server_running(api_url):
        seed_via_api(api_url)
    else:
        seed_via_direct_store()
    print("Seeding completed successfully!")


if __name__ == "__main__":
    main()
