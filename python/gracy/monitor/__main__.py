"""``python -m gracy.monitor`` — launch the live terminal dashboard."""

from __future__ import annotations

import sys

from gracy.monitor.viewer import main

if __name__ == "__main__":
    sys.exit(main())
