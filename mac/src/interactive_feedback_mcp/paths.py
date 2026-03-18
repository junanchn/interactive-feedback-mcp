from __future__ import annotations

import os
import sys
from pathlib import Path


ENV_HOME = "INTERACTIVE_FEEDBACK_HOME"


def resolve_config_dir(project_directory: str = "") -> Path:
    env_home = os.environ.get(ENV_HOME, "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()

    if project_directory.strip():
        return Path(project_directory).expanduser().resolve()

    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.exists():
        return argv0.resolve().parent

    return Path.cwd().resolve()
