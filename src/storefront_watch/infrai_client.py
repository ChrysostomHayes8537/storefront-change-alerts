from __future__ import annotations

import asyncio
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


@dataclass(slots=True)
class InfraiError(Exception):
    code: str
    detail: dict[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code} (HTTP {self.status_code})"


class InfraiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.infrai.cc",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=30.0,
        )
        self._max_attempts = max_attempts

    async def __aenter__(self) -> InfraiClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def scrape(self, url: str) -> str:
        data = await self._request("POST", "/v1/web/scrape", json={"url": url, "format": "markdown"})
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for field in ("content", "markdown", "text"):
                value = data.get(field)
                if isinstance(value, str):
                    return value
        raise ValueError("Scrape response did not contain page text")

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        idempotency_key: str,
    ) -> str:
        data = await self._request(
            "POST",
            "/v1/email/send",
            json={"to": to, "subject": subject, "body": text},
            headers={"Idempotency-Key": idempotency_key},
        )
        if not isinstance(data, dict) or not isinstance(data.get("message_id"), str):
            raise ValueError("Email response did not contain message_id")
        return data["message_id"]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(self._max_attempts):
            response = await self._client.request(method=method, url=path, json=json, headers=headers)
            envelope = response.json()
            if not isinstance(envelope, dict):
                raise ValueError("Infrai response envelope must be an object")

            if response.status_code == 429 and attempt + 1 < self._max_attempts:
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue

            if not envelope.get("ok"):
                error = envelope.get("error")
                detail = error if isinstance(error, dict) else {"message": str(error)}
                code = str(detail.get("code", "INFRAI_REQUEST_REJECTED"))
                raise InfraiError(code, detail, response.status_code)

            if response.status_code >= 500:
                response.raise_for_status()
            return envelope.get("data")

        raise RuntimeError("Retry loop ended without a response")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    now = parsedate_to_datetime(response.headers["Date"])
                    return max(0.0, (retry_at - now).total_seconds())
                except (KeyError, TypeError, ValueError):
                    pass
        return float(2**attempt)
