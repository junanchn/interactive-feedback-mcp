from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from interactive_feedback_mcp.models import FeedbackRequest, write_json

_UI_CONFIG_DIR = Path.home() / ".config" / "interactive-feedback-mcp"
_UI_CONFIG_FILE = "ui_prefs.json"


def _config_path_for(project_directory: str) -> Path:
    key = project_directory.strip() or "default"
    slug = hashlib.md5(key.encode()).hexdigest()[:10]
    return _UI_CONFIG_DIR / slug / _UI_CONFIG_FILE


def _load_ui_config(project_directory: str) -> dict:
    path = _config_path_for(project_directory)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_ui_config(project_directory: str, config: dict) -> None:
    path = _config_path_for(project_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

STATUS_ACTIVE = "active"
STATUS_TIMED_OUT = "timed_out"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"

_DARK = {
    "bg": "#353535",
    "fg": "#ffffff",
    "input_bg": "#2a2a2a",
    "input_fg": "#ffffff",
    "disabled_fg": "#7f7f7f",
    "accent": "#2a82da",
    "button_bg": "#454545",
    "button_active": "#555555",
    "border": "#232323",
    "status_fg": "#cccccc",
    "insert": "#ffffff",
}


def _apply_dark_theme(root, tk, ttk):
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(".", background=_DARK["bg"], foreground=_DARK["fg"],
                     bordercolor=_DARK["border"], troughcolor=_DARK["input_bg"],
                     fieldbackground=_DARK["input_bg"], insertcolor=_DARK["insert"])
    style.configure("TFrame", background=_DARK["bg"])
    style.configure("TLabel", background=_DARK["bg"], foreground=_DARK["fg"])
    style.configure("Status.TLabel", background=_DARK["bg"], foreground=_DARK["status_fg"])
    style.configure("TButton", background=_DARK["button_bg"], foreground=_DARK["fg"],
                     borderwidth=1, focuscolor=_DARK["accent"])
    style.map("TButton",
              background=[("active", _DARK["button_active"]), ("pressed", _DARK["accent"])],
              foreground=[("disabled", _DARK["disabled_fg"])])
    style.configure("Accent.TButton", background=_DARK["accent"], foreground="#ffffff")
    style.map("Accent.TButton",
              background=[("active", "#3292ea"), ("pressed", "#1a72ca")])
    style.configure("TLabelframe", background=_DARK["bg"], foreground=_DARK["fg"],
                     bordercolor=_DARK["border"])
    style.configure("TLabelframe.Label", background=_DARK["bg"], foreground=_DARK["fg"])

    root.configure(bg=_DARK["bg"])


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

        _apply_dark_theme(self.root, tk, ttk)

        self.root.title(self.request.title)
        self.root.geometry("760x520")
        self.root.minsize(620, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self._process: subprocess.Popen | None = None
        self._log_buffer: list[str] = []
        self._ui_config = _load_ui_config(self.request.project_directory)

        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        _text_opts = dict(
            bg=_DARK["input_bg"], fg=_DARK["input_fg"],
            insertbackground=_DARK["insert"],
            selectbackground=_DARK["accent"], selectforeground="#ffffff",
            relief="flat", borderwidth=1, highlightthickness=1,
            highlightbackground=_DARK["border"], highlightcolor=_DARK["accent"],
        )
        self._text_opts = _text_opts

        self._cmd_visible = tk.BooleanVar(value=False)
        self.toggle_cmd_button = ttk.Button(
            frame, text="▶ Show Command Section",
            command=self._toggle_command_section,
        )
        self.toggle_cmd_button.pack(anchor="w", pady=(0, 8))

        self.cmd_frame = ttk.LabelFrame(frame, text="Command", padding=8)

        project_dir = self.request.project_directory or os.getcwd()
        ttk.Label(self.cmd_frame, text=f"Working directory: {project_dir}",
                  style="Status.TLabel").pack(anchor="w", pady=(0, 4))

        cmd_input_row = ttk.Frame(self.cmd_frame)
        cmd_input_row.pack(fill="x", pady=(0, 4))

        self.cmd_entry = tk.Entry(cmd_input_row, **{
            k: v for k, v in _text_opts.items() if k != "highlightthickness"
        }, highlightthickness=1, font=("Menlo", 12))
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.cmd_entry.bind("<Return>", lambda _e: self._run_command())

        self.run_button = ttk.Button(cmd_input_row, text="Run", command=self._run_command)
        self.run_button.pack(side="right")

        ttk.Label(self.cmd_frame, text="Console Output").pack(anchor="w", pady=(4, 2))
        self.console_box = tk.Text(self.cmd_frame, wrap="word", height=10,
                                    state="disabled", font=("Menlo", 11), **_text_opts)
        self.console_box.pack(fill="both", expand=True, pady=(0, 4))

        console_btn_row = ttk.Frame(self.cmd_frame)
        console_btn_row.pack(fill="x")
        ttk.Button(console_btn_row, text="Clear", command=self._clear_console).pack(side="right")

        saved_cmd = self._ui_config.get("command", "")
        if saved_cmd:
            self.cmd_entry.insert(0, saved_cmd)
        if self._ui_config.get("command_visible", False):
            self._toggle_command_section()

        ttk.Label(frame, text="AI Summary").pack(anchor="w")
        self.summary_box = tk.Text(frame, wrap="word", height=10, **_text_opts)
        self.summary_box.pack(fill="x", pady=(6, 12))
        self.summary_box.insert("1.0", self.request.summary)
        self.summary_box.configure(state="disabled",
                                   fg=_DARK["disabled_fg"], bg=_DARK["bg"])

        ttk.Label(frame, text="Your Feedback").pack(anchor="w")
        self.input_box = tk.Text(frame, wrap="word", height=12, undo=True, **_text_opts)
        self.input_box.pack(fill="both", expand=True, pady=(6, 8))
        if self.request.default:
            self.input_box.insert("1.0", self.request.default)

        ttk.Label(frame, textvariable=self.status_message,
                  style="Status.TLabel").pack(anchor="w", pady=(0, 8))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x")

        self.submit_button = ttk.Button(button_row, text="Continue (⌘↵)",
                                         command=self.submit, style="Accent.TButton")
        self.cancel_button = ttk.Button(button_row, text="Close (Esc)", command=self.close)
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

        logs = "".join(self._log_buffer).strip()
        if logs and feedback:
            combined = f"{feedback}\n\n--- Command Output ---\n{logs}"
        elif logs:
            combined = f"--- Command Output ---\n{logs}"
        else:
            combined = feedback

        self._kill_process()
        self._persist_ui_config()
        self._write_result(combined)
        self._write_status(STATUS_COMPLETED)
        self.root.destroy()

    def close(self) -> None:
        self._kill_process()
        self._persist_ui_config()
        if self.current_state == STATUS_ACTIVE:
            self._write_result("")
            self._write_status(STATUS_COMPLETED)
        self.root.destroy()

    def _persist_ui_config(self) -> None:
        config = {
            "command": self.cmd_entry.get().strip(),
            "command_visible": self._cmd_visible.get(),
        }
        try:
            _save_ui_config(self.request.project_directory, config)
        except Exception:
            pass

    def _toggle_command_section(self) -> None:
        visible = not self._cmd_visible.get()
        self._cmd_visible.set(visible)
        if visible:
            self.cmd_frame.pack(fill="both", expand=False, pady=(0, 8),
                                after=self.toggle_cmd_button)
            self.toggle_cmd_button.configure(text="▼ Hide Command Section")
            self.root.geometry("760x750")
        else:
            self.cmd_frame.pack_forget()
            self.toggle_cmd_button.configure(text="▶ Show Command Section")
            self.root.geometry("760x520")

    def _run_command(self) -> None:
        if self._process is not None:
            self._kill_process()
            self.run_button.configure(text="Run")
            return

        command = self.cmd_entry.get().strip()
        if not command:
            self._append_console("Please enter a command to run\n")
            return

        self._log_buffer = []
        self._append_console(f"$ {command}\n")
        self.run_button.configure(text="Stop")

        project_dir = self.request.project_directory or os.getcwd()
        try:
            self._process = subprocess.Popen(
                command,
                shell=True,
                cwd=project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            self._append_console(f"Error: {exc}\n")
            self.run_button.configure(text="Run")
            return

        def _read_pipe(pipe):
            for line in iter(pipe.readline, ""):
                self.root.after(0, self._append_console, line)
            pipe.close()

        threading.Thread(target=_read_pipe, args=(self._process.stdout,), daemon=True).start()
        threading.Thread(target=_read_pipe, args=(self._process.stderr,), daemon=True).start()
        self._poll_process()

    def _poll_process(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is not None:
            exit_code = self._process.returncode
            self._append_console(f"\nProcess exited with code {exit_code}\n")
            self._process = None
            self.run_button.configure(text="Run")
            return
        self.root.after(200, self._poll_process)

    def _kill_process(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=2)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        self._process = None

    def _append_console(self, text: str) -> None:
        self._log_buffer.append(text)
        self.console_box.configure(state="normal")
        self.console_box.insert("end", text)
        self.console_box.see("end")
        self.console_box.configure(state="disabled")

    def _clear_console(self) -> None:
        self._log_buffer = []
        self.console_box.configure(state="normal")
        self.console_box.delete("1.0", "end")
        self.console_box.configure(state="disabled")

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
