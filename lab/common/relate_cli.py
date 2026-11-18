"""The `relate` command line, always with DO_NOT_TRACK=1, and the credentials file pull and push read."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from lab.settings import Settings, get_settings


class RelateError(RuntimeError):
    def __init__(self, args: list[str], result: CliResult):
        self.result = result
        super().__init__(f"relate {' '.join(args[:2])} failed ({result.returncode}): {result.error_line()}")


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    json: dict | list | None = field(default=None)

    def error_line(self) -> str:
        for line in (self.stderr + "\n" + self.stdout).splitlines():
            if line.startswith("Error:"):
                return line
        # Relate 1.1.0 ends an error with a "hint:" line about its feedback channel; the reason is the line before it
        tail = [ln for ln in (self.stderr or self.stdout).strip().splitlines() if not ln.startswith("hint:")]
        return tail[-1] if tail else ""


def relate_bin(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    found = shutil.which(s.relate_bin)
    if not found:
        raise FileNotFoundError(
            f"relate binary {s.relate_bin!r} not found. Run `uv run python tools/install_relate.py`, "
            "or set LAB_RELATE_BIN."
        )
    return found


def relate_env() -> dict[str, str]:
    env = dict(os.environ)
    env["DO_NOT_TRACK"] = "1"
    return env


def relate_cli(args: list[str], *, check: bool = True, timeout: float | None = 3600,
               settings: Settings | None = None) -> CliResult:
    """Run `relate <args>`. If stdout is one JSON value it is parsed into `.json`."""
    proc = subprocess.run(
        [relate_bin(settings), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=relate_env(), timeout=timeout,
    )
    parsed = None
    out = proc.stdout.strip()
    if out.startswith(("{", "[")):
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            parsed = None
    result = CliResult(proc.returncode, proc.stdout, proc.stderr, parsed)
    if check and proc.returncode != 0:
        raise RelateError(args, result)
    return result


@contextmanager
def credentials_file(settings: Settings | None = None):
    """A temporary env file for `relate pull --from` and `relate push --to`, deleted afterwards.

    Relate reads NEO4J_URI as the http(s) Query API address, so it gets the settings' query_uri (decision 25).
    """
    s = settings or get_settings()
    fd, name = tempfile.mkstemp(prefix="relate-creds-", suffix=".env")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"NEO4J_URI={s.query_uri}\n")
            f.write(f"NEO4J_USERNAME={s.neo4j_username}\n")
            f.write(f"NEO4J_PASSWORD={s.neo4j_password}\n")
            f.write(f"NEO4J_DATABASE={s.neo4j_database}\n")
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def plaintext_flag(settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    return ["--allow-plaintext"] if s.plaintext else []
