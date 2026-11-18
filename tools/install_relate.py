"""Download the Relate CLI for this platform, check it against the release's SHA256SUMS, and put it on the venv's
path. `uv run python tools/install_relate.py`. The Dockerfile does the same for Linux.
"""
from __future__ import annotations

import hashlib
import os
import platform
import stat
import sys
import sysconfig
import tempfile
import urllib.request
from pathlib import Path

VERSION = "1.1.0"
BASE = f"https://github.com/relatedb/relate/releases/download/v{VERSION}"


def asset_name() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    if sys.platform.startswith("win"):
        return "relate-windows-amd64.exe"
    if sys.platform == "darwin":
        return f"relate-darwin-{arch}"
    return f"relate-linux-{arch}"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (fixed https release URL)
        return r.read()


def main() -> int:
    name = asset_name()
    target_dir = Path(sysconfig.get_path("scripts"))
    target = target_dir / ("relate.exe" if name.endswith(".exe") else "relate")
    sums = fetch(f"{BASE}/SHA256SUMS").decode()
    expected = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(name)), None)
    if not expected:
        print(f"{name} is not listed in SHA256SUMS for v{VERSION}", file=sys.stderr)
        return 1
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        print(f"relate {VERSION} already installed at {target}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:  # an empty folder for the download
        blob = fetch(f"{BASE}/{name}")
        got = hashlib.sha256(blob).hexdigest()
        if got != expected:
            print(f"checksum mismatch for {name}: {got} != {expected}", file=sys.stderr)
            return 1
        staged = Path(tmp) / target.name
        staged.write_bytes(blob)
        staged.chmod(staged.stat().st_mode | stat.S_IEXEC)
        os.replace(staged, target)
    print(f"installed relate {VERSION} at {target} (sha256 {expected[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
