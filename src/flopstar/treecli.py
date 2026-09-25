"""`flopstar tree ...`: master seed, DIDs and the dry run. Nothing here signs or posts."""

import os

from . import config
from .tree import TREE_SIZE, get_seed_path, load_seed, tree_dids


def init_seed() -> None:
    path = get_seed_path()
    if path.exists():
        raise SystemExit(f"{path} already exists; refusing to overwrite the master seed")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(os.urandom(32).hex() + "\n")
    print(f"created {path} (0600). Back it up offline: it recreates all {TREE_SIZE} tree keys.")
    write_dids()


def write_dids() -> None:
    dids = tree_dids(load_seed())
    out = config.get_data_dir() / "tree-dids.txt"
    out.write_text("".join(f"{i} {d}\n" for i, d in enumerate(dids)))
    print(f"{len(dids)} tree DIDs (public) written to {out}")


def run_tree(args: list[str]) -> None:
    command = args[0] if args else ""
    if command == "init":
        init_seed()
    elif command == "dids":
        write_dids()
    elif command == "dryrun":
        from .dryrun import run_dryrun
        paths = int(args[1]) if len(args) > 1 else 200
        run_dryrun(paths=paths)
    else:
        raise SystemExit("usage: flopstar tree [init|dids|dryrun [paths]]")
