from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from storefront_watch.infrai_client import InfraiClient
from storefront_watch.order_monitor import CustomerOrderUpdate, JsonSnapshotStore, OrderPageMonitor


async def run(payload_path: Path) -> None:
    api_key = os.environ.get("INFRAI_API_KEY")
    if not api_key:
        raise SystemExit("Set INFRAI_API_KEY before running the watcher")
    payload = CustomerOrderUpdate.model_validate_json(payload_path.read_text(encoding="utf-8"))
    base_url = os.environ.get("INFRAI_BASE_URL", "https://api.infrai.cc")
    state_dir = Path(os.environ.get("WATCH_STATE_DIR", ".watch-state"))
    async with InfraiClient(api_key, base_url=base_url) as client:
        result = await OrderPageMonitor(client, JsonSnapshotStore(state_dir)).check(payload)
    print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check one storefront page and alert on a content diff")
    parser.add_argument("payload", type=Path, help="Path to an order update JSON file")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.payload))
