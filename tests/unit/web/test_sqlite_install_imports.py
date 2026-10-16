"""Spec #44 contract: a SQLite-only install (no ``postgres`` extra) never
imports psycopg. A fresh interpreter, because other tests in the same
process may already have imported it."""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_web_app_imports_without_psycopg():
    code = (
        "import sys\n"
        "sys.modules['psycopg'] = None  # makes `import psycopg` raise ImportError\n"
        "import thestill.web.app\n"
        "import thestill.services.listening_progress_service\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
