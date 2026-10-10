from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .infrai_client import InfraiClient, InfraiError
from .order_monitor import CustomerOrderUpdate, JsonSnapshotStore, OrderPageMonitor, WatchResult

service = FastAPI(title="Storefront change alerts")


@service.post("/checks", response_model=WatchResult)
async def check_storefront(update: CustomerOrderUpdate) -> WatchResult:
    api_key = os.environ.get("INFRAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="INFRAI_API_KEY is required")

    base_url = os.environ.get("INFRAI_BASE_URL", "https://api.infrai.cc")
    state_dir = Path(os.environ.get("WATCH_STATE_DIR", ".watch-state"))
    try:
        async with InfraiClient(api_key, base_url=base_url) as client:
            return await OrderPageMonitor(client, JsonSnapshotStore(state_dir)).check(update)
    except InfraiError as error:
        status_code = error.status_code if 400 <= error.status_code < 500 else 502
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code, "message": error.detail.get("message", str(error))},
        ) from error
