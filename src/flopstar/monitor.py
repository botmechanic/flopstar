"""Read-only monitor for technocore Close Call contest."""

import asyncio
import json
import logging

from . import config
from .didkey import verify_signature
from .store import MessageStore
from .technocore import TechnocoreClient

logger = logging.getLogger(__name__)

SWEEP_SETTLE_SECONDS = 15


class ContestMonitor:
    """
    Read-only monitor for Close Call contest.

    - Long-polls d-close1-price only (to stay under 4 concurrent limit)
    - Reads other referee rooms once after each new price sweep
    - Validates all referee messages against REFEREE_DID
    - Stores all messages in append-only SQLite
    - Detects and fills sequence gaps
    """

    def __init__(self, store: MessageStore, client: TechnocoreClient):
        self.store = store
        self.client = client
        self.running = False

        # Warn about provisional trust anchor
        logger.warning(
            "⚠️  Using PROVISIONAL trust anchor from sonnet-2 launch record. "
            "Close-1 has no signed launch record yet. "
            f"Referee DID: {config.REFEREE_DID}"
        )

    async def verify_referee_message(self, room: str, msg: dict) -> bool:
        """
        Verify a message is signed by the referee.

        All referee room content is untrusted data until verified.
        """
        did = msg.get("from")
        nonce = msg.get("nonce")
        text = msg.get("text")
        sig = msg.get("sig")

        if not did or nonce is None or not text or not sig:
            logger.warning(f"Message missing required fields in {room}: {msg.get('seq')}")
            return False

        if did != config.REFEREE_DID:
            logger.warning(
                f"Message in referee room {room} from non-referee DID: {did}"
            )
            return False

        # Verify signature
        if not verify_signature(did, room, str(nonce), text, sig):
            logger.error(
                f"INVALID SIGNATURE in referee room {room}, seq {msg.get('seq')}"
            )
            return False

        return True

    async def fill_gap(self, room: str, since: int, first_new: int):
        """Fill seqs since+1 .. first_new-1 from the export (the whole retained ring)."""
        logger.info(f"Filling gap in {room}: {since+1} to {first_new-1}")

        exported = await self.client.export_room(room)
        missing = [m for m in exported if since < m.get("seq", 0) < first_new]

        if missing:
            verified = [m for m in missing if await self.verify_referee_message(room, m)]
            self.store.store_messages(room, verified)
            logger.info(f"Filled gap in {room}: {len(verified)} messages")
        else:
            logger.warning(f"Failed to fill gap in {room} (export empty or ring already rotated)")

    async def ingest(self, room: str, since: int, messages: list[dict]):
        """Backfill any gap before `messages`, then verify and store them."""
        if not messages:
            return

        # ?since=S returns the NEWEST messages after S, so the oldest ones may be skipped
        first_seq = messages[0].get("seq")
        if first_seq and since + 1 < first_seq:
            await self.fill_gap(room, since, first_seq)

        verified = [m for m in messages if await self.verify_referee_message(room, m)]
        self.store.store_messages(room, verified)

        if verified:
            logger.info(f"Synced {room}: {len(verified)} new messages")

    async def sync_room(self, room: str):
        """Sync a room from the last known sequence."""
        since = self.store.get_latest_seq(room) or 0
        logger.debug(f"Syncing {room} since {since}")
        messages = await self.client.get_room(room, since=since, limit=200)
        await self.ingest(room, since, messages)

    async def sync_other_rooms(self):
        """Sync the referee rooms that post after the price room each sweep."""
        # The other four rooms post a few seconds after the price room
        await asyncio.sleep(SWEEP_SETTLE_SECONDS)
        for room in config.REFEREE_ROOMS:
            if room != config.LONGPOLL_ROOM:
                await self.sync_room(room)

    async def monitor_loop(self):
        """Main monitoring loop."""
        self.running = True

        # Initial sync of all rooms
        logger.info("Starting initial sync of all referee rooms")
        for room in config.REFEREE_ROOMS:
            await self.sync_room(room)

        # Long-poll the price room
        logger.info(f"Starting long-poll on {config.LONGPOLL_ROOM}")

        while self.running:
            try:
                latest_seq = self.store.get_latest_seq(config.LONGPOLL_ROOM) or 0

                messages = await self.client.longpoll_room(
                    config.LONGPOLL_ROOM,
                    since=latest_seq
                )

                if messages:
                    await self.ingest(config.LONGPOLL_ROOM, latest_seq, messages)
                    for msg in messages:
                        try:
                            sweep = json.loads(msg.get("text", "{}")).get("n")
                        except json.JSONDecodeError:
                            sweep = None
                        if sweep is not None:
                            logger.info(f"New sweep {sweep}")
                    await self.sync_other_rooms()

                # Small delay to prevent tight loop on errors
                await asyncio.sleep(1)

            except Exception:
                logger.exception("Error in monitor loop")
                await asyncio.sleep(5)

    async def run(self):
        """Run the monitor."""
        try:
            await self.monitor_loop()
        except KeyboardInterrupt:
            logger.info("Shutting down monitor")
            self.running = False


async def run_monitor():
    """Entry point for the monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )

    db_path = config.get_data_dir() / "flopstar.db"
    logger.info(f"Using database: {db_path}")

    store = MessageStore(db_path)

    async with TechnocoreClient() as client:
        monitor = ContestMonitor(store, client)
        await monitor.run()

    store.close()
