**English** | [한국어 ↓](#tm-mode--한국어)

# tm-mode

[![CI](https://github.com/T-Gates/tm-mode/actions/workflows/test.yml/badge.svg)](https://github.com/T-Gates/tm-mode/actions/workflows/test.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

> Turn your team mode on. — A **cross-agent team collaboration toolkit** for AI coding agents (Claude Code · Codex).

**Everyone on the team works with their own AI, and nobody has to write up or ask "so what did you do today?"**
Each session, the agent automatically *reads* the team context and *records* what it did. The only thing a human does is `git push`.

## Install — just copy-paste

Common requirements: **`python3` (3.9+) · `git`**. Anything else is per-case below.

### ⓐ Join a team (member — the team repo already exists)

The common case — nothing beyond the common requirements (python3·git); **no `gh` needed**.

```bash
pip install "git+https://github.com/T-Gates/tm-mode"   # launcher (after PyPI: pipx install tm-mode)
tm-mode join <team-repo-clone-url>                      # clone → setup → remote wiring, all at once
```

Or **let the agent do it** — just clone and open:

```bash
git clone <team-repo-clone-url> && cd <repo-name>
```
```text
Open Claude Code / Codex →  "set up this repo"
   → shows an install plan (dry-run, everything it writes to your machine), sets up after you approve.
```

### ⓑ Start a new team (introducer — first to bring it in)

**No need to create a repo first.** `gh` (GitHub CLI) generates *your own team repo* from this template.

```bash
pip install "git+https://github.com/T-Gates/tm-mode"
gh auth login          # GitHub login (once, if you haven't)
tm-mode init           # create repo from template → clone → setup → wire remote
```

### ⓒ New team without gh (fallback)

1. On GitHub, [tm-mode](https://github.com/T-Gates/tm-mode) → **"Use this template"** button to create the repo
2. Run `tm-mode join <new-repo-clone-url>` from ⓐ above

> **Without `pip` (curl):** `curl -fsSL https://raw.githubusercontent.com/T-Gates/tm-mode/refs/tags/v0.1.0/install.sh | sh -s -- join <url>` (`init` too).
> Once installed, run `tm-onboard` in your agent for auto verification & briefing. For activation, flags, and engine verbs, see **→ [INSTALL.md](INSTALL.md)** (Korean).

> Status: **v0.1 — L1 (team memory, automatic context injection, session logs, Obsidian view) works and is validated in daily use.** For L2 (service connections), some providers work today (linear, notion — those with MCP launch info); others (slack, google) are placeholders while the provider pack grows.

---

## Why tm-mode?

> **In one line:** the *writer and reader* of team memory shifts from humans to agents.
> With Slack, Notion, or a wiki, *humans write and humans read*. With tm-mode, **agents do both** → zero extra human labor.

That's the core, and it shows up as two pillars:

### Pillar ① Work flow — automatic recording & injection

> **Before** — daily "what are you working on?" standups, scrolling Slack, writing end-of-day recaps.
> **After** — open a session and the team state is already there; the day's work and decisions get recorded by the agent on their own.

At session start, a hook injects each member's recent session logs into the agent, and every session the agent records what it did into `memory/`. **Nobody tells a human to write things down** — the agent follows the reminders it receives.

### Pillar ② Team & product memory — pulled directly from memory

> **Before** — dig through Notion for product specs, domain rules, and past decisions, then copy-paste them into the agent.
> **After** — the agent **pulls team and product memory directly** from memory. No human ferrying context around.

Pile up product specs, team rules, decisions, and domain knowledge as markdown in `memory/`, and the agent searches and retrieves them when needed. **A single source of internal team memory that agents consume directly** — replacing the internal wiki or Notion.

### Why not Slack · Notion · meetings?

| | Slack · Notion · wiki | tm-mode |
|---|---|---|
| Who **writes** | humans (end-of-day write-ups) | **agents, automatically** |
| Who **reads** | humans (search & copy-paste) | **agents, automatically at session start** |
| Extra human labor | yes | **zero** |

### Supporting strengths

| Strength | One line |
|---|---|
| 📈 **Compounding · zero-day onboarding** | The more logs accumulate, the thicker the context; a new member starts day one with the full history — zero handover meetings. |
| 🤖 **Cross-agent · zero lock-in** | Team members can use different agents (Claude Code, Codex) and share the same memory. No forced tool standardization; switch agents and keep your context. |
| 🌿 **Git-native** | Markdown + git. Zero servers/infra, 100% data ownership; history, diffs, and backups come free. |

<details>
<summary>More strengths</summary>

| Strength | One line |
|---|---|
| 📝 **Personal asset** | Reasons behind decisions, blockers, and daily work remain on record — material for retrospectives, résumés, and blog posts. |
| 🔒 **Safety first** | Tokens stay in a local vault, real config writes are gated behind `--yes`, and pushing is a human decision. |
| 🧩 **No per-agent redefinition** | Put a skill once in `infra/skills/base/` and it deploys to both Claude and Codex. |
| 🎚️ **Skill management** | Define and share the team's skills in one place; install only the ones you want. |
| 🔏 **Log privacy** | Session logs are guided to record team work only, and the recording point is explicit every session. |

</details>

## What you get (L1)

| Feature | Description |
|---|---|
| **Team memory** | Session logs, decisions, and an INDEX as markdown in `memory/`, shared via git. |
| **Automatic context injection** | At session start, a hook (`session-start.py`) injects each member's recent session logs into the agent. |
| **Mechanical session logging** | `teammode.py log` handles dates, frontmatter, and the 6 AM cutoff automatically (agents can't get filenames wrong). |
| **Obsidian view** *(opt-in, zero keys)* | Open `memory/` as an Obsidian vault and see team memory as a graph. Auto-registration supported. |

## Team lifecycle

```
team setup (introducer, once)  →  personal setup (each member)  →  service connections (L2)
```

## Layout

```
infra/
├── teammode.py        # engine (verbs)
├── install.py         # bootstrap (setup)
├── install_lib.py     # bootstrap pure core
├── git_ops.py         # shared git ops
├── agents/<name>/     # per-agent adapters (claude · codex)
├── hooks/             # shared hooks (session-start · session-log-remind · auto_pull)
└── skills/            # skills (tm-onboard …)
memory/                # team memory (created at setup)
conformance/           # compatibility checks + golden scenarios
```

Spec: [docs/spec/](docs/spec/README.md) — the single authoritative SPEC v0.3 (Korean; docs are Korean-first). Contributor map: the Architecture section above (Korean original in the [한국어 section](#tm-mode--한국어)).

## Architecture — a map for contributors

> A map so someone arriving to change the code knows *where to look* within 10 minutes. The single authority on behavior (contracts) is [docs/spec/](docs/spec/README.md) (Korean).

```
┌─ Launcher (src/teammode/cli.py · install.sh) ─────────────┐
│  Thin entry point shipped via pip/curl/npx. Repo create,   │
│  clone, wizard. After clone it does nothing — install and  │
│  runtime behavior all live in the engine inside the repo.  │
└──────────────────────┬────────────────────────────────────┘
                       ▼ clone
┌─ Team repo (template copy = team instance) ───────────────┐
│  infra/   ← product code (engine·hooks·adapters·skills),   │
│             synced via `tm-mode update`                    │
│  memory/  ← team data. upstream never touches it           │
│  tests/ conformance/ ← validation layer, file-level sync   │
└───────────────────────────────────────────────────────────┘
```

<details>
<summary>Component map · session data flow · design principles · where to start</summary>

| Path | What it is | Read together with |
|---|---|---|
| `src/teammode/cli.py` | **Launcher.** stdlib single file (downloaded raw by curl/npx — no package imports) | `install.sh`, `npm/bin/tm-mode.js` |
| `infra/teammode.py` | **Engine.** Verb dispatcher — on/off/log/context/pull/commit/update/issue/memory/util | [docs/spec/](docs/spec/README.md) §3 |
| `infra/install.py` + `install_lib.py` | **Bootstrap.** Hook wiring, skill deploy, env. `--dry-run`/`--yes` gates | `tests/test_install_*.py` |
| `infra/git_ops.py` | **Shared git ops** + sync planning (validation plan/apply) — never raises, timeouts, killpg | `tests/test_git_ops.py` |
| `infra/agents/<name>/` | **Adapters.** Render per-agent config (Claude settings.json · Codex config.toml) | `infra/hooks/manifest.json`(single source), `events.json` |
| `infra/hooks/` | **Shared hooks.** session-start(context injection)·auto-commit·push-worker(async push)·kb-write-guard(memory write governance) | per-hook tests |
| `infra/skills/` | **Skills.** base(deployed to both agents)·core(tm-onboard·tm-connect·tm-memory…)·util. **Engine = mechanical, skills = judgment** | `docs/spec/skills.md` |
| `conformance/check.py` | **Conformance.** Machine-checks that an instance honors the spec contracts | golden scenarios |
| `providers/*.json` | **L2 provider packs.** Data for service slots (issues/chat/docs/calendar) | `infra/skills/core/tm-connect/` |
| `npm/` | **npm shim.** Publish artifact — thin `npx tm-mode` skin over the pinned cli.py | `tests/test_npm_wrapper.py` |

One session cycle:

```
session start
  └ session-start hook: team origin reconcile (once) + recent session logs
    + memory INDEX + guidelines injected
  └ (on `tm on`) auto_update_on_start: detect upstream engine/validation lag
    — applying is `tm-mode update` only
while working
  └ user-prompt-submit hook: session-log reminder (1–3 lines)
  └ kb-write-guard hook: blocks direct edits to memory/ (use the managing
    skills; your own session log is exempt)
recording
  └ agent appends the session log → auto-commit hook: local commit (sync)
    + push-pending ledger
      └ push-worker (detached): plain push in background — the session never blocks
```

Design principles:

1. **stdlib-only** — zero runtime pip deps (`dependencies = []`); git/gh are host prerequisites.
2. **The engine never judges** — summarizing/classifying is the skills' (agent's) job; engine verbs are idempotent mechanics.
3. **Hooks never kill a session** — no raises, timeouts (killpg down to grandchildren), failures are non-fatal and surfaced later.
4. **Host-untouched tests** — never touch real `~/.claude` or real remotes; tmp + `--settings` isolation, remotes faked locally.
5. **Instance data is inviolable** — no product code path syncs or deletes `memory/`·`team.config.json`.
6. **Reproducible installs** — distribution artifacts (install.sh·cli.py·npx shim) are pinned to release tags; main stays stable via PR+CI.

Where to start, by contribution type: engine verb → spec § then `infra/teammode.py`; new hook → declare in `infra/hooks/manifest.json`; new agent → `infra/agents/<name>/`; new L2 provider → `providers/<name>.json` (no code change needed); bug fix → red test first, then `python3 -m pytest -q`.

</details>

## License

tm-mode is distributed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

---

# tm-mode — 한국어

[English ↑](#tm-mode) | **한국어**


> Turn your team mode on. — AI 코딩 에이전트(Claude Code · Codex)를 위한 **크로스에이전트 팀 협업 툴킷.**

**팀원이 각자 AI로 일해도, 누구도 "지금 뭐 했는지" 정리하거나 묻지 않는다.**
에이전트가 세션마다 팀 맥락을 자동으로 *읽고*, 한 일을 자동으로 *남긴다*. 사람이 하는 건 `git push`뿐.

## 설치 — 복붙하면 됩니다

공통 요구사항: **`python3`(3.9+) · `git`**. 그 외 필요한 건 아래 각 상황에만.

### ⓐ 팀에 합류 (팀원 — 팀 레포가 이미 있을 때)

가장 흔한 경우 — 공통 요구사항(python3·git) 외 추가 도구 불필요(**`gh` 안 씀**).

```bash
pip install "git+https://github.com/T-Gates/tm-mode"   # 런처 (PyPI 발행 후: pipx install tm-mode)
tm-mode join <팀레포-clone-url>                          # 클론 → 셋업 → remote 연결까지 한 번에
```

또는 **에이전트한테 맡기기** — 클론만 해두고 열어서 시키면 된다:

```bash
git clone <팀레포-clone-url> && cd <레포명>
```
```text
Claude Code / Codex 를 열고 →  "셋업해줘"
   → 설치 계획(dry-run, 내 컴퓨터에 뭘 쓰는지 전부)을 보여주고, 승인받은 뒤 설치.
```

### ⓑ 새 팀 시작 (도입자 — 팀에 처음 들이는 사람)

**레포를 미리 만들 필요 없다.** `gh`(GitHub CLI)가 이 템플릿에서 *당신의 새 팀 레포*를 자동 생성한다.

```bash
pip install "git+https://github.com/T-Gates/tm-mode"
gh auth login          # GitHub 로그인 (아직 안 했다면, 한 번만)
tm-mode init           # 템플릿 복제로 새 레포 생성 → 클론 → 셋업 → remote 연결
```

### ⓒ gh 없이 새 팀 만들기 (폴백)

1. GitHub 에서 [tm-mode](https://github.com/T-Gates/tm-mode) → **"Use this template"** 버튼으로 레포 생성
2. 위 ⓐ 의 `tm-mode join <새 레포 clone-url>` 실행

> **`pip` 없이 (curl):** `curl -fsSL https://raw.githubusercontent.com/T-Gates/tm-mode/refs/tags/v0.1.0/install.sh | sh -s -- join <url>` (`init` 도 동일).
> 설치가 끝나면 에이전트에서 `tm-onboard` — 검증·가치 브리핑이 자동. 활성화·플래그·엔진 동사 등 상세는 **→ [INSTALL.md](INSTALL.md)**.

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
