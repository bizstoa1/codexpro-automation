# 초절약모드

초절약모드는 로컬 Codex 비용을 최소화하면서 설계·구현·검토 품질을 웹
ChatGPT 단계에 맡기는 선택형 종합 워크플로입니다. 일반 전역 설정을 바꾸지
않으며, 사용자가 해당 작업의 모델을 직접 `gpt-5.6-luna` / `max`로 선택한
경우에만 시작합니다.

## 활성화

새 작업을 Luna, Max로 선택한 뒤 다음처럼 요청합니다.

```text
$ultra-economy-mode 초절약모드로 이 작업을 끝내줘.
```

스킬은 현재 작업 런타임의 모델과 추론 수준을 확인합니다. 현재 값이 다르거나
런타임에서 관찰되지 않으면 아무 세션도 만들지 않고 모델 변경을 요청합니다.
`~/.codex/config.toml`은 다음 작업의 기본값일 수 있을 뿐 현재 작업 선택의
증거가 아니므로 활성화 판정에 사용하지 않습니다.

## 작업 분담

```text
로컬 Luna Max 지휘관
  ├─ exact-root 최초 자격 확인
  ├─ 최소 미션·영수증·해시·상태 관리
  └─ 최종 결정론적 명령 1회

웹 세션
  ├─ Pro: 읽기 전용 설계
  ├─ regular: 별도 설계 검토 + 구현 미션 작성
  ├─ regular: 코드 구현 + 프로젝트 테스트
  └─ regular: 별도 최종 검증, 필요하면 다음 구현 세션으로 수리 반송
```

로컬에서 의미 판단이 꼭 필요하면 지휘관이 직접 긴 맥락을 읽지 않고, 새
`default` 서브에이전트를 `gpt-5.6-luna` / `max`로 한 명씩 실행합니다. 전달
내용은 목표, exact 파일 경로, 현재 영수증, 권한, 성공 조건으로 제한합니다.
전역 scout/implementer/verifier 역할은 다른 모델 계약일 수 있으므로 이 모드에서
사용하지 않습니다.

## Manifest

기존 Oracle comprehensive manifest에 다음 필드를 추가합니다.

```json
{
  "schema": "codex.chatgpt.oracle-comprehensive/v1",
  "workflow_id": "stable-hex-or-uuid",
  "workflow_profile": "ultra-economy",
  "initial_stage": "pro",
  "project_root": "D:\\project",
  "workflow_dir": "D:\\project\\.workflow\\ultra-economy",
  "initial_mission_path": "D:\\project\\missions\\design.md",
  "app_name": "codex",
  "model": "gpt-5.6",
  "max_stages": 8,
  "local_gate_command": ["python", "-m", "pytest", "-q"]
}
```

실행기는 현재 `CODEX_THREAD_ID`와 일치하는 Codex rollout의 최신
`turn_context`에서 모델과 추론 수준을 직접 읽습니다. manifest 자기선언,
환경변수만의 모델 주장, `config.toml`, 프롬프트, 자식 에이전트 보고는 증거로
인정하지 않습니다. exact 런타임 증거를 찾지 못하면 제출 전에 중단합니다.

먼저 dry-run을 실행합니다.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_comprehensive.py" `
  --manifest D:\project\workflow.json --dry-run
```

dry-run은 첫 단계가 qualified Pro, read-only DevSpace인지 확인하고 실제 제출은
하지 않습니다. 실제 실행은 `--dry-run`만 제거합니다.

## 완료·중단 조건

- 새 프로젝트의 exact root 자격 확인은 첫 질문 전에 한 번 수행하며, DevSpace
  config hash가 같으면 후속 단계마다 반복하지 않습니다.
- Pro는 설계 전용입니다. 구현과 프로젝트 테스트는 별도 regular 웹 세션이
  수행하고, 또 다른 regular 웹 세션이 최종 검증합니다.
- 불명확한 제출 실패는 exact-session 복구만 허용합니다. 새 제출로 대체하지
  않습니다.
- 최종 웹 PASS 영수증과 로컬 결정론적 gate의 exit code 0이 모두 있어야
  완료입니다. 로컬 Luna의 주관적 판단만으로 완료하지 않습니다.
