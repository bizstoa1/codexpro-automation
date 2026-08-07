from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_compat.py"


def load_compat():
    name = "chatgpt_oracle_compat_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_exact_version_patch_is_hash_gated_idempotent_and_backed_up(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {"sample.txt": {"patch": "sample.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    compat.patch_root = lambda: patches
    backup = tmp_path / "backup"

    first = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)
    second = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)

    assert first["changed"] == ["sample.txt"]
    assert second["already_patched"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_hash_specific_legacy_patch_migrates_without_backup(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"middle\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    (patches / "legacy.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+middle\n",
        encoding="utf-8",
    )
    legacy_hash = digest(b"middle\n")
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
            "legacy_patched": [legacy_hash],
            "legacy_patches": {legacy_hash: "legacy.patch"},
        }
    }
    compat.patch_root = lambda: patches
    backup = tmp_path / "backup"

    result = compat.ensure_oracle_compatibility(
        "oracle 0.17.1",
        package_root=package,
        backup_root=backup,
    )

    assert result["changed"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_unknown_oracle_version_or_file_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    with pytest.raises(compat.OracleCompatError) as version:
        compat.ensure_oracle_compatibility("oracle 0.17.0", package_root=tmp_path)
    assert version.value.code == "ORACLE_VERSION_UNVALIDATED"

    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"unknown\n")
    compat.PATCHES = {"sample.txt": {"patch": "missing.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    with pytest.raises(compat.OracleCompatError) as mismatch:
        compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package)
    assert mismatch.value.code == "ORACLE_FILE_HASH_MISMATCH"


def test_published_0171_patch_requires_extra_high_and_pro_selection_proof(tmp_path: Path) -> None:
    compat = load_compat()
    source = (
        Path.home()
        / "AppData"
        / "Local"
        / "npm-cache"
        / "_npx"
        / "0a10f56e3ba43148"
        / "node_modules"
        / "@steipete"
        / "oracle"
    )
    if not source.is_dir():
        pytest.skip("published Oracle 0.17.1 cache is unavailable")
    package = tmp_path / "oracle"
    shutil.copytree(source, package)
    backup = tmp_path / "backup"

    result = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)
    assert result["changed"] == ["dist/src/browser/actions/thinkingTime.js"]
    target = package / "dist/src/browser/actions/thinkingTime.js"
    source_text = target.read_text(encoding="utf-8")
    assert "strictGpt56Effort" in source_text
    assert 'level === "extra-high" || level === "heavy"' in source_text
    assert "strictRequestedEffort" in source_text
    assert "composer-model-picker-slider-simple-view" in source_text
    assert "label: 'Power ' + current + ' of 5'" in source_text
    assert "`Power ${current} of 5`" not in source_text
    assert "targetPower: POWER_TARGET" in source_text
    node = shutil.which("node")
    assert node is not None, "Node.js is required to validate the patched Oracle source"
    syntax = subprocess.run(
        [node, "--check", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert compat.sha256_file(target) == compat.PATCHES["dist/src/browser/actions/thinkingTime.js"]["patched"]
    assert compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)["already_patched"]
