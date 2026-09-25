"""Read-only monitor for technocore Close Call contest."""

import asyncio
import json
import logging

from . import config
from .didkey import verify_signature
from .store import MessageStore
from .technocore import TechnocoreClient

logger = logging.getLogger(__name__)


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
        did = msg.get("did")
        nonce = msg.get("nonce")
        text = msg.get("text")
        sig = msg.get("sig")

        if not all([did, nonce, text, sig]):
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
        """Fill a gap in the sequence using export API."""
        logger.info(f"Filling gap in {room}: {since+1} to {first_new-1}")

        messages = await self.client.export_room(room, since + 1, first_new - 1)

        if messages:
            # Verify referee messages
            verified = []
            for msg in messages:
                if await self.verify_referee_message(room, msg):
                    verified.append(msg)

            self.store.store_messages(room, verified)
            logger.info(f"Filled gap in {room}: {len(verified)} messages")
        else:
            logger.warning(f"Failed to fill gap in {room}")

    async def sync_room(self, room: str):
        """Sync a room from the last known sequence."""
        latest_seq = self.store.get_latest_seq(room)
        since = latest_seq if latest_seq is not None else 0

        logger.debug(f"Syncing {room} since {since}")
        messages = await self.client.get_room(room, since=since)

        if not messages:
            return

        # Check for gaps (server returns newest first with limit)
        if messages:
            first_msg = messages[0]
            first_seq = first_msg.get("seq")

            if first_seq and since + 1 < first_seq:
                # Gap detected
                await self.fill_gap(room, since, first_seq)

        # Verify and store messages
        verified = []
        for msg in messages:
            if await self.verify_referee_message(room, msg):
                verified.append(msg)

        self.store.store_messages(room, verified)

        if verified:
            logger.info(f"Synced {room}: {len(verified)} new messages")

    async def process_price_update(self, msg: dict):
        """Process a new price message and sync other rooms."""
        try:
            data = json.loads(msg.get("text", "{}"))
            sweep_num = data.get("n")

            if sweep_num:
                logger.info(f"New sweep {sweep_num}, syncing other referee rooms")

                # Sync all other referee rooms
                for room in config.REFEREE_ROOMS:
                    if room != config.LONGPOLL_ROOM:
                        await self.sync_room(room)

        except json.JSONDecodeError:
            logger.warning("Failed to parse price message text")

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
                    # Verify and store
                    verified = []
                    for msg in messages:
                        if await self.verify_referee_message(config.LONGPOLL_ROOM, msg):
                            verified.append(msg)
                            # Process each new price update
                            await self.process_price_update(msg)

                    self.store.store_messages(config.LONGPOLL_ROOM, verified)

                    if verified:
                        logger.info(
                            f"{config.LONGPOLL_ROOM}: {len(verified)} new messages"
                        )

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
