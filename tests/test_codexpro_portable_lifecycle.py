from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_portable_lifecycle_is_exact_inverse(tmp_path: Path) -> None:
    module = load("portable_lifecycle_test", ROOT / "bin" / "codexpro_lifecycle.py")
    codex_home = tmp_path / "codex"
    prior = codex_home / "bin" / "chatgpt_oracle_state.py"
    prior.parent.mkdir(parents=True)
    prior.write_bytes(b"user-owned-before\n")

    plan = module.install(ROOT, codex_home, dry_run=True)
    assert plan["ok"] and "bin/codexpro_harness.py" in plan["files"]
    assert not (codex_home / "receipts").exists()

    installed = module.install(ROOT, codex_home)
    assert installed["ok"] and installed["count"] > 120
    receipt = Path(installed["receipt"])
    missing_policy = module.doctor(codex_home)
    assert missing_policy["status"] == "FAIL"
    assert any(item["code"] == "HOST_POLICY_REQUIRED" for item in missing_policy["issues"])
    profile_seed = tmp_path / "oracle-profile-seed"
    profile_seed.mkdir()
    policy_path = codex_home / "state" / "chatgpt-oracle" / "host-policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.oracle-host-policy/v1",
                "profile_seed": str(profile_seed.resolve()),
                "profile_mode": "copy-per-run",
                "max_total_concurrency": 5,
            }
        ),
        encoding="utf-8",
    )
    doctor = module.doctor(codex_home)
    assert doctor["status"] == "PASS"
    assert doctor["oracle_host_policy"]["max_total_concurrency"] == 5

    rolled_back = module.rollback(codex_home, receipt)
    assert rolled_back == {"ok": True, "status": "COMPLETE", "receipt": str(receipt), "conflicts": []}
    assert prior.read_bytes() == b"user-owned-before\n"
    assert not (codex_home / "bin" / "codexpro_harness.py").exists()


def test_portable_rollback_preserves_modified_managed_file(tmp_path: Path) -> None:
    module = load("portable_lifecycle_conflict_test", ROOT / "bin" / "codexpro_lifecycle.py")
    codex_home = tmp_path / "codex"
    installed = module.install(ROOT, codex_home)
    managed = codex_home / "bin" / "codexpro_harness.py"
    managed.write_text("user changed\n", encoding="utf-8")

    result = module.rollback(codex_home, Path(installed["receipt"]))

    assert result["ok"] is False
    assert any(item["path"] == "bin/codexpro_harness.py" for item in result["conflicts"])
    assert managed.read_text(encoding="utf-8") == "user changed\n"


def test_portable_receipt_rejects_external_backup(tmp_path: Path) -> None:
    module = load("portable_lifecycle_forgery_test", ROOT / "bin" / "codexpro_lifecycle.py")
    codex_home = tmp_path / "codex"
    receipt = codex_home / "receipts" / "codexpro-automation-forged.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"schema": module.RECEIPT_SCHEMA, "backup": str(tmp_path / "outside"), "files": []}), encoding="utf-8")

    try:
        module.rollback(codex_home, receipt)
    except module.LifecycleError as exc:
        assert "backup must be owned" in str(exc)
    else:
        raise AssertionError("forged receipt was accepted")
