from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from interactive_feedback_mcp.models import FeedbackRequest, write_json

STATUS_ACTIVE = "active"
STATUS_TIMED_OUT = "timed_out"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"


def _build_root():
    import tkinter as tk

    dnd_files = None
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore[import-not-found]
    except Exception:
        root = tk.Tk()
    else:
        root = TkinterDnD.Tk()
        dnd_files = DND_FILES

    return root, dnd_files


class FeedbackWindow:
    def __init__(self, state_dir: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.state_dir = state_dir
        self.request = FeedbackRequest(**json.loads((state_dir / "request.json").read_text(encoding="utf-8")))
        self.root, self.dnd_files = _build_root()
        self.tk = tk
        self.ttk = ttk
        self.current_state = STATUS_ACTIVE
        self.status_message = tk.StringVar(value="AI is waiting for your feedback.")
        self._status_mtime: float | None = None

        self.root.title(self.request.title)
        self.root.geometry("760x520")
        self.root.minsize(620, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="AI Summary").pack(anchor="w")
        self.summary_box = tk.Text(frame, wrap="word", height=10)
        self.summary_box.pack(fill="x", pady=(6, 12))
        self.summary_box.insert("1.0", self.request.summary)
        self.summary_box.configure(state="disabled")

        ttk.Label(frame, text="Your Feedback").pack(anchor="w")
        self.input_box = tk.Text(frame, wrap="word", height=12, undo=True)
        self.input_box.pack(fill="both", expand=True, pady=(6, 8))
        if self.request.default:
            self.input_box.insert("1.0", self.request.default)

        ttk.Label(frame, textvariable=self.status_message).pack(anchor="w", pady=(0, 8))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x")

        self.submit_button = ttk.Button(button_row, text="Continue", command=self.submit)
        self.cancel_button = ttk.Button(button_row, text="Close", command=self.close)
        self.cancel_button.pack(side="right")
        self.submit_button.pack(side="right", padx=(0, 8))

        self._bind_shortcuts()
        self._bind_drag_and_drop()
        self._center_window()
        self.input_box.focus_set()
        self._poll_status()

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def submit(self) -> None:
        feedback = self._feedback_text()
        if self.current_state != STATUS_ACTIVE:
            self.status_message.set("This request is no longer active. The server is no longer listening.")
            self.root.bell()
            return

        self._write_result(feedback)
        self._write_status(STATUS_COMPLETED)
        self.root.destroy()

    def close(self) -> None:
        if self.current_state == STATUS_ACTIVE:
            self._write_result("")
            self._write_status(STATUS_COMPLETED)
        self.root.destroy()

    def _feedback_text(self) -> str:
        return self.input_box.get("1.0", "end-1c").strip()

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.bind("<Command-Return>", lambda _event: self.submit())
        self.root.bind("<Control-Return>", lambda _event: self.submit())
        self.input_box.bind("<Command-v>", self._maybe_paste_paths)
        self.input_box.bind("<Command-V>", self._maybe_paste_paths)
        self.input_box.bind("<Control-v>", self._maybe_paste_paths)
        self.input_box.bind("<Control-V>", self._maybe_paste_paths)

    def _bind_drag_and_drop(self) -> None:
        if not self.dnd_files:
            return

        self.input_box.drop_target_register(self.dnd_files)
        self.input_box.dnd_bind("<<Drop>>", self._handle_drop)

    def _handle_drop(self, event) -> str:
        paths = [Path(item).expanduser().resolve() for item in self.root.tk.splitlist(event.data)]
        self._insert_paths(paths)
        return "break"

    def _maybe_paste_paths(self, _event):
        try:
            clipboard = self.root.clipboard_get()
        except self.tk.TclError:
            return None

        paths = _extract_file_paths(clipboard)
        if not paths:
            return None

        self._insert_paths(paths)
        return "break"

    def _insert_paths(self, paths: list[Path]) -> None:
        text = "\n".join(str(path) for path in paths)
        if self.input_box.index("insert") != "1.0" and not self._feedback_text().endswith("\n"):
            text = "\n" + text
        self.input_box.insert("insert", text)

    def _poll_status(self) -> None:
        status_path = self.state_dir / "status.json"
        try:
            stat = status_path.stat()
        except FileNotFoundError:
            self.root.after(250, self._poll_status)
            return

        if self._status_mtime != stat.st_mtime:
            self._status_mtime = stat.st_mtime
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self._apply_status(str(payload.get("state", STATUS_ACTIVE)))

        self.root.after(250, self._poll_status)

    def _apply_status(self, state: str) -> None:
        if state == self.current_state:
            return

        self.current_state = state
        if state == STATUS_TIMED_OUT:
            self.root.title(f"[Timed Out] {self.request.title}")
            self.status_message.set("The auto-reply timer fired. Your text was kept, but this request is closed.")
        elif state == STATUS_CANCELLED:
            self.root.title(f"[Cancelled] {self.request.title}")
            self.status_message.set("Cursor cancelled this request. Your text was kept, but this request is closed.")
        elif state == STATUS_COMPLETED:
            self.root.destroy()
            return
        else:
            self.root.title(self.request.title)
            self.status_message.set("AI is waiting for your feedback.")
            return

        if not self._feedback_text():
            self.root.destroy()

    def _write_result(self, feedback: str) -> None:
        write_json(self.state_dir / "result.json", {"feedback": feedback})

    def _write_status(self, state: str) -> None:
        write_json(self.state_dir / "status.json", {"state": state})

    def _center_window(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 3, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")


def _extract_file_paths(raw_text: str) -> list[Path]:
    results: list[Path] = []
    for chunk in raw_text.splitlines():
        item = chunk.strip()
        if not item:
            continue

        if item.startswith("file://"):
            parsed = urlparse(item)
            candidate = Path(unquote(parsed.path)).expanduser()
        else:
            candidate = Path(item).expanduser()

        if candidate.exists():
            results.append(candidate.resolve())

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    window = FeedbackWindow(Path(args.state_dir))
    return window.run()


if __name__ == "__main__":
    raise SystemExit(main())
