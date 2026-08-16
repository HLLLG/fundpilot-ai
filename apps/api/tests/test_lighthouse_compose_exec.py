"""Lighthouse remote-exec helper must be valid bash on the server.

Deploy to Lighthouse #136 failed immediately after rsync:

    bash: line 48: syntax error near unexpected token `('

The start script is sent as a quoted heredoc (``<<'REMOTE'``), which already
prevents GitHub-runner expansion. Escaping ``$(seq …)`` anyway made the server
see ``\\$(seq``, which bash parses as an unexpected ``(``.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

HELPER = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "run-lighthouse-compose-exec.sh"


def _start_remote_body() -> str:
    text = HELPER.read_text(encoding="utf-8")
    return text.split("bash -s <<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]


def test_start_script_does_not_escape_command_substitution_after_inner_heredoc() -> None:
    after_run = _start_remote_body().split("\nRUN\n", 1)[1]
    code_lines = [
        line
        for line in after_run.splitlines()
        if line.lstrip() and not line.lstrip().startswith("#")
    ]
    joined = "\n".join(code_lines)
    assert r"\$(seq" not in joined
    assert r"\$(tr" not in joined
    assert "for i in $(seq 1 10); do" in joined


def test_start_script_parses_as_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not on PATH")
    body = _start_remote_body()
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(body)
        path = handle.name
    try:
        result = subprocess.run(
            [bash, "-n", path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
