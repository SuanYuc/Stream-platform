from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_REEXEC_GUARD_ENV = "NSY_BROADCASTING_PLATFORM_VENV_REEXEC"
_PROJECT_ROOT = Path(__file__).resolve().parent
_PROJECT_MAIN = _PROJECT_ROOT / "main.py"


def _project_python() -> Path | None:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    python_name = "python.exe" if os.name == "nt" else "python"
    candidate = _PROJECT_ROOT / ".venv" / scripts_dir / python_name
    return candidate if candidate.exists() else None


def _maybe_relaunch_with_project_venv() -> None:
    if getattr(sys, "frozen", False) or os.environ.get(_REEXEC_GUARD_ENV):
        return

    project_python = _project_python()
    if project_python is None:
        return

    try:
        if Path(sys.executable).resolve() == project_python.resolve():
            return
    except OSError:
        pass

    env = {**os.environ, _REEXEC_GUARD_ENV: "1"}
    raise SystemExit(
        subprocess.call([str(project_python), str(_PROJECT_MAIN), *sys.argv[1:]], cwd=_PROJECT_ROOT, env=env)
    )


def main() -> None:
    _maybe_relaunch_with_project_venv()

    from nsy_broadcasting_platform.app import run

    run()


if __name__ == "__main__":
    main()

