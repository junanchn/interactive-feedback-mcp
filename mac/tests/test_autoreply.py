from __future__ import annotations

from pathlib import Path

import pytest

from interactive_feedback_mcp.autoreply import (
    LOOP_FILE_NAME,
    ONESHOT_FILE_NAME,
    AutoReplyManager,
)


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "config"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── rule parsing ──────────────────────────────────────────────


class TestLoadRules:
    def test_empty_dir(self, config_dir: Path) -> None:
        mgr = AutoReplyManager(config_dir)
        assert mgr.current_rule() is None

    def test_basic_oneshot(self, config_dir: Path) -> None:
        _write(config_dir / ONESHOT_FILE_NAME, "0|go ahead\n")
        mgr = AutoReplyManager(config_dir)
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.kind == "oneshot"
        assert rule.timeout_seconds == 0
        assert rule.reply == "go ahead"

    def test_basic_loop(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "300|keep going\n")
        mgr = AutoReplyManager(config_dir)
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.kind == "loop"
        assert rule.timeout_seconds == 300
        assert rule.reply == "keep going"

    def test_comments_and_blanks_skipped(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "# comment\n\n60|reply\n")
        mgr = AutoReplyManager(config_dir)
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.reply == "reply"
        assert rule.line_index == 2

    def test_malformed_lines_skipped(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "no_pipe\nabc|bad timeout\n10|ok\n")
        mgr = AutoReplyManager(config_dir)
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.reply == "ok"

    def test_negative_timeout_clamped_to_zero(self, config_dir: Path) -> None:
        _write(config_dir / ONESHOT_FILE_NAME, "-5|clamped\n")
        mgr = AutoReplyManager(config_dir)
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.timeout_seconds == 0

    def test_pipe_in_reply_preserved(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "10|a|b|c\n")
        mgr = AutoReplyManager(config_dir)
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.reply == "a|b|c"


# ── oneshot priority ──────────────────────────────────────────


class TestOneshotPriority:
    def test_oneshot_before_loop(self, config_dir: Path) -> None:
        _write(config_dir / ONESHOT_FILE_NAME, "0|oneshot first\n")
        _write(config_dir / LOOP_FILE_NAME, "60|loop\n")
        mgr = AutoReplyManager(config_dir)
        assert mgr.current_rule().kind == "oneshot"


# ── oneshot consume ───────────────────────────────────────────


class TestOneshotConsume:
    def test_consume_removes_line_from_file(self, config_dir: Path) -> None:
        _write(config_dir / ONESHOT_FILE_NAME, "0|first\n0|second\n")
        mgr = AutoReplyManager(config_dir)

        rule = mgr.current_rule()
        assert rule.reply == "first"
        mgr.consume(rule)

        rule = mgr.current_rule()
        assert rule.reply == "second"
        mgr.consume(rule)

        assert mgr.current_rule() is None
        assert (config_dir / ONESHOT_FILE_NAME).read_text(encoding="utf-8").strip() == ""

    def test_consume_with_comments(self, config_dir: Path) -> None:
        _write(config_dir / ONESHOT_FILE_NAME, "# header\n0|task\n")
        mgr = AutoReplyManager(config_dir)

        rule = mgr.current_rule()
        assert rule.reply == "task"
        mgr.consume(rule)

        remaining = (config_dir / ONESHOT_FILE_NAME).read_text(encoding="utf-8")
        assert "# header" in remaining
        assert "task" not in remaining


# ── loop cycling ──────────────────────────────────────────────


class TestLoopCycle:
    def test_cycle_through_rules(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "60|a\n60|b\n60|c\n")
        mgr = AutoReplyManager(config_dir)

        replies = []
        for _ in range(6):
            rule = mgr.current_rule()
            replies.append(rule.reply)
            mgr.consume(rule)

        assert replies == ["a", "b", "c", "a", "b", "c"]

    def test_reset_loop(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "60|a\n60|b\n")
        mgr = AutoReplyManager(config_dir)

        mgr.consume(mgr.current_rule())  # a → b
        assert mgr.current_rule().reply == "b"

        mgr.reset_loop()
        assert mgr.current_rule().reply == "a"


# ── hot reload ────────────────────────────────────────────────


class TestHotReload:
    def test_file_change_detected(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "60|old\n")
        mgr = AutoReplyManager(config_dir)
        assert mgr.current_rule().reply == "old"

        import time
        time.sleep(0.05)

        _write(config_dir / LOOP_FILE_NAME, "60|new\n")
        mgr.reload()
        assert mgr.current_rule().reply == "new"

    def test_file_deleted(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "60|exists\n")
        mgr = AutoReplyManager(config_dir)
        assert mgr.current_rule() is not None

        (config_dir / LOOP_FILE_NAME).unlink()
        mgr.reload(force=True)
        assert mgr.current_rule() is None

    def test_loop_index_clamped_after_shrink(self, config_dir: Path) -> None:
        _write(config_dir / LOOP_FILE_NAME, "60|a\n60|b\n60|c\n")
        mgr = AutoReplyManager(config_dir)
        mgr.consume(mgr.current_rule())  # → index 1
        mgr.consume(mgr.current_rule())  # → index 2

        import time
        time.sleep(0.05)

        _write(config_dir / LOOP_FILE_NAME, "60|x\n")
        mgr.reload()
        rule = mgr.current_rule()
        assert rule is not None
        assert rule.reply == "x"
