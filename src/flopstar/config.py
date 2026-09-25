"""Configuration for Flopstar agent."""

import os
from pathlib import Path

# Referee trust anchor (PROVISIONAL - no signed close-1 launch record yet)
# From FLOP Labs' signed sonnet-2 launch record
REFEREE_DID = "did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte"

# Contest configuration
SEASON_ID = "close-1"

# Referee rooms (read-only)
REFEREE_ROOMS = [
    "d-close1-price",   # Reference price, limits, global price, S
    "d-close1-flow",    # Mints, rooms, trade outcomes (counts only)
    "d-close1-positions",  # Open interest and positions
    "d-close1-pnl",     # PnL and live board
    "d-close1-state",   # State root
]

# Primary long-poll room (only one to avoid 4 concurrent limit)
LONGPOLL_ROOM = "d-close1-price"

# Trading rooms (for reading signed trades)
TRADING_ROOMS = ["close1"]

# Our own d- room: only the owner and allow-listed keys can post in it
OWN_ROOM = "d-flopstar-close1"

# Paths
def get_key_path() -> Path:
    """Get the path to the private key (NOT used in monitor mode)."""
    path_str = os.environ.get("FLOPSTAR_KEY_PATH", "~/.config/flopstar/flopstar.pem")
    return Path(path_str).expanduser()

def get_data_dir() -> Path:
    """Get the data directory for the SQLite store."""
    path_str = os.environ.get("FLOPSTAR_DATA_DIR", "./data")
    path = Path(path_str).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path
