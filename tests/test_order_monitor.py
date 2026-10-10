import asyncio

from storefront_watch.order_monitor import (
    CustomerOrderUpdate,
    JsonSnapshotStore,
    OrderPageMonitor,
)


class RecordingDelivery:
    def __init__(self, pages: list[str]) -> None:
        self.pages = iter(pages)
        self.emails: list[dict[str, str]] = []

    async def scrape(self, url: str) -> str:
        return next(self.pages)

    async def send_email(
        self, *, to: str, subject: str, text: str, idempotency_key: str
    ) -> str:
        self.emails.append(
            {"to": to, "subject": subject, "text": text, "idempotency_key": idempotency_key}
        )
        return "msg_42"


def test_changed_page_sends_order_context_and_updates_snapshot(tmp_path) -> None:
    delivery = RecordingDelivery(
        [
            "# Camera kit\nShips in 3 days",
            "# Camera kit\nShips tomorrow",
            "# Camera kit\nShips tomorrow",
        ]
    )
    monitor = OrderPageMonitor(delivery, JsonSnapshotStore(tmp_path))
    update = CustomerOrderUpdate.model_validate(
        {
            "page_url": "https://shop.example.com/orders/A1007",
            "alert_to": "editor@example.com",
            "checkout": {"order_id": "A1007", "currency": "USD", "total_minor": 12900},
            "fulfillment": {"status": "packing"},
            "receipt": {"receipt_number": "R-1007", "issued_at": "2026-09-25T09:30:00Z"},
        }
    )

    first = asyncio.run(monitor.check(update))
    changed = asyncio.run(monitor.check(update))
    stable = asyncio.run(monitor.check(update))

    assert first.outcome == "baseline"
    assert changed.outcome == "alerted"
    assert changed.message_id == "msg_42"
    assert "-Ships in 3 days" in changed.diff
    assert "+Ships tomorrow" in changed.diff
    assert delivery.emails[0]["to"] == "editor@example.com"
    assert "Fulfillment: packing" in delivery.emails[0]["text"]
    assert delivery.emails[0]["idempotency_key"].startswith("page-change-A1007-")
    assert stable.outcome == "unchanged"
    assert len(delivery.emails) == 1
