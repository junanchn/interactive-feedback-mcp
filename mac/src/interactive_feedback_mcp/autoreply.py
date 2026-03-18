from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ONESHOT_FILE_NAME = "auto_reply_oneshot.txt"
LOOP_FILE_NAME = "auto_reply_loop.txt"


@dataclass(frozen=True)
class AutoReplyRule:
    kind: str
    timeout_seconds: int
    reply: str
    path: Path
    line_index: int

    @property
    def signature(self) -> tuple[str, int, str, str, int]:
        return (
            self.kind,
            self.timeout_seconds,
            self.reply,
            str(self.path),
            self.line_index,
        )


class AutoReplyManager:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.oneshot_path = config_dir / ONESHOT_FILE_NAME
        self.loop_path = config_dir / LOOP_FILE_NAME
        self._mtimes: dict[Path, float | None] = {}
        self._oneshot_lines: list[str] = []
        self._loop_lines: list[str] = []
        self._oneshot_rules: list[AutoReplyRule] = []
        self._loop_rules: list[AutoReplyRule] = []
        self._loop_index = 0
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        if not force and not self._changed():
            return

        self.config_dir.mkdir(parents=True, exist_ok=True)

        self._oneshot_lines, self._oneshot_rules = self._load_rules(self.oneshot_path, "oneshot")
        self._loop_lines, self._loop_rules = self._load_rules(self.loop_path, "loop")

        if self._loop_rules:
            self._loop_index %= len(self._loop_rules)
        else:
            self._loop_index = 0

        self._mtimes = {
            self.oneshot_path: self._mtime(self.oneshot_path),
            self.loop_path: self._mtime(self.loop_path),
        }

    def current_rule(self) -> AutoReplyRule | None:
        self.reload()
        if self._oneshot_rules:
            return self._oneshot_rules[0]
        if self._loop_rules:
            return self._loop_rules[self._loop_index]
        return None

    def consume(self, rule: AutoReplyRule) -> None:
        self.reload()
        if rule.kind == "oneshot":
            self._consume_oneshot(rule)
            self.reload(force=True)
            return

        if not self._loop_rules:
            return

        try:
            current_index = next(
                index for index, candidate in enumerate(self._loop_rules) if candidate.signature == rule.signature
            )
        except StopIteration:
            current_index = self._loop_index

        self._loop_index = (current_index + 1) % len(self._loop_rules)

    def reset_loop(self) -> None:
        self.reload()
        self._loop_index = 0

    def _consume_oneshot(self, rule: AutoReplyRule) -> None:
        if not self.oneshot_path.exists():
            return

        lines = list(self._oneshot_lines)
        if 0 <= rule.line_index < len(lines):
            del lines[rule.line_index]
        self.oneshot_path.write_text("".join(lines), encoding="utf-8")

    def _changed(self) -> bool:
        for path in (self.oneshot_path, self.loop_path):
            if self._mtimes.get(path) != self._mtime(path):
                return True
        return False

    @staticmethod
    def _mtime(path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return None

    @staticmethod
    def _load_rules(path: Path, kind: str) -> tuple[list[str], list[AutoReplyRule]]:
        if not path.exists():
            return [], []

        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        rules: list[AutoReplyRule] = []

        for line_index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            timeout_text, separator, reply = stripped.partition("|")
            if separator != "|":
                continue

            try:
                timeout_seconds = int(timeout_text.strip())
            except ValueError:
                continue

            rules.append(
                AutoReplyRule(
                    kind=kind,
                    timeout_seconds=max(0, timeout_seconds),
                    reply=reply,
                    path=path,
                    line_index=line_index,
                )
            )

        return lines, rules
