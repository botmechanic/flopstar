"""Command-line interface for Flopstar."""

import asyncio
import sys


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: flopstar <command>")
        print("\nCommands:")
        print("  monitor    Run the read-only contest monitor")
        sys.exit(1)

    command = sys.argv[1]

    if command == "monitor":
        from .monitor import run_monitor
        asyncio.run(run_monitor())
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
