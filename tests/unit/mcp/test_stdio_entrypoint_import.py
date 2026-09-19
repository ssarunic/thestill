"""The stdio entry point must import cleanly in a fresh interpreter.

``thestill.mcp.tools`` imports the rate limiter from ``thestill.web``, whose
package init imports the app and, through it, ``web/mcp_http.py``. When that
module imported ``mcp.tools`` back at module level, ``thestill-mcp`` died on
startup with a circular ImportError — but only when ``mcp.tools`` was the
*first* import, which no in-process test reproduces because pytest has long
since imported ``thestill.web``. Hence the subprocess.
"""

import subprocess
import sys


def test_stdio_server_module_imports_first_in_a_fresh_interpreter():
    proc = subprocess.run(
        [sys.executable, "-c", "import thestill.mcp.server"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
