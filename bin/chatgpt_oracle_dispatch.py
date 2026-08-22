from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable

BIN = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PROFILES = _load("oracle_dispatch_profiles", BIN / "chatgpt_oracle_profiles.py")
RUNNER = _load("oracle_dispatch_runner", BIN / "chatgpt_oracle_run.py")
CAPABILITY = _load("oracle_dispatch_capability", BIN / "chatgpt_capability_runtime.py")


def compile_manifest(
    *,
    mode: str,
    project_root: Path,
    mission_path: Path | None,
    output_path: Path,
    reasoning_level: str | None = None,
    attachment_paths: Iterable[Path] | None = None,
    app_name: str | None = None,
    mission_authority_path: Path | None = None,
) -> dict[str, Any]:
    contract = PROFILES.build_launch_contract(
        mode,
        mission_path=mission_path,
        reasoning_level=reasoning_level,
        attachment_paths=list(attachment_paths or ()),
        app_name=app_name,
    )
    result = {"ok": True, "contract": contract, "oracle_manifest_path": None}
    if not contract["oracle_launch"]:
        return result
    root = project_root.expanduser().resolve(strict=True)
    target = output_path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": RUNNER.STATE.SCHEMA,
        "project_root": str(root),
        "mission_path": contract["mission_path"],
        "mode": "browser",
        "task_kind": contract["task_kind"],
        "transport": {
            "oracle-pro-attachment-only": "pro-attachment-only",
            "oracle-pro-devspace": "pro-devspace",
            "oracle-pro-devspace-readonly": "pro-devspace-readonly",
            "oracle-devspace": "devspace",
        }[contract["route"]],
        "model": contract.get("model") or "gpt-5.6",
        "model_strategy": "select",
        "thinking_time": contract["thinking_time"],
        "research": "deep" if contract["research"] else "off",
        "archive": "auto",
    }
    if contract["route"] == "oracle-pro-attachment-only":
        manifest["attachments"] = contract["attachments"]
    else:
        manifest["app_name"] = contract["app_name"]
        manifest["task_outcome_contract"] = "v1"
        manifest["capability_required"] = True
        if contract["route"] == "oracle-pro-devspace":
            if mission_authority_path is None:
                raise ValueError(  # noqa: GENERIC_ERR_OK - invalid caller input is a value contract
                    "Pro DevSpace requires an explicit mission authority"
                )
            authority = mission_authority_path.expanduser().resolve(strict=True)
            if authority.is_symlink() or not authority.is_file():
                raise ValueError(  # noqa: GENERIC_ERR_OK - invalid caller input is a value contract
                    "mission authority must be an exact regular file"
                )
            manifest["capability_kind"] = "pro-bounded-write"
            manifest["capability_authority_path"] = str(authority)
        else:
            manifest["capability_kind"] = "read-only"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["oracle_manifest_path"] = str(target)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a GPT mode and dispatch it to Oracle + DevSpace.")
    parser.add_argument("--mode", required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mission-path", type=Path)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--reasoning-level")
    parser.add_argument("--attachment", type=Path, action="append", default=[])
    parser.add_argument("--app-name")
    parser.add_argument("--mission-authority", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        compiled = compile_manifest(
            mode=args.mode,
            project_root=args.project_root,
            mission_path=args.mission_path,
            output_path=args.manifest_output,
            reasoning_level=args.reasoning_level,
            attachment_paths=args.attachment,
            app_name=args.app_name,
            mission_authority_path=args.mission_authority,
        )
        if compiled["oracle_manifest_path"]:
            manifest_path = Path(compiled["oracle_manifest_path"])
            manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_value.get("capability_kind") == "pro-bounded-write":
                session = CAPABILITY.open_pro(
                    args.project_root,
                    args.mission_path,
                    args.mission_authority,
                    dry_run=args.dry_run,
                )
            elif manifest_value.get("capability_kind") == "read-only":
                session = CAPABILITY.open_read_only(
                    args.project_root,
                    args.mission_path,
                    dry_run=args.dry_run,
                )
            else:
                session = None
            run = RUNNER.execute_run(
                manifest_path,
                dry_run=args.dry_run,
                capability_token=session.token if session else None,
            )
            capability_receipt = None
            if session is not None:
                run_state = run.get("result") if isinstance(run.get("result"), dict) else {}
                capability_receipt = CAPABILITY.finish(
                    session,
                    terminal_harvested=run_state.get("terminal_harvested") is True,
                    safe_pre_submit=bool(run.get("safe_for_fresh_run")),
                )
            value = {
                **compiled,
                "run": run,
                "capability_receipt": capability_receipt,
                "ok": bool(run.get("ok")),
            }
        else:
            value = compiled
    except Exception as exc:
        value = {"ok": False, "error": {"code": "ORACLE_DISPATCH_FAILED", "message": str(exc)}}
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
