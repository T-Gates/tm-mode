# teammode 자율 빌드 로그

> dev-cycle (구현 → 적대적 검수 → 반영 루프). 그린필드 기준선 = 0 tests.
> 각 슬라이스: TDD(분석→RED→구현→GREEN) 구현 서브에이전트 → 별도 적대적 검수 서브에이전트 → "수정할 내역 없음"까지 루프.

## 환경 결정

- **언어/테스트**: Python 3.13 + pytest 9. `pyproject.toml`에 `[tool.pytest.ini_options]` (testpaths=tests). 근거: 스펙 §11.5 "Python-first", 훅이 이미 Python 의존이라 추가 의존성 0.
- **pytest 격리**: 시스템에 pytest 부재 → 레포 루트 `.venv/`에 설치(.gitignore 등재됨). 테스트 실행은 `.venv/bin/python -m pytest`.
- **실환경 오염 금지**: 모든 어댑터 테스트는 tmp_path 픽스처로 settings.json/config.toml을 가짜 경로에 둔다. `~/.claude/settings.json` 등 실파일 무접촉.

---

## 슬라이스 1 — 검수 도구 우선 (골든 시나리오 + 러너)

### 결정/근거
- **시나리오 = 선언적 JSON** (`conformance/scenarios/*.json`). 각 시나리오는 `steps[]`, 각 step은 `action`(command/fs_write/noop) + `expect[]`(assertion 배열, AND). 근거: §11.12 "시나리오 = 실행 가능한 스펙" — 데이터로 두면 verify·conform이 한 정의를 공유.
- **엔진 하니스 인터페이스**: `engine.run(argv) -> Result(exit_code, stdout, stderr)` + root 아래 파일 부작용. 스펙 03 §2 "C2 주의"(독립 구현에 파일배치·언어 비강제)와 정합 — `SubprocessEngine`이 임의 `--engine` prefix를 받아 어떤 언어 구현도 검사 가능.
- **noop step의 stdout/exit_code 단언은 직전 command 결과를 상속**. 02·05 시나리오가 "동작 1회 → 결과를 여러 각도로 단언" 패턴이라 필요. (`test_noop_step_inherits_last_command_output`로 고정)
- **Tier 산출(§11.11)**: 결정적 시나리오 전부 통과해야 compliant. advisory 순응률(advisory 시나리오 통과 비율)로 Tier 1(100%)/2(부분)/3(0%) 등급. 04-log-accumulate만 `advisory`(세션로그=§11.11 advisory enforcement 예시), 나머지 4개 deterministic.
- **assertion kind**: exit_code, stdout/stderr_contains, file_exists/contains, session_log_single_file(스펙 01 §3.1 하루1파일·분할금지), session_log_contains, state_off/on(.acme-active 마커). 미지의 kind는 크래시 대신 fail 처리(`test_unknown_assertion_kind_is_failure_not_crash`).
- **lint(정적)**: v0.1은 manifest 정규형(mcp__/Write|Edit/apply_patch 직표기 금지, K4) 1항목으로 시작. 슬라이스 2에서 manifest 생기면 실효 검사. 빈 레포에선 "manifest 없음 — 건너뜀"으로 PASS(크래시 금지).

### 라운드 1 (구현 → 적대적 검수)
- RED: `tests/test_check.py` 작성 → `ModuleNotFoundError: check` 확인.
- 구현: `conformance/check.py` (파싱/실행/Tier/3모드 디스패치) → 17 테스트 GREEN.
- **검수 지적 1건(테스트 결함)**: `test_file_exists_and_file_contains`가 같은 tmp_path를 양성/음성 케이스에 재사용 → 첫 케이스의 side_effect(배너 파일)가 음성 케이스에 잔존해 오탐. **구현 버그 아님**(파일 검사 로직은 정확). 음성 케이스를 `tmp_path/clean` 별도 루트로 격리 수정.
- 재검수: 구현 로직 결함 0건. "수정할 내역 없음".

### 1.4 빈 엔진 verify = 전부 RED (인수 테스트 박힘) — 실행 증거
no-op 엔진(`sys.exit(127)`)에 `verify` 실행 결과:
```
[FAIL] 01-on-banner / 02-context-injection / 03-issue-create / 05-off-persist (deterministic)
[FAIL] 04-log-accumulate (advisory)
RED: 0/5 통과   (exit 1)
```
`conform` 모드 → "비호환: 결정적 시나리오 실패" (Tier 미산정, 의도대로).
`lint`(빈 레포) → manifest 없음 건너뜀 PASS.

### 결과
- 신규 테스트 17개 전부 green (기준선 0 → 17). 슬라이스 1 검수 통과.
