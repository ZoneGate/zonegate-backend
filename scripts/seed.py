"""Seeds the Zova store with the demo actors and their device bindings.

Run this with the API server STOPPED. Zova is an embedded database: if the
server holds the same file open, writes from this process are silently lost.

    docker compose stop zonegate
    docker compose run --rm zonegate python scripts/seed.py
    docker compose start zonegate

Locally, with the venv active:

    uv run python scripts/seed.py
"""

import asyncio
import os
from datetime import datetime, timezone

from zonegate.config import get_settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.storage.zova import ZoneGateStore

DEMO_ACTORS: list[Actor] = [
    Actor(
        actor_id="usr_cargo_operator_01",
        role="CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
        enrollment_status="ACTIVE",
    ),
    Actor(
        actor_id="usr_cargo_operator_02",
        role="CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550142",
        registered_device_id="dev_imei_44112200",
        enrollment_status="ACTIVE",
    ),
    Actor(
        actor_id="usr_courier_07",
        role="PRIORITY_COURIER",
        permissions=["cargo:release"],
        registered_phone_number="+14155550310",
        registered_device_id="dev_imei_77553311",
        enrollment_status="ACTIVE",
    ),
    # Enrolled but without the cargo:release permission, so the policy engine
    # denies them on rule 1 rather than on a missing enrolment.
    Actor(
        actor_id="usr_yard_runner_12",
        role="YARD_RUNNER",
        permissions=["yard:move"],
        registered_phone_number="+14155550288",
        registered_device_id="dev_imei_10293847",
        enrollment_status="ACTIVE",
    ),
]


async def main() -> None:
    path = os.environ.get("ZOVA_DB_PATH") or get_settings().ZOVA_DB_PATH
    store = ZoneGateStore.open_or_create(path)

    try:
        now = datetime.now(timezone.utc)

        for actor in DEMO_ACTORS:
            await store.save_actor(actor)
            await store.save_device_binding(
                DeviceBinding(
                    actor_id=actor.actor_id,
                    phone_number=actor.registered_phone_number,
                    device_id=actor.registered_device_id,
                    bound_at=now,
                    is_active=True,
                )
            )
            print(f"enrolled {actor.actor_id} ({actor.role})")

        print(f"\n{len(DEMO_ACTORS)} actors seeded into {path}")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
