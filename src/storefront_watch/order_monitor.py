from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Checkout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    total_minor: int = Field(ge=0)


class Fulfillment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["unfulfilled", "packing", "shipped", "delivered"]
    carrier: str | None = None
    tracking_reference: str | None = None


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_number: str
    issued_at: str


class CustomerOrderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_url: HttpUrl
    alert_to: str = Field(min_length=3)
    checkout: Checkout
    fulfillment: Fulfillment
    receipt: Receipt

    @field_validator("alert_to")
    @classmethod
    def validate_alert_recipient(cls, value: str) -> str:
        local, separator, domain = value.rpartition("@")
        if not separator or not local or "." not in domain:
            raise ValueError("alert_to must be an email address")
        return value


class WatchResult(BaseModel):
    order_id: str
    outcome: Literal["baseline", "unchanged", "alerted"]
    changed: bool
    message_id: str | None = None
    diff: str | None = None


class ChangeDelivery(Protocol):
    async def scrape(self, url: str) -> str:
        raise NotImplementedError

    async def send_email(
        self, *, to: str, subject: str, text: str, idempotency_key: str
    ) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    digest: str
    content: str


class JsonSnapshotStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def read(self, order_id: str) -> PageSnapshot | None:
        path = self._path(order_id)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return PageSnapshot(digest=value["digest"], content=value["content"])

    def write(self, order_id: str, snapshot: PageSnapshot) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path(order_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"digest": snapshot.digest, "content": snapshot.content}),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _path(self, order_id: str) -> Path:
        safe_id = "".join(character for character in order_id if character.isalnum() or character in "-_")
        if not safe_id or safe_id != order_id:
            raise ValueError("order_id may contain only letters, numbers, hyphens, and underscores")
        return self._directory / f"{safe_id}.json"


class OrderPageMonitor:
    def __init__(self, delivery: ChangeDelivery, snapshots: JsonSnapshotStore) -> None:
        self._delivery = delivery
        self._snapshots = snapshots

    async def check(self, update: CustomerOrderUpdate) -> WatchResult:
        content = self._normalize(await self._delivery.scrape(str(update.page_url)))
        current = PageSnapshot(
            digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content=content,
        )
        previous = self._snapshots.read(update.checkout.order_id)

        if previous is None:
            self._snapshots.write(update.checkout.order_id, current)
            return WatchResult(order_id=update.checkout.order_id, outcome="baseline", changed=False)

        if previous.digest == current.digest:
            return WatchResult(order_id=update.checkout.order_id, outcome="unchanged", changed=False)

        diff = "\n".join(
            difflib.unified_diff(
                previous.content.splitlines(),
                current.content.splitlines(),
                fromfile="previous storefront copy",
                tofile="current storefront copy",
                lineterm="",
            )
        )
        message_id = await self._delivery.send_email(
            to=str(update.alert_to),
            subject=f"Storefront changed for order {update.checkout.order_id}",
            text=self._alert_text(update, diff),
            idempotency_key=f"page-change-{update.checkout.order_id}-{current.digest}",
        )
        self._snapshots.write(update.checkout.order_id, current)
        return WatchResult(
            order_id=update.checkout.order_id,
            outcome="alerted",
            changed=True,
            message_id=message_id,
            diff=diff,
        )

    @staticmethod
    def _normalize(content: str) -> str:
        return "\n".join(line.rstrip() for line in content.splitlines()).strip()

    @staticmethod
    def _alert_text(update: CustomerOrderUpdate, diff: str) -> str:
        return (
            f"Order: {update.checkout.order_id}\n"
            f"Total: {update.checkout.total_minor} {update.checkout.currency}\n"
            f"Fulfillment: {update.fulfillment.status}\n"
            f"Receipt: {update.receipt.receipt_number} ({update.receipt.issued_at})\n"
            f"Page: {update.page_url}\n\n"
            f"Content diff:\n{diff}"
        )
