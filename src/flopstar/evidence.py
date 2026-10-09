"""Keep our own signed records: room rings forget within minutes in busy rooms."""

import json

from .technocore import TechnocoreClient


async def save_record(
    client: TechnocoreClient, room: str, did: str, nonce: int, path: str
) -> dict | None:
    """Find our record by (from, nonce) in the room export and append its exact line to path."""
    for line in (await client.export_lines(room)):
        if did in line:
            record = json.loads(line)
            if record.get("from") == did and str(record.get("nonce")) == str(nonce):
                _append(path, line)
                return record
    return None


def _append(path: str, line: str) -> None:
    with open(path, "a") as f:
        f.write(line + "\n")
