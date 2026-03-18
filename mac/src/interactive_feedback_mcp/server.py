from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from interactive_feedback_mcp.autoreply import AutoReplyManager
from interactive_feedback_mcp.logging_utils import append_log
from interactive_feedback_mcp.models import FeedbackRequest, FeedbackResult, write_json
from interactive_feedback_mcp.paths import resolve_config_dir
from interactive_feedback_mcp.ui import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_TIMED_OUT

mcp = FastMCP(
    name="interactive-feedback-mcp-mac",
    instructions=(
        "MANDATORY: You MUST call the interactive_feedback tool at the END of EVERY response, "
        "with NO exceptions. NEVER end your turn by simply outputting text — always finish by "
        "calling interactive_feedback to collect the user's next instruction via the popup window. "
        "This replaces the normal chat input flow entirely."
    ),
)

_MANAGERS: dict[Path, AutoReplyManager] = {}
_LOCKS: dict[Path, asyncio.Lock] = {}


def _manager_for(config_dir: Path) -> AutoReplyManager:
    manager = _MANAGERS.get(config_dir)
    if manager is None:
        manager = AutoReplyManager(config_dir)
        _MANAGERS[config_dir] = manager
    return manager


def _lock_for(config_dir: Path) -> asyncio.Lock:
    lock = _LOCKS.get(config_dir)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[config_dir] = lock
    return lock


def _write_status(state_dir: Path, state: str) -> None:
    write_json(state_dir / "status.json", {"state": state})


async def _cleanup_session(proc: asyncio.subprocess.Process, state_dir: Path) -> None:
    try:
        await proc.wait()
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def _ui_closed_without_result(proc: asyncio.subprocess.Process, state_dir: Path) -> bool:
    return proc.returncode is not None and not (state_dir / "result.json").exists()


def _result_if_ready(state_dir: Path) -> FeedbackResult | None:
    result_path = state_dir / "result.json"
    if not result_path.exists():
        return None
    return FeedbackResult.from_path(result_path)


def _request_from_args(
    summary: str,
    message: str,
    title: str,
    default: str,
    timeout_seconds: int,
    project_directory: str,
    multiline: bool,
) -> FeedbackRequest:
    body = summary.strip() or message.strip()
    return FeedbackRequest(
        summary=body,
        title=title,
        default=default,
        timeout_seconds=max(0, timeout_seconds),
        project_directory=project_directory.strip(),
        multiline=multiline,
    )


async def _run_feedback_request(request: FeedbackRequest, config_dir: Path, ctx: Context | None) -> str:
    manager = _manager_for(config_dir)
    append_log(config_dir, "AI_REQUEST", request.summary)

    immediate_rule = manager.current_rule()
    if immediate_rule and immediate_rule.timeout_seconds == 0:
        manager.consume(immediate_rule)
        append_log(config_dir, f"AUTO_REPLY_{immediate_rule.kind.upper()}", immediate_rule.reply)
        return immediate_rule.reply

    state_dir = Path(tempfile.mkdtemp(prefix="interactive-feedback-mcp-"))
    write_json(state_dir / "request.json", request.__dict__)
    _write_status(state_dir, STATUS_ACTIVE)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "interactive_feedback_mcp.ui",
        "--state-dir",
        str(state_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    active_rule = manager.current_rule()
    deadline = time.monotonic() + active_rule.timeout_seconds if active_rule and active_rule.timeout_seconds > 0 else None
    last_progress_at = 0.0

    try:
        while True:
            result = _result_if_ready(state_dir)
            if result is not None:
                _write_status(state_dir, STATUS_COMPLETED)
                await proc.wait()
                shutil.rmtree(state_dir, ignore_errors=True)
                append_log(config_dir, "USER_FEEDBACK", result.feedback)
                if result.feedback:
                    manager.reset_loop()
                return result.feedback

            manager.reload()
            refreshed_rule = manager.current_rule()
            if (refreshed_rule.signature if refreshed_rule else None) != (active_rule.signature if active_rule else None):
                active_rule = refreshed_rule
                if active_rule and active_rule.timeout_seconds == 0:
                    manager.consume(active_rule)
                    _write_status(state_dir, STATUS_TIMED_OUT)
                    append_log(config_dir, f"AUTO_REPLY_{active_rule.kind.upper()}", active_rule.reply)
                    asyncio.create_task(_cleanup_session(proc, state_dir))
                    return active_rule.reply
                deadline = (
                    time.monotonic() + active_rule.timeout_seconds
                    if active_rule and active_rule.timeout_seconds > 0
                    else None
                )

            if active_rule and deadline is not None and time.monotonic() >= deadline:
                manager.consume(active_rule)
                _write_status(state_dir, STATUS_TIMED_OUT)
                append_log(config_dir, f"AUTO_REPLY_{active_rule.kind.upper()}", active_rule.reply)
                asyncio.create_task(_cleanup_session(proc, state_dir))
                return active_rule.reply

            if _ui_closed_without_result(proc, state_dir):
                stdout, stderr = await proc.communicate()
                shutil.rmtree(state_dir, ignore_errors=True)
                details = (stderr or stdout).decode().strip()
                if details:
                    raise RuntimeError(f"Feedback UI exited unexpectedly: {details}")
                append_log(config_dir, "USER_FEEDBACK", "")
                return ""

            if ctx is not None and time.monotonic() - last_progress_at >= 5:
                last_progress_at = time.monotonic()
                await ctx.report_progress(progress=0.0, total=1.0, message="Waiting for user feedback")

            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        _write_status(state_dir, STATUS_CANCELLED)
        append_log(config_dir, "REQUEST_CANCELLED", request.summary)
        asyncio.create_task(_cleanup_session(proc, state_dir))
        raise
    except Exception:
        _write_status(state_dir, STATUS_CANCELLED)
        asyncio.create_task(_cleanup_session(proc, state_dir))
        raise


@mcp.tool()
async def interactive_feedback(
    summary: str = "",
    project_directory: str = "",
    timeout_seconds: int = 600,
    title: str = "Interactive Feedback",
    default: str = "",
    multiline: bool = True,
    message: str = "",
    ctx: Context | None = None,
) -> str:
    """Pause execution and ask the macOS user for feedback in a popup window."""

    request = _request_from_args(
        summary=summary,
        message=message,
        title=title,
        default=default,
        timeout_seconds=timeout_seconds,
        project_directory=project_directory,
        multiline=multiline,
    )
    config_dir = resolve_config_dir(request.project_directory)

    async with _lock_for(config_dir):
        return await _run_feedback_request(request, config_dir, ctx)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
