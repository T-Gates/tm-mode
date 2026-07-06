**한국어** | [English](README.en.md)

# tm-mode

[![CI](https://github.com/T-Gates/tm-mode/actions/workflows/test.yml/badge.svg)](https://github.com/T-Gates/tm-mode/actions/workflows/test.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

> Turn your team mode on. — AI 코딩 에이전트(Claude Code · Codex)를 위한 **크로스에이전트 팀 협업 툴킷.**

**팀원이 각자 AI로 일해도, 누구도 "지금 뭐 했는지" 정리하거나 묻지 않는다.**
에이전트가 세션마다 팀 맥락을 자동으로 *읽고*, 한 일을 자동으로 *남긴다*. 사람이 하는 건 `git push`뿐.

## 도입 — 두 가지 길

**① clone-and-go (팀 레포가 이미 있으면 — CLI 설치 불필요):**

```bash
git clone <팀레포 clone-url> && cd <팀레포>
# Claude Code / Codex 를 열고: "셋업해줘"
```

→ 에이전트가 설치 계획(dry-run — 내 컴퓨터에 뭘 쓰는지 전부)을 보여주고, 채팅 승인을 받은 뒤 셋업합니다. 레포 안에 엔진이 통째로 들어 있어 클론이 곧 설치 준비 완료.

**② CLI wizard (새 팀 생성부터, 또는 클론까지 맡기려면):**

```bash
pip install "git+https://github.com/T-Gates/tm-mode"
tm-mode init                      # 새 팀 (도입자) — 레포 생성 → 곧바로 셋업
tm-mode join <팀레포 clone-url>    # 기존 팀 합류 (팀원)
```

→ CLI wizard가 org·팀명·이름·에이전트·설치 위치를 묻고 **레포 생성/clone·훅·스킬·env까지 한 번에.** (curl도 동일 — `... | sh -s -- init|join`. PyPI 발행 후에는 `uv tool install tm-mode` / `pipx install tm-mode` 권장.)

어느 길이든 설치가 끝나면 에이전트에서 `tm-onboard` — 검증·가치 브리핑이 자동입니다.

**요구사항**: `python3`(3.9+) · `git` — 이 둘뿐. (`tm-mode init`의 레포 자동 생성만 `gh` 선택 사용.)

> 상태: **v0.1 — L1(팀 메모리·맥락 자동주입·세션로그·Obsidian 뷰) 동작·실사용 검증 완료.** L2(서비스 연동)는 일부 provider(linear·notion 등 MCP 실행정보 보유분)가 동작하고, 나머지(slack·google 등)는 placeholder — provider 팩 확장 중.

---

## 왜 tm-mode?

> **한 줄로:** 팀 메모리의 **기록·열람 주체가 사람 → 에이전트로** 넘어간다.
> Slack·Notion·위키는 *사람이 쓰고 사람이 읽지만*, tm-mode는 양쪽 다 **에이전트가** 한다 → 사람의 추가 노동 0.

이게 핵심이고, 두 기둥으로 나타난다:

### 기둥 ① 작업 흐름 — 자동 기록·주입

> **Before** — 매일 "지금 뭐 하고 있어?" 스탠드업, 슬랙 스크롤, 퇴근 전 회고 정리.
> **After** — 세션을 켜면 팀 상태가 이미 떠 있고, 그날 한 일·결정은 에이전트가 알아서 남긴다.

세션 시작 시 훅이 팀원별 최근 세션로그를 에이전트에 주입하고, 매 세션 에이전트가 한 일을 `memory/`에 기록한다. **사람은 기록하라고 시키지 않는다** — 에이전트가 받은 리마인드대로 남긴다.

### 기둥 ② 팀·제품 메모리 — 메모리에서 끌어쓰기

> **Before** — 노션을 뒤져 제품 스펙·도메인 규칙·과거 결정을 찾아 복붙해서 에이전트에 먹인다.
> **After** — 에이전트가 메모리에서 팀·제품 메모리를 **직접 끌어쓴다.** 사람이 찾아 나르지 않는다.

제품 스펙·팀 규칙·결정·도메인을 `memory/`에 마크다운으로 쌓아두면, 에이전트가 필요할 때 검색해 가져온다. **팀 내부 메모리를 에이전트가 직접 끌어쓰는 단일 소스** — 사내 위키·노션을 대체한다.

### 왜 Slack·Notion·회의가 아니라?

| | Slack · Notion · 위키 | tm-mode |
|---|---|---|
| 누가 **쓰나** | 사람 (퇴근 전 정리) | **에이전트가 자동으로** |
| 누가 **읽나** | 사람 (검색·복붙) | **에이전트가 세션 시작에 자동** |
| 사람의 추가 노동 | 있음 | **0** |

### 그 위에 받쳐주는 강점

| 강점 | 한 줄 |
|---|---|
| 📈 **복리 · 합류자 제로데이** | 로그가 쌓일수록 맥락이 두꺼워지고, 합류자는 첫날 전체 히스토리를 안고 시작 — 인수인계 회의 0. |
| 🤖 **크로스에이전트 · 종속 0** | 팀원마다 다른 에이전트(Claude Code·Codex)를 써도 같은 메모리를 공유. 도구 통일 강제 없고, 갈아타도 맥락 그대로. |
| 🌿 **git 네이티브** | 마크다운 + git. 서버·인프라 0, 데이터 소유권 100%, 이력·diff·백업은 공짜. |

<details>
<summary>그 밖의 강점</summary>

| 강점 | 한 줄 |
|---|---|
| 📝 **개인 자산** | 결정의 이유·막힌 점·그날 한 일이 남아 회고·이력서·자기소개서·블로그 글감이 된다. |
| 🔒 **안전 우선** | 토큰은 로컬 금고, 실 설정 쓰기는 `--yes` 게이트, 푸시는 사람 결정. |
| 🧩 **에이전트별 재정의 불필요** | 스킬을 `infra/skills/base/`에 한 번 두면 Claude·Codex 양쪽에 자동 배포. |
| 🎚️ **스킬 관리** | 팀이 쓸 스킬을 한 곳에서 정의·공유하고, 원하는 스킬만 골라 설치. |
| 🔏 **로그 프라이버시** | 세션로그엔 팀 작업만 기록하도록 안내되고, 기록 시점이 매 세션 명시된다. |

</details>

## 무엇이 되나 (L1)

| 기능 | 설명 |
|---|---|
| **팀 메모리** | `memory/`에 세션로그·결정·INDEX를 마크다운으로. git으로 공유. |
| **맥락 자동주입** | 세션 시작 시 훅(`session-start.py`)이 팀원별 최근 세션로그를 에이전트에 주입. |
| **세션로그 기계 기록** | `teammode.py log`가 날짜·frontmatter·06시컷을 자동 처리(에이전트가 파일명 틀릴 일 0). |
| **Obsidian 뷰** *(opt-in, 키 0)* | `memory/`를 Obsidian 볼트로 열면 팀 메모리가 그래프로. 자동 등록도 지원. |

## 팀 생애주기

```
팀 셋업 (도입자 1회)  →  개인 셋업 (각 멤버)  →  서비스 연결 (L2)
```

## 설치

**팀 레포가 있으면 clone-and-go(클론 → "셋업해줘"), 새 팀 생성부터면 `tm-mode init` 한 줄**(위 [도입 — 두 가지 길](#도입--두-가지-길)). 요구사항·**활성화**(`tm on`)·플래그·엔진 동사 등 상세는 **→ [INSTALL.md](INSTALL.md)**.

## 아키텍처 — 코드 지도 (기여자용)

> 코드를 고치러 온 사람이 10분 안에 "어디를 보면 되는지" 알게 하는 지도. 동작 명세(계약)는 [docs/spec/](docs/spec/README.md)가 단일 권위다.

### 큰 그림 — 3계층

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

<details>
<summary>컴포넌트 지도 · 세션 데이터 흐름 · 설계 철칙 · 기여 시나리오별 진입점</summary>

### 컴포넌트 지도

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

### 세션 한 사이클 (데이터 흐름)

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

### 설계 철칙 (PR 전에 알아야 할 것)

1. **stdlib-only** — 런타임 pip 의존성 0(`dependencies = []`). git/gh 는 호스트 필수도구지 pip 의존성이 아니다.
2. **엔진은 판단하지 않는다** — 요약·분류·판단은 스킬(에이전트) 몫. 엔진 동사는 멱등한 기계 작업만.
3. **훅은 절대 세션을 죽이지 않는다** — 무raise, 타임아웃(killpg — 손자 프로세스까지), 실패는 비치명 + 다음 표면에서 가시화.
4. **호스트 무접촉 테스트** — 실 `~/.claude`·실 원격 네트워크에 손대는 테스트 금지. tmp + `--settings` 격리, 원격은 로컬 bare/remote-helper 로 모사.
5. **인스턴스 데이터 불가침** — `memory/`·`team.config.json` 을 읽는 제품 코드는 있어도, 동기화/삭제 경로에 올리는 코드는 없다.
6. **재현 가능한 설치** — 배포 아티팩트(install.sh·cli.py)는 릴리스 태그에 핀. main 은 stable 정책(전 변경 PR+CI).

### 어디부터 읽나 (기여 시나리오별)

- **엔진 동사 추가/수정** → `docs/spec/` 해당 절 → `infra/teammode.py` 디스패처 → 동사별 테스트 패턴(`tests/test_update.py` 등) 모방.
- **훅 추가** → `infra/hooks/manifest.json` 에 선언 → 훅 본체(정규 stdin 계약) → 어댑터는 손댈 일 없음(manifest 를 읽음).
- **에이전트 지원 추가** → `infra/agents/<name>/` 신설(adapter.py + events.json) → `install_lib.wire_agents()`.
- **L2 provider 추가** → `providers/<name>.json` 데이터 팩(코드 변경 없이 동작해야 정상).
- **버그 수정** → 재현 테스트 먼저(red) → 수정(green) → `python3 -m pytest -q` 전체.

</details>

스펙: [docs/spec/](docs/spec/README.md) — 단일 권위 SPEC v0.3.

## 라이선스

tm-mode는 Apache License 2.0으로 배포됩니다. 자세한 내용은 [LICENSE](LICENSE)를 참조하세요.
