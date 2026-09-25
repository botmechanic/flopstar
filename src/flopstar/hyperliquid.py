"""Hyperliquid's latest xyz:NVDA trade, for pricing the tree's trades when they are posted.

Round triggers never use this: they use the referee's verified reference. This only sets the
price of a trade so that it lands near the sweep's close and pays the flat 1% fee.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx

INFO_URL = "https://api.hyperliquid.xyz/info"
COIN = "xyz:NVDA"


@dataclass
class LastTrade:
    px: Decimal
    time: datetime
    tid: int


def parse_last_trade(trades: list[dict]) -> LastTrade | None:
    """The newest trade in a recentTrades reply (ties broken by tid, as the referee's are)."""
    ours = [t for t in trades if t.get("coin") == COIN]
    if not ours:
        return None
    t = max(ours, key=lambda t: (int(t["time"]), int(t["tid"])))
    return LastTrade(Decimal(t["px"]), datetime.fromtimestamp(int(t["time"]) / 1000, UTC),
                     int(t["tid"]))


async def last_trade(client: httpx.AsyncClient) -> LastTrade | None:
    response = await client.post(INFO_URL, json={"type": "recentTrades", "coin": COIN},
                                 timeout=10)
    response.raise_for_status()
    return parse_last_trade(response.json())
