"""Shared runtime helpers used by every entry-point script."""
from __future__ import annotations

import os
import subprocess
import sys


def auto_update() -> None:
    """Best-effort: ask update.sh to fast-forward this skill from origin/main.

    Honours the 24-hour cache inside update.sh; the script returns instantly
    when no update is needed. Any failure (no git, offline, divergent local
    history) is swallowed — we never block the customer's command on this.

    Disabled when PALO_ALTO_SKIP_AUTO_UPDATE=1 (useful for tests / CI).
    """
    if os.environ.get("PALO_ALTO_SKIP_AUTO_UPDATE") == "1":
        return
    here = os.path.dirname(os.path.realpath(__file__))
    script = os.path.join(here, "update.sh")
    if not os.path.exists(script):
        return
    try:
        subprocess.run(
            ["bash", script],
            check=False,
            timeout=15,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            # update.sh writes its "skill updated X -> Y" status line to stderr;
            # let it through so the customer sees the version bump.
            stderr=sys.stderr,
        )
    except Exception:
        pass
