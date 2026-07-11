"""PyInstaller entry point for the standalone `gracy` binary."""
import sys

from gracy.cli import main

if __name__ == "__main__":
    sys.exit(main())
