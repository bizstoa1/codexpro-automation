from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bin.chatgpt_oracle_run as RUNNER
import bin.chatgpt_oracle_state as STATE


SCHEMA: Final = "codex.chatgpt.oracle-archived-exact-settlement/v1"
RECEIPT_SCHEMA: Final = "codex.chatgpt.oracle-archived-exact-settlement-receipt/v1"
CONFIRMATION_TOKEN: Final = "user-confirmed-archived-exact-transcript-settlement"
SETTLEMENT_NAME: Final = "archived-exact-transcript-settlement.json"
ZERO_ACTION_COUNTERS: Final = {
    "oracle_preview": 0,
    "oracle_submission": 0,
    "oracle_recovery": 0,
    "oracle_canary": 0,
    "conversation_creation": 0,
    "chrome_launch": 0,
    "seed_profile_mutation": 0,
    "product_file_mutation": 0,
    "play_cloud_github_product_work": 0,
}
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class SettlementError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class Binding:
    exact_run_id: str
    session_locator: str
    project_root: Path
    run_dir: Path
    state_sha256: str
    transcript_path: Path
    transcript_sha256: str
    recovery_transcript_sha256: str
    final_gate_output_path: Path
    final_gate_output_sha256: str
    pass_stage_receipt_path: Path
    pass_stage_receipt_sha256: str
    prior_runtime_release_receipt_path: Path
    prior_runtime_release_receipt_sha256: str


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: JsonValue, label: str) -> str:
    normalized = str(value or "").strip().casefold()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise SettlementError("MANIFEST_INVALID", f"{label} must be a lowercase SHA-256")
    return normalized


def absolute(value: JsonValue | Path, label: str, *, exists: bool = True) -> Path:
    raw = Path(str(value or "")).expanduser()
    if not raw.is_absolute():
        raise SettlementError("MANIFEST_INVALID", f"{label} must be absolute")
    try:
        return raw.resolve(strict=exists)
    except OSError as exc:
        raise SettlementError("PATH_INVALID", f"{label} could not be resolved: {raw}") from exc


def _regular(value: JsonValue, label: str, root: Path) -> Path:
    raw = Path(str(value or "")).expanduser()
    if raw.is_symlink():
        raise SettlementError("REGULAR_FILE_REQUIRED", f"{label} must not be a symlink")
    path = absolute(raw, label)
    if not path.is_file() or not STATE.is_within(root, path):
        raise SettlementError("PROJECT_CONTAINMENT_REQUIRED", f"{label} must be a regular contained file")
    return path


