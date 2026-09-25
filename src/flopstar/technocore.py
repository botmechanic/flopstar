"""Technocore.chat API client."""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://technocore.chat"
LONGPOLL_TIMEOUT = 10  # Server-side timeout
REQUEST_TIMEOUT = 15   # Client timeout (slightly higher)
MAX_CONCURRENT_LONGPOLLS = 1  # Only 4 per IP; use 1 to be safe


class TechnocoreClient:
    """Client for technocore.chat API."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT + 5, connect=5),
        )
        self.last_429 = None
        self.wait_until = None

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _handle_rate_limit(self, response: httpx.Response):
        """Handle 429 rate limiting; the body says "retry after: Ns"."""
        match = re.search(r"retry after:\s*(\d+(?:\.\d+)?)", response.text, re.IGNORECASE)
        if match:
            seconds = float(match.group(1)) + 1
        else:
            seconds = float(response.headers.get("Retry-After", 60))
        logger.warning(f"Rate limited (429), waiting {seconds:.0f} seconds")
        self.wait_until = datetime.now(UTC) + timedelta(seconds=seconds)

    async def _wait_if_rate_limited(self):
        """Wait if we're currently rate limited."""
        if self.wait_until and datetime.now(UTC) < self.wait_until:
            wait_seconds = (self.wait_until - datetime.now(UTC)).total_seconds()
            if wait_seconds > 0:
                logger.info(f"Waiting {wait_seconds:.1f}s due to rate limit")
                await asyncio.sleep(wait_seconds)
                self.wait_until = None

    async def get_room(
        self,
        room: str,
        since: int | None = None,
        limit: int | None = None,
        wait: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch messages from a room.

        Args:
            room: Room name
            since: Fetch messages after this sequence number
            limit: Maximum number of messages (default: all new)
            wait: Use long-polling (10s server timeout)

        Returns:
            List of message records (newest first with limit, chronological otherwise)

        Note: With ?since=&limit=, server returns NEWEST N messages.
        """
        await self._wait_if_rate_limited()

        params = {"format": "json"}
        if since is not None:
            params["since"] = str(since)
        if limit is not None:
            params["limit"] = str(limit)
        if wait:
            params["wait"] = str(LONGPOLL_TIMEOUT)

        try:
            response = await self.client.get(f"/r/{room}", params=params)

            if response.status_code == 429:
                await self._handle_rate_limit(response)
                return []

            response.raise_for_status()
            data = response.json()

            # Handle different response formats
            if isinstance(data, dict) and "messages" in data:
                return data["messages"]
            elif isinstance(data, list):
                return data
            else:
                logger.warning(f"Unexpected response format: {type(data)}")
                return []

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching {room}: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error fetching {room}: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {room}: {e}")
            return []

    async def longpoll_room(
        self,
        room: str,
        since: int,
    ) -> list[dict[str, Any]]:
        """
        Long-poll a room for new messages.

        Server waits up to 10s for new messages after 'since'.
        Returns immediately if messages are available.
        """
        return await self.get_room(room, since=since, wait=True)

    async def post_signed(
        self, room: str, did: str, sig: str, nonce: int, text: str
    ) -> httpx.Response:
        """POST a signed message; the caller checks the status (422 = duplicate text)."""
        await self._wait_if_rate_limited()
        response = await self.client.post(
            f"/r/{room}", json={"did": did, "sig": sig, "nonce": str(nonce), "text": text}
        )
        if response.status_code == 429:
            await self._handle_rate_limit(response)
        return response

    async def get_note(self, namespace: str, key: str) -> str | None:
        """Read a note's value, or None if it does not exist."""
        await self._wait_if_rate_limited()
        response = await self.client.get(f"/kv/{namespace}/{key}")
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            await self._handle_rate_limit(response)
        response.raise_for_status()
        return response.text

    async def set_note_signed(
        self, namespace: str, key: str, did: str, sig: str, nonce: int, value: str,
        if_absent: bool = False,
    ) -> httpx.Response:
        """Signed note write (room-owners / room-allow only); 409 means someone beat us."""
        await self._wait_if_rate_limited()
        response = await self.client.post(
            f"/kv/{namespace}/{key}",
            json={"did": did, "sig": sig, "nonce": str(nonce), "value": value,
                  **({"if_absent": True} if if_absent else {})},
        )
        if response.status_code == 429:
            await self._handle_rate_limit(response)
        return response

    async def export_lines(self, room: str) -> list[str]:
        """The export's raw lines, byte-for-byte, for keeping re-verifiable evidence."""
        await self._wait_if_rate_limited()
        response = await self.client.get(f"/r/{room}/export", timeout=90)
        response.raise_for_status()
        return [line for line in response.text.splitlines() if line.strip()]

    async def export_room(self, room: str) -> list[dict[str, Any]]:
        """
        Fetch the whole retained ring from /r/<room>/export.

        The body is raw JSONL, one record per line, byte-for-byte as written; the endpoint
        takes no query params. json.loads keeps 19-digit nonces exact (Python ints).
        Used to fill gaps: ?since=&limit= returns only the NEWEST messages after since.
        """
        await self._wait_if_rate_limited()

        try:
            response = await self.client.get(f"/r/{room}/export")

            if response.status_code == 429:
                await self._handle_rate_limit(response)
                return []

            response.raise_for_status()
            messages = []
            for line in response.text.splitlines():
                if line.strip():
                    messages.append(json.loads(line))
            return messages

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error exporting {room}: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error exporting {room}: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error exporting {room}: {e}")
            return []
