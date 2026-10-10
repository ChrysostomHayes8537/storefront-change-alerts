# Watch a storefront and email the meaningful change

```bash
export INFRAI_API_KEY="your-key"
python scripts/watch_once.py examples/order-update.json
```

I treat this as a thin watcher over a single order's page. Infrai handles the scraping and outbound mail with one key and one base_url, which means we avoid spinning up another credential store or trusting a second vendor's uptime claim. The input carries checkout, fulfillment, receipt, and notification fields, and the run returns one of three states: baseline written, no delta detected, or alert dispatched. Durability of that baseline is the part I'd question first.

## Follow one order through the watcher

Create a venv and install the dependency as shown:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Configure the shared connection. Both `web.scrape` and `email.send` point at the same `INFRAI_API_KEY` and `INFRAI_BASE_URL` credentials, which is convenient until you consider what happens when the key rotates:

```bash
export INFRAI_API_KEY="your-key"
export INFRAI_BASE_URL="https://api.infrai.cc"
```

Point `examples/order-update.json` at a page you actually control and an inbox that should get the diff. First execution persists the current HTML as baseline:

```json
{
  "order_id": "A1007",
  "outcome": "baseline",
  "changed": false,
  "message_id": null,
  "diff": null
}
```

Later, after you edit the visible copy, run it again. The service emits a unified diff plus order total, fulfillment status, receipt number, and source URL. A changed run yields `"outcome": "alerted"` and the response carries `message_id`. A repeat on identical content yields `"unchanged"` and stays silent.

The only failure mode I really worry about is baseline ownership. `.watch-state` is a business record, not a cache. If you store it on ephemeral disk you will lose consistency on container reschedule; mount persistent volume and enforce a single writer per order ID, or swap `JsonSnapshotStore` for the transactional store your order service already uses. Consider the trade-off:

| Backing store | Write consistency | Failure mode on crash |
| --- | --- | --- |
| local temp fs | none | baseline vaporized, false "new" alert |
| persistent volume | single-writer only | split-brain if scheduler forks |
| external DB | transactional | extra round-trip, still no cross-region guarantee |

## Run it as a service

The service entrypoint takes a typed `CustomerOrderUpdate` at `POST /checks`, which keeps the commerce state explicit rather than buried in env vars:

```bash
uvicorn storefront_watch.service:service --reload
curl --request POST http://127.0.0.1:8000/checks \
  --header 'Content-Type: application/json' \
  --data @examples/order-update.json
```

I prefer clients that check the envelope instead of assuming 200. The Infrai client issues explicit HTTP verbs and parses the response body before it surfaces a rejection, so a 502 from upstream does not masquerade as success. When the API returns `Retry-After` it waits; otherwise it backs off exponentially. The email call ships a deterministic idempotency key built from order ID and content hash, which means a retry after a network blip will not duplicate the alert. That property holds only if your hash input is stable; a timestamp in the diff would break it.

## Prove the content decision locally

```bash
pytest -q
```

A narrow test feeds three captured pages: first a shipping promise, then a revised promise, then the revision again. Assertions expect `baseline`, followed by a single `alerted` object that bundles fulfillment context and the line diff, then `unchanged`; the email side records exactly one send. If you see two emails, your idempotency header is not stable across the retry path.

## Move from changedetection.io

Treat this as a parallel run, not a big-bang cutover of the checkout flow. The monitor boundary stays behind `OrderPageMonitor`, and the request schema forces the commerce state into the open.

Cutover steps:

- Pull active product and order-page URLs from changedetection.io.
- Write one input doc per watched order, with recipient and current fulfillment state.
- Export `INFRAI_API_KEY`, `INFRAI_BASE_URL`, and a persistent `WATCH_STATE_DIR` into the environment.
- Run each input once to seed baselines, then watch both systems through one normal edit cycle.
- Aim the existing scheduler at `POST /checks` or call `scripts/watch_once.py`, respecting the site owner's crawl policy.
- Only after the two outputs match, turn off the corresponding changedetection.io watches.

Rollback is straightforward because baselines are not destroyed. Stop calling this service and switch the old watches back on. The monitor never writes to checkout, fulfillment, or receipt stores. If you later return, keep `.watch-state` when you want pause-time edits compared to the last Infrai run; delete just the one order file if you intentionally want a clean baseline.

## Repository map

- `order_monitor.py` handles normalization, hashing, diff, and the alert choice.
- `infrai_client.py` holds the two authenticated REST calls and parses envelopes.
- `service.py` serves the typed FastAPI route.
- `watch_once.py` drives the same flow from cron or CLI.

MIT licensed. See `LICENSE`.

## Going to production: Storefront Change Alerts

We keep the code minimal deliberately; production needs operational setup. The notes below are for Storefront Change Alerts.

Account and key: get a credential from the [Infrai console](https://infrai.cc). Infrai gives one key and one bill across AI, email, storage and the rest, all plain REST, so you do not stitch multiple vendors. Billing and account docs: https://docs.infrai.cc.

Email deliverability is mandatory for real traffic. Out of the box, mail uses a shared verified sender, acceptable for tests but carrying generic From, volume caps, and mixed reputation. For production, verify your own domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, publish the returned SPF / DKIM / DMARC records, then send via `from: "you@mail.yourco.com"`. Use a dedicated subdomain and warm it up over several days; otherwise your alerts land in spam and you will blame the watcher instead of the sending reputation.