from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class FeedbackRequest:
    summary: str
    title: str = "Interactive Feedback"
    default: str = ""
    timeout_seconds: int = 600
    project_directory: str = ""
    multiline: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class FeedbackResult:
    feedback: str

    @classmethod
    def from_path(cls, path: Path) -> "FeedbackResult":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(feedback=str(data.get("feedback", "")))


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
