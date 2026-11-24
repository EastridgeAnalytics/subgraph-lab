"""`tools/install_relate.py`: the download lands beside its destination and is renamed into place, so the move never
crosses a drive, and a failed check or move leaves nothing behind. No network: `fetch` is patched."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools import install_relate

BLOB = b"#!/bin/sh\necho relate\n"


def serve(monkeypatch, blob: bytes = BLOB, listed: bytes | None = None) -> None:
    """`fetch` answers SHA256SUMS with the hash of `listed` (default: `blob`) for this platform's asset, and the asset
    with `blob`."""
    name = install_relate.asset_name()
    sums = f"{hashlib.sha256(listed if listed is not None else blob).hexdigest()}  {name}\n".encode()
    monkeypatch.setattr(install_relate, "fetch", lambda url: sums if url.endswith("SHA256SUMS") else blob)


def target_in(folder: Path) -> Path:
    return folder / ("relate.exe" if install_relate.asset_name().endswith(".exe") else "relate")


def test_the_download_is_staged_beside_the_target_and_renamed_into_place(tmp_path, monkeypatch):
    moves: list[tuple[Path, Path]] = []
    real_replace = install_relate.os.replace

    def replace(src, dst):
        moves.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(install_relate.os, "replace", replace)
    target = tmp_path / "bin" / "relate"
    target.parent.mkdir()
    install_relate.place(BLOB, target)
    assert target.read_bytes() == BLOB
    assert [(src.parent, dst) for src, dst in moves] == [(target.parent, target)]  # one rename, in the same folder
    assert sorted(p.name for p in target.parent.iterdir()) == ["relate"]


def test_a_move_that_fails_leaves_nothing_behind(tmp_path, monkeypatch):
    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(install_relate.os, "replace", locked)
    with pytest.raises(PermissionError):
        install_relate.place(BLOB, tmp_path / "relate")
    assert list(tmp_path.iterdir()) == []


def test_main_installs_into_the_scripts_folder(tmp_path, monkeypatch, capsys):
    serve(monkeypatch)
    monkeypatch.setattr(install_relate, "scripts_dir", lambda: tmp_path)
    assert install_relate.main() == 0
    assert target_in(tmp_path).read_bytes() == BLOB
    assert [p.name for p in tmp_path.iterdir()] == [target_in(tmp_path).name]
    assert install_relate.main() == 0 and "already installed" in capsys.readouterr().out


def test_main_writes_nothing_when_the_checksum_is_wrong(tmp_path, monkeypatch, capsys):
    serve(monkeypatch, listed=b"something else")
    monkeypatch.setattr(install_relate, "scripts_dir", lambda: tmp_path)
    assert install_relate.main() == 1
    assert "checksum mismatch" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_main_says_so_when_relate_is_running(tmp_path, monkeypatch, capsys):
    serve(monkeypatch)
    monkeypatch.setattr(install_relate, "scripts_dir", lambda: tmp_path)

    def locked(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(install_relate.os, "replace", locked)
    assert install_relate.main() == 1
    assert "stop any running relate" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