def read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettlementError("JSON_INVALID", f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SettlementError("JSON_INVALID", f"{label} must be a JSON object")
    return value


def load_binding(manifest_path: Path) -> Binding:
    raw_manifest = manifest_path.expanduser()
    if raw_manifest.is_symlink():
        raise SettlementError("REGULAR_FILE_REQUIRED", "manifest must not be a symlink")
    path = absolute(raw_manifest, "manifest_path")
    value = read_json(path, "manifest")
    if value.get("schema") != SCHEMA:
        raise SettlementError("MANIFEST_INVALID", f"manifest schema must be {SCHEMA}")
    project = absolute(value.get("project_root"), "project_root")
    if project.is_symlink() or not project.is_dir() or not STATE.is_within(project, path):
        raise SettlementError("PROJECT_CONTAINMENT_REQUIRED", "manifest must be inside the exact project")
    session_locator = str(value.get("session_locator") or "").strip()
    transcript_path = Path(str(value.get("transcript_path") or "")).expanduser()
    if not transcript_path.is_absolute():
        raise SettlementError("MANIFEST_INVALID", "transcript_path must be absolute")
    if transcript_path.is_symlink() or not transcript_path.is_file():
        raise SettlementError("REGULAR_FILE_REQUIRED", "archived transcript must be a regular non-symlink file")
    return Binding(
        exact_run_id=str(value.get("exact_run_id") or "").strip(),
        session_locator=session_locator,
        project_root=project,
        run_dir=absolute(value.get("run_dir"), "run_dir"),
        state_sha256=_hash(value.get("state_sha256"), "state_sha256"),
        transcript_path=transcript_path,
        transcript_sha256=_hash(value.get("transcript_sha256"), "transcript_sha256"),
        recovery_transcript_sha256=_hash(
            value.get("recovery_transcript_sha256"), "recovery_transcript_sha256"
        ),
        final_gate_output_path=_regular(value.get("final_gate_output_path"), "final_gate_output", project),
        final_gate_output_sha256=_hash(value.get("final_gate_output_sha256"), "final_gate_output_sha256"),
        pass_stage_receipt_path=_regular(value.get("pass_stage_receipt_path"), "pass_stage_receipt", project),
        pass_stage_receipt_sha256=_hash(value.get("pass_stage_receipt_sha256"), "pass_stage_receipt_sha256"),
        prior_runtime_release_receipt_path=absolute(
            value.get("prior_runtime_release_receipt_path"), "prior_runtime_release_receipt_path"
        ),
        prior_runtime_release_receipt_sha256=_hash(
            value.get("prior_runtime_release_receipt_sha256"), "prior_runtime_release_receipt_sha256"
        ),
    )


def _require_hash(path: Path, expected: str) -> None:
    if path.is_symlink() or not path.is_file() or sha(path) != expected:
        raise SettlementError("HASH_MISMATCH", f"hash-bound evidence changed: {path}")


def validate(
    binding: Binding,
    process_alive: Callable[[int], bool],
) -> tuple[JsonObject, tuple[int, ...], bytes, Path]:
    state_root = STATE.oracle_state_root()
    if (
        binding.run_dir.is_symlink()
        or not binding.run_dir.is_dir()
        or not STATE.is_within(state_root, binding.run_dir)
        or binding.run_dir.name != binding.exact_run_id
    ):
        raise SettlementError("RUN_ID_MISMATCH", "run directory is not the exact host-state run")
    state_path = binding.run_dir / "state.json"
    _require_hash(state_path, binding.state_sha256)
    state: JsonObject = STATE.load_state(state_path)
    artifact_value = state.get("artifacts")
    artifacts: JsonObject = artifact_value if isinstance(artifact_value, dict) else {}
    output_path = absolute(artifacts.get("output"), "state output", exists=False)
    if output_path.exists() or output_path.is_symlink():
        raise SettlementError("OUTPUT_MUST_BE_ABSENT", "exact run output already exists")
    settlement_path = binding.run_dir / SETTLEMENT_NAME
    if settlement_path.exists() or settlement_path.is_symlink():
        raise SettlementError("SETTLEMENT_MUST_BE_ABSENT", "settlement receipt already exists")
    if state.get("run_id") != binding.exact_run_id:
        raise SettlementError("RUN_ID_MISMATCH", "state run_id changed")
    oracle_value = state.get("oracle")
    oracle: JsonObject = oracle_value if isinstance(oracle_value, dict) else {}
    if oracle.get("slug") != binding.session_locator or oracle.get("session_locator") != binding.session_locator:
        raise SettlementError("LOCATOR_MISMATCH", "state exact slug/session locator changed")
    expected_transcript = (
        Path.home() / ".oracle" / "sessions" / binding.session_locator / "artifacts" / "transcript.md"
    )
    if (
        not binding.session_locator
        or Path(binding.session_locator).name != binding.session_locator
        or binding.transcript_path != expected_transcript
    ):
        raise SettlementError(
            "ARCHIVED_TRANSCRIPT_PATH_INVALID",
            "approved transcript must be the exact archived session transcript",
        )
    if (
        state.get("project_root") != str(binding.project_root)
        or str(state.get("status") or "") != "attention_required"
        or str(state.get("session_authority") or "") != "submitted_unknown"
        or state.get("terminal_harvested") is not False
        or str(state.get("task_outcome") or "") != "pending"
        or output_path != binding.run_dir / "output.md"
    ):
        raise SettlementError("STATE_ENTRY_INVALID", "state no longer matches the approved unsettled entry")
    recovery_transcript_path = _regular(
        artifacts.get("transcript"), "state recovery transcript", binding.run_dir
    )
    _require_hash(recovery_transcript_path, binding.recovery_transcript_sha256)
    release_root = absolute(os.environ.get("CODEX_HOME") or Path.home() / ".codex", "CODEX_HOME") / "receipts"
    for evidence, expected in (
        (binding.transcript_path, binding.transcript_sha256),
        (binding.final_gate_output_path, binding.final_gate_output_sha256),
        (binding.pass_stage_receipt_path, binding.pass_stage_receipt_sha256),
        (binding.prior_runtime_release_receipt_path, binding.prior_runtime_release_receipt_sha256),
    ):
        _require_hash(evidence, expected)
    if not STATE.is_within(release_root, binding.prior_runtime_release_receipt_path):
        raise SettlementError("RELEASE_RECEIPT_INVALID", "prior release receipt must be under CODEX_HOME/receipts")
    if read_json(binding.prior_runtime_release_receipt_path, "prior release receipt").get("schema") != "codexpro.install-release-receipt/v1":
        raise SettlementError("RELEASE_RECEIPT_INVALID", "prior runtime release receipt schema is invalid")
    final_bytes = binding.final_gate_output_path.read_bytes()
    transcript_bytes = binding.transcript_path.read_bytes()
    try:
        final_lines = final_bytes.decode("utf-8", errors="strict").strip().splitlines()
        transcript_lines = transcript_bytes.decode("utf-8", errors="strict").strip().splitlines()
    except UnicodeDecodeError as exc:
        raise SettlementError("TRANSCRIPT_ANSWER_INVALID", "final output and transcript must be UTF-8") from exc
    if (
        not final_lines
        or not transcript_lines
        or final_lines[-1].strip() != "TASK_OUTCOME: EXECUTED"
        or transcript_lines[-1].strip() != "TASK_OUTCOME: EXECUTED"
    ):
        raise SettlementError("TRANSCRIPT_ANSWER_INVALID", "final output and transcript must report execution")
    report_fields: dict[str, list[str]] = {}
    for raw_line in transcript_lines[:-1]:
        line = raw_line.strip()
        if line.startswith(("- ", "* ", "+ ")):
            line = line[2:].strip()
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized_key = "_".join(key.strip(" `*_").casefold().replace("-", " ").split())
        report_fields.setdefault(normalized_key, []).append(value.strip(" `*_"))
    required_report = (
        (("output", "output_path", "final_output", "final_output_path", "final_gate_output", "final_gate_output_path"), str(binding.final_gate_output_path)),
        (("output_sha256", "output_sha_256", "final_output_sha256", "final_output_sha_256", "final_gate_output_sha256", "final_gate_output_sha_256"), binding.final_gate_output_sha256),
        (("receipt", "receipt_path", "stage_receipt", "stage_receipt_path", "pass_receipt", "pass_receipt_path", "pass_stage_receipt", "pass_stage_receipt_path"), str(binding.pass_stage_receipt_path)),
        (("receipt_sha256", "receipt_sha_256", "stage_receipt_sha256", "stage_receipt_sha_256", "pass_receipt_sha256", "pass_receipt_sha_256", "pass_stage_receipt_sha256", "pass_stage_receipt_sha_256"), binding.pass_stage_receipt_sha256),
        (("status",), "PASS"),
        (("next_stage",), "complete"),
        (("ready_for_next",), "true"),
    )
    if any(
        [value for key in keys for value in report_fields.get(key, ())] != [expected]
        for keys, expected in required_report
    ):
        raise SettlementError("TRANSCRIPT_ANSWER_INVALID", "transcript lacks exact settlement report bindings")
    receipt = read_json(binding.pass_stage_receipt_path, "PASS stage receipt")
    receipt_output = absolute(receipt.get("output_path"), "PASS receipt output")
    if (
        receipt.get("schema") != "codex.chatgpt.oracle-stage-result/v1"
        or receipt.get("stage") != "final-web-gate"
        or receipt.get("status") != "PASS"
        or receipt.get("next_stage") != "complete"
        or receipt.get("ready_for_next") is not True
        or receipt.get("blocker")
        or receipt_output != binding.final_gate_output_path
        or receipt.get("output_sha256") != binding.final_gate_output_sha256
    ):
        raise SettlementError("PASS_RECEIPT_INVALID", "stage receipt is not PASS/complete/ready_for_next")
    pids = RUNNER.run_owned_process_ids(binding.run_dir, state)
    if any(process_alive(pid) for pid in pids):
        raise SettlementError("ACTIVE_PROCESS", "an exact-run Oracle or recovery process is still active")
    return state, pids, final_bytes, recovery_transcript_path
