# ARCHITECTURE — 기여자용 지도

> 코드를 고치러 온 사람이 10분 안에 "어디를 보면 되는지" 알게 하는 문서.
> 동작 명세(계약)는 [docs/spec/](docs/spec/README.md)이 단일 권위이고, 내부 구현 상세는 [docs/spec/internals.md](docs/spec/internals.md)에 있다. 이 문서는 그 진입 지도다.

## 큰 그림 — 3계층

```
┌─ 런처 (src/teammode/cli.py · install.sh) ─────────────────┐
│  pip/curl 로 배포되는 얇은 진입점. 레포 생성·clone·wizard.    │
│  clone 이후는 아무 일도 안 함 — 설치·동작은 전부 레포 안 엔진. │
└──────────────────────┬────────────────────────────────────┘
                       ▼ clone
┌─ 팀 레포 (template 복사본 = 팀 인스턴스) ───────────────────┐
│  infra/   ← 제품 코드(엔진·훅·어댑터·스킬). update 로 동기화  │
│  memory/  ← 팀 데이터. upstream 이 절대 건드리지 않음         │
│  tests/ conformance/ ← 검증층. update 가 파일단위 동기화(v2) │
└───────────────────────────────────────────────────────────┘
```

- **upstream(T-Gates/tm-mode) = 오픈소스 제품**, **팀 인스턴스 = template 복사본 + 팀 데이터**.
- 인스턴스는 `tm-mode update`(엔진 동사)로 upstream 의 `infra/`·`NOTICE.md` 를 받아오고, 검증층(`tests/`·`conformance/`)은 로컬 수정 보존 판정(blob-history) 하에 파일 단위로 따라간다. `memory/`·`team.config.json` 은 어떤 경로로도 동기화되지 않는다.

## 컴포넌트 지도

| 경로 | 정체 | 고칠 때 같이 볼 것 |
|---|---|---|
| `src/teammode/cli.py` | **런처.** stdlib 단일 파일(curl 로 받아 단독 실행됨 — 패키지 import 금지) | `install.sh`, `tests/test_cli_join_wizard.py` |
| `infra/teammode.py` | **엔진.** 동사(verb) 디스패처 — on/off/log/context/pull/commit/update/issue/memory/util | [docs/spec/](docs/spec/README.md) §3 동사 계약 |
| `infra/install.py` + `install_lib.py` | **부트스트랩.** 훅 배선·스킬 배포·env 주입. `--dry-run`/`--yes` 게이트 | `tests/test_install_*.py`, golden 시나리오 |
| `infra/git_ops.py` | **git 공통.** fetch/pull/commit/push + 동기화 판정(validation plan/apply) — 전부 무raise·타임아웃·killpg | `tests/test_git_ops.py`, `test_validation_sync.py` |
| `infra/agents/<name>/` | **어댑터.** 에이전트별 설정 파일 렌더(Claude settings.json · Codex config.toml). 공통 훅을 각 에이전트 이벤트에 배선 | `infra/hooks/manifest.json`(훅 선언 단일 소스), `events.json` |
| `infra/hooks/` | **공통 훅.** session-start(맥락 주입)·auto-commit·push-worker(비동기 push)·kb-write-guard(메모리 쓰기 거버넌스) 등. 에이전트 무관 — 정규화된 stdin 계약 | `manifest.json`, `tests/test_kb_write_guard.py` 등 훅별 테스트 |
| `infra/skills/` | **스킬.** base(양 에이전트 공통 배포)·core(tm-onboard·tm-connect·tm-memory…)·util(인스턴스 이식용). **엔진=기계, 스킬=판단** — 판단이 필요한 일은 스킬 문서가, 기계적 실행은 엔진 동사가 담당 | `docs/spec/skills.md` |
| `conformance/check.py` | **호환 검사.** 인스턴스가 스펙 계약을 지키는지 기계 검증 | golden 시나리오 |
| `providers/*.json` | **L2 provider 팩.** 서비스 연결(issues/chat/docs/calendar 슬롯)의 발급 안내·MCP 실행정보 데이터 | `infra/skills/core/tm-connect/` |

## 세션 한 사이클 (데이터 흐름)

```
에이전트 세션 시작
  └ session-start 훅: 팀 원격(origin) 정합(세션당 1회) + 팀원별 최근 세션로그
    + memory INDEX + 가이드라인 주입
  └ (tm on 시) auto_update_on_start: upstream 엔진/검증층 뒤처짐 감지·알림
    — 적용은 tm-mode update 에서만
작업 중
  └ user-prompt-submit 훅: 세션로그 미작성 리마인드(1~3줄)
  └ kb-write-guard 훅: memory/ 직접 Edit 차단(관리 스킬 경유 강제, 본인 세션로그 예외)
세션 중 기록
  └ 에이전트가 세션로그 이어쓰기 → auto-commit 훅: 로컬 커밋(동기) + push-pending 장부
      └ push-worker(detached): 백그라운드 plain push — 세션은 안 막힘
```

## 설계 철칙 (PR 전에 알아야 할 것)

1. **stdlib-only** — 런타임 pip 의존성 0(`dependencies = []`). git/gh 는 호스트 필수도구지 pip 의존성이 아니다.
2. **엔진은 판단하지 않는다** — 요약·분류·판단은 스킬(에이전트) 몫. 엔진 동사는 멱등한 기계 작업만.
3. **훅은 절대 세션을 죽이지 않는다** — 무raise, 타임아웃(killpg — 손자 프로세스까지), 실패는 비치명 + 다음 표면에서 가시화.
4. **호스트 무접촉 테스트** — 실 `~/.claude`·실 원격 네트워크에 손대는 테스트 금지. tmp + `--settings` 격리, 원격은 로컬 bare/remote-helper 로 모사.
5. **인스턴스 데이터 불가침** — `memory/`·`team.config.json` 을 읽는 제품 코드는 있어도, 동기화/삭제 경로에 올리는 코드는 없다.
6. **재현 가능한 설치** — 배포 아티팩트(install.sh·cli.py)는 릴리스 태그에 핀. main 은 stable 정책(전 변경 PR+CI).

## 어디부터 읽나 (기여 시나리오별)

- **엔진 동사 추가/수정** → `docs/spec/` 해당 절 → `infra/teammode.py` 디스패처 → 동사별 테스트 패턴(`tests/test_update.py` 등) 모방.
- **훅 추가** → `infra/hooks/manifest.json` 에 선언 → 훅 본체(정규 stdin 계약) → 어댑터는 손댈 일 없음(manifest 를 읽음).
- **에이전트 지원 추가** → `infra/agents/<name>/` 신설(adapter.py + events.json) → `install_lib.wire_agents()`.
- **L2 provider 추가** → `providers/<name>.json` 데이터 팩(코드 변경 없이 동작해야 정상).
- **버그 수정** → 재현 테스트 먼저(red) → 수정(green) → `python3 -m pytest -q` 전체.

기여 절차·커밋 스타일은 [CONTRIBUTING.md](CONTRIBUTING.md).
