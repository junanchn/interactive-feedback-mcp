from __future__ import annotations

from datetime import datetime
from pathlib import Path


LOG_FILE_NAME = "feedback_log.txt"


def append_log(config_dir: Path, event: str, content: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    log_path = config_dir / LOG_FILE_NAME
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    normalized = content.replace("\r\n", "\n").strip()
    if not normalized:
        normalized = "<empty>"

    with log_path.open("a", encoding="utf-8") as handle:
        for line in normalized.split("\n"):
            handle.write(f"[{timestamp}] {event}: {line}\n")
