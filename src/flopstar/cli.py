"""Command-line interface for Flopstar."""

import asyncio
import sys


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: flopstar <command>")
        print("\nCommands:")
        print("  monitor    Run the read-only contest monitor")
        print("  register   Sign the close-1 owner message (dry run; --post to send it)")
        print("  room       Own room: status|verify|claim|register|allow|heartbeat|reclaim|statement [--post]")
        print("  tree       Key tree: init|dids|dryrun [paths] (never signs or posts)")
        print("  trader     Tree trader: run|status [--live] (paper unless --live); register [--post]")
        print("  verify-key Check the owner key decrypts and matches flopstar.did")
        sys.exit(1)

    command = sys.argv[1]

    if command == "monitor":
        from .monitor import run_monitor
        asyncio.run(run_monitor())
    elif command == "register":
        from .register import run_register
        asyncio.run(run_register(post="--post" in sys.argv[2:]))
    elif command == "room":
        from .room import run_room
        asyncio.run(run_room(sys.argv[2:]))
    elif command == "tree":
        from .treecli import run_tree
        run_tree(sys.argv[2:])
    elif command == "trader":
        from .trader import run_trader
        asyncio.run(run_trader(sys.argv[2:]))
    elif command == "verify-key":
        from .signer import did_from_private_key, expected_did, load_private_key
        did = did_from_private_key(load_private_key())
        print(f"derived:  {did}")
        print(f"expected: {expected_did()}")
        if did != expected_did():
            print("MISMATCH")
            sys.exit(1)
        print("MATCH")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
