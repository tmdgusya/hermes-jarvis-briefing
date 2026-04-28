"""File-based status bridge for the Jarvis demo overlay."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATUS_DIR = Path.home() / ".hermes" / "jarvis-overlay"
STATUS_FILE = STATUS_DIR / "status.json"

STATE_LABELS = {
    "on": "JARVIS ON",
    "listening": "듣는 중",
    "generating": "생성 중",
    "speaking": "읽어드리는 중",
}


def write_status(state: str) -> Path:
    """Persist the latest demo-overlay state to disk.

    Raises:
        ValueError: if ``state`` is not one of the supported overlay states.
    """
    if state not in STATE_LABELS:
        raise ValueError(f"unsupported overlay state: {state}")

    status_dir = STATUS_FILE.parent
    status_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "label": STATE_LABELS[state],
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    tmp_path = STATUS_FILE.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(STATUS_FILE)
    return STATUS_FILE
