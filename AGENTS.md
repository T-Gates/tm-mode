# AGENTS.md — tm-mode

이 레포는 **tm-mode**(크로스에이전트 팀 협업 툴킷)다. 에이전트(Claude Code · Codex)가 이 파일을 읽고 셋업·운영을 안내한다.

## 첫 접촉: "셋업해줘" / "온보딩"
사용자가 팀모드를 켜달라고 하면 **`tm-onboard` 스킬**을 따른다(`infra/skills/base/tm-onboard/SKILL.md`).

> **설치는 CLI가 끝낸다.** 팀 레포 생성·clone·scaffold·훅 배선까지 전부 아래 CLI 명령이 wizard로 처리한다. 에이전트(스킬)는 설치를 직접 실행하지 않는다.
>
> - **새 팀 (레포 없음)**: `tm-mode init` — org/계정·팀명·레포명을 대화로 정하고 레포 생성 → 곧바로 join(clone+셋업)
> - **기존 팀 합류**: `tm-mode join <clone-url>` — 설치 위치·에이전트·이름·역할·Obsidian을 wizard로 묻고 clone+셋업
>
> 설치가 끝나면 CLI(`_done()`)가 *"Claude/Codex를 열고 'tm-onboard' 입력 → 검증·브리핑 자동 진행"*이라고 안내한다.

**스킬(tm-onboard)이 하는 일은 딱 둘뿐:**

1. **설치 검증** — 검증 전용 서브에이전트에 위임(메인은 기다리지 않음)
2. **팀모드 가치 전달** — `infra/skills/base/tm-onboard/value.md`를 읽고 사람에게 전달

스킬은 `install.py`를 직접 호출하지 않으며, 멤버명·org·팀명·역할·에이전트·Obsidian을 묻지 않는다. 아직 설치 안 된 사람이 "셋업해줘"라고 하면 → `tm-mode init`(새 팀) / `tm-mode join <url>`(합류)을 터미널에서 실행하도록 안내 후 멈춘다.

## 서비스 연결: "연결해줘" / "서비스 붙여줘"
역할 슬롯(issues / chat / docs / calendar)에 서비스를 붙이려면 **`tm-connect` 스킬**을 따른다(`infra/skills/core/tm-connect/SKILL.md`). tm-onboard 는 L2 서비스 연결을 다루지 않는다 — 필요한 순간 `tm-connect` 스킬이 트리거로 드러난다(progressive). 실제 연결(토큰 안내·금고 저장·config 슬롯 기록·재배선)은 tm-connect 가 한다.

- 발급 링크·단계·연결방식은 `providers/<provider>.json` 의 `token_guide`·`auth`·`default_scope`·`resource_fields` 를 **데이터로 읽어** 안내한다(하드코딩 금지).
- **각자 입력(v0.1)**: 각 멤버가 자기 토큰을 직접 입력 → 로컬 금고(`infra/credentials.py`, 0600). 팀 자동공유 없음.
- ⚠️ 평문 금고이므로 **동기화 폴더(Syncthing/Dropbox 등) 금지.**

## 안전 (필수)
- 팀 루트는 **`--root` 명시로만.** 환경변수(`TEAMMODE_HOME` 등)를 신뢰하지 않는다.
- 실 에이전트 설정(`~/.claude/settings.json`)·셸 프로파일·`obsidian.json`은 **`--yes`(실설치) 또는 `--settings`(격리) / `--register-obsidian` 동의** 없이는 건드리지 않는다.
- 막히면(exit≠0) 사유를 사람에게 옮기고 멈춘다. 추측 수리 금지.
- **Windows PowerShell**: git 의 진단 메시지(clone 진행률·remote 정보 등)는 stderr 로 나와 빨간색으로 표시되지만 비치명 — 실제 에러가 아니면 무시해도 된다.

## 운영 (셋업된 팀 레포에서)
- 팀 작업 맥락은 **세션로그**로 남는다. 직접 파일을 만들지 말고 엔진(`teammode.py log`)/훅이 기록하게 한다(날짜·frontmatter·06시컷 자동).
- 세션 시작 시 `session-start.py` 훅이 팀원별 최근 세션로그를 자동 주입한다 — 팀 현황은 거기서 파악.
- 푸시·PR은 사람이 결정. 에이전트가 임의 푸시하지 않는다.

## 핵심 파일
- **진입 CLI**: `src/teammode/cli.py` (`tm-mode init` / `tm-mode join` — wizard로 설치 전담)
- 엔진: `infra/teammode.py` (동사 on/off/log/context/pull/commit/update)
- 셋업: `infra/install.py` (+ `install_lib.py`) — CLI가 subprocess로 위임 호출
- 훅: `infra/hooks/` · 어댑터: `infra/agents/<name>/` · 스킬: `infra/skills/`
- 동작이 예상과 다르면 설계 스펙([설치·부트스트랩](docs/spec/onboarding.md) · [온보딩 스킬](docs/spec/skills.md)) 확인.
