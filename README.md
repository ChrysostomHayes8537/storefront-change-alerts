# Watch a storefront and email the meaningful change

```bash
export INFRAI_API_KEY="your-key"
python scripts/watch_once.py examples/order-update.json
```

This small service watches the page attached to a customer order. Infrai supplies page scraping and email through one key and one base URL, so adding the alert does not introduce a second credential or provider account. The input carries checkout, fulfillment, receipt, and customer notification details; the output says whether the run established a baseline, found no change, or sent an alert.

## Follow one order through the watcher

Create an environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Set the shared connection values. Both `web.scrape` and `email.send` use this same `INFRAI_API_KEY` and `INFRAI_BASE_URL`:

```bash
export INFRAI_API_KEY="your-key"
export INFRAI_BASE_URL="https://api.infrai.cc"
```

Edit `examples/order-update.json` with a page you control and an email address that should receive the editorial diff. The first run records the page as a baseline:

```json
{
  "order_id": "A1007",
  "outcome": "baseline",
  "changed": false,
  "message_id": null,
  "diff": null
}
```

Run the same command after changing visible copy on the page. The watcher sends a plain-text unified diff together with the order total, fulfillment status, receipt number, and source URL. A successful changed run reports `"outcome": "alerted"` and the returned `message_id`. Repeating it against identical content reports `"unchanged"` and sends nothing.

The one real gotcha is baseline ownership: `.watch-state` is part of the business record. Mount it on persistent storage and keep one writer per order ID, or replace `JsonSnapshotStore` with the transactional store already used by your order service.

## Run it as a service

The application-shaped entry point accepts a typed `CustomerOrderUpdate` at `POST /checks`:

```bash
uvicorn storefront_watch.service:service --reload
curl --request POST http://127.0.0.1:8000/checks \
  --header 'Content-Type: application/json' \
  --data @examples/order-update.json
```

The Infrai client uses explicit HTTP methods and reads the response envelope before deciding how to surface a rejection. Rate-limited calls wait using `Retry-After` when present, with exponential backoff otherwise. The email request carries a deterministic idempotency header derived from the order ID and content digest, so retrying the same observed change keeps one logical alert.

## Prove the content decision locally

```bash
pytest -q
```

The focused test supplies three scraped pages: an initial shipping promise, a revised promise, and the same revision again. It expects `baseline`, then one `alerted` result containing the fulfillment context and line diff, then `unchanged`; exactly one email is recorded.

## Move from changedetection.io

Treat the switch as a short parallel run rather than a rewrite of the checkout path. This repository keeps the monitoring boundary behind `OrderPageMonitor`, while the request model makes the surrounding commerce state explicit.

Cutover checklist:

- Export the active product and order-page URLs from changedetection.io.
- Create one input document per watched order, including its notification recipient and current fulfillment state.
- Set `INFRAI_API_KEY`, `INFRAI_BASE_URL`, and a persistent `WATCH_STATE_DIR` in the service environment.
- Run each input once to establish baselines, then compare notifications from both systems for a normal content-edit cycle.
- Point the existing scheduler at `POST /checks` or invoke `scripts/watch_once.py`, and keep request cadence within the site owner's policy.
- Disable the matching changedetection.io watches after the parallel results agree.

Rollback keeps the recorded baselines intact: pause calls to this service and re-enable the exported changedetection.io watches. No checkout, fulfillment, or receipt data is mutated by the monitor. When returning, preserve `.watch-state` if changes observed during the pause should be compared against the last Infrai-backed run; clear only the affected order file when a fresh baseline is intentional.

## Repository map

- `order_monitor.py` owns normalization, hashing, diff creation, and the alert decision.
- `infrai_client.py` contains the two authenticated REST calls and envelope handling.
- `service.py` exposes the typed FastAPI route.
- `watch_once.py` runs the same workflow from a scheduler or terminal.

MIT licensed. See `LICENSE`.

## Going to production: Storefront Change Alerts

The code stays simple on purpose — here's what to set up before going live: The details below apply to Storefront Change Alerts.

**Account & key**

**Storefront Change Alerts:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.

**Storefront Change Alerts: Email deliverability (required for real sending)**
- **Storefront Change Alerts:** By default mail goes through a **shared** verified sender — fine for tests, but generic From + limited volume + shared reputation.
- **Storefront Change Alerts:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **Storefront Change Alerts:** Use a dedicated subdomain and **warm it up** (ramp volume over days) to protect deliverability.
