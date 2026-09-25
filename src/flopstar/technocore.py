"""Technocore.chat API client."""

import asyncio
import json
import logging
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
        """Handle 429 rate limiting."""
        if response.status_code == 429:
            # Check for "# wait: not held" in body
            body = response.text
            if "wait: not held" in body:
                logger.warning("Rate limited: wait: not held")
                self.wait_until = datetime.now(UTC) + timedelta(seconds=60)
                return

            # Generic rate limit
            logger.warning("Rate limited (429), waiting 60 seconds")
            self.wait_until = datetime.now(UTC) + timedelta(seconds=60)

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

    async def export_room(
        self,
        room: str,
        start_seq: int,
        end_seq: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch a range of messages from /r/<room>/export.

        Used to fill gaps when detecting missing sequences.
        """
        await self._wait_if_rate_limited()

        try:
            response = await self.client.get(
                f"/r/{room}/export",
                params={
                    "format": "json",
                    "start": str(start_seq),
                    "end": str(end_seq),
                }
            )

            if response.status_code == 429:
                await self._handle_rate_limit(response)
                return []

            response.raise_for_status()
            data = response.json()

            if isinstance(data, dict) and "messages" in data:
                return data["messages"]
            elif isinstance(data, list):
                return data
            else:
                return []

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error exporting {room}: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error exporting {room}: {e}")
            return []
