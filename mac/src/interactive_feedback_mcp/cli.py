from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from interactive_feedback_mcp.models import FeedbackRequest, FeedbackResult, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the interactive feedback GUI and print feedback to stdout.")
    parser.add_argument("summary", help="Summary shown in the feedback window.")
    parser.add_argument("timeout_seconds", nargs="?", default="600", help="Timeout value shown to the user.")
    args = parser.parse_args()

    try:
        timeout_seconds = max(0, int(args.timeout_seconds))
    except ValueError as exc:
        raise SystemExit(f"Invalid timeout: {args.timeout_seconds}") from exc

    request = FeedbackRequest(summary=args.summary, timeout_seconds=timeout_seconds)
    state_dir = Path(tempfile.mkdtemp(prefix="interactive-feedback-cli-"))
    try:
        write_json(state_dir / "request.json", request.__dict__)
        write_json(state_dir / "status.json", {"state": "active"})
        completed = subprocess.run(
            [sys.executable, "-m", "interactive_feedback_mcp.ui", "--state-dir", str(state_dir)],
            check=True,
            capture_output=True,
            text=True,
        )

        if completed.stderr.strip():
            raise RuntimeError(completed.stderr.strip())

        result_path = state_dir / "result.json"
        if not result_path.exists():
            return 0

        result = FeedbackResult.from_path(result_path)
        sys.stdout.write(result.feedback)
        return 0
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
