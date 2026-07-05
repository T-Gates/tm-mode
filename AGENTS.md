# AGENTS.md — tm-mode

이 레포는 **tm-mode**(크로스에이전트 팀 협업 툴킷)다. 에이전트(Claude Code · Codex)가 이 파일을 읽고 셋업·운영을 안내한다.

## 첫 접촉: "셋업해줘" / "온보딩"

사용자가 팀모드 셋업/온보딩을 요청하면 **먼저 설치 상태를 판정**한다.

### 설치 상태 판정 (라우팅용 — 보수적으로)

설치 완료로 볼 수 있는 최소 신호 셋: ① `team.config.json` 존재 ② `memory/team/members.md` 존재 ③ `team.config.json`을 **JSON으로 파싱**해 top-level `agents`가 **비어 있지 않은 문자열 list**임(파싱 실패·타입 불일치 = 애매함으로 취급). 셋 다 있으면 → **`tm-onboard` 스킬**(`infra/skills/base/tm-onboard/SKILL.md`)로 — 검증·가치 전달만 한다.

- `.teammode-active`는 **활성화** 마커일 뿐 설치 판정에 쓰지 않는다(install은 on을 켜지 않음).
- 판정은 라우팅용이다 — 완전성 검증은 tm-onboard의 검증 서브에이전트 몫. 오판해도 안전: 설치됨 오판 → tm-onboard 검증이 누락을 드러내 bootstrap 재안내, 미설치 오판 → `install.py`는 멱등이라 재실행 무해.
- 신호가 하나라도 빠졌거나 애매하면 → 아래 **bootstrap**.

### 설치 전 bootstrap (clone-and-go — 레포 클론 = 즉시 사용)

레포 안에 엔진(`infra/`)이 이미 있으므로 CLI 없이도 여기서 셋업이 끝난다. **호스트 설정 쓰기의 동의는 대화 승인으로 받는다** — `--yes`의 본질은 "사람의 명시 의사"다(제품 결정 2026-07-04).

1. **계획만 출력**: `python3 infra/install.py --root . --dry-run --yes`
   — `--yes`를 **함께** 준다: dry-run이 우선이라 아무것도 쓰지 않으면서, 계획은 **실설치 기준**(env 주입·autopush 포함)으로 렌더된다. `--yes` 없이 뽑은 계획은 "비실설치(미주입)" 계획이라 승인 대상과 다르다.
2. 출력에 `member_name=(미정)` blocker가 있으면 **멤버명을 딱 한 번** 묻고, `--member-name <이름>`을 붙여 1을 다시 실행한다. (재질문 금지는 "wizard가 이미 받은" 경우의 규칙 — bootstrap엔 wizard가 없다.)
3. **dry-run 출력 전체를 사용자에게 보여주고 명시 승인을 받는다.** 계획에는 레포 쓰기·실호스트 파일 경로·배선될 훅·env·scaffold 자동 커밋/push 시도·Codex Trust가 담겨 있다. 승인 전에는 실호스트 설정·스킬 디렉토리·셸 env·Obsidian을 **절대 쓰지 않는다.**
4. 사용자가 승인하면 **1의 인자에서 `--dry-run`만 뗀** 실설치: `python3 infra/install.py --root . --yes [--member-name <이름>]`
   — 승인한 계획과 실행이 같은 인자·같은 계약이다. 레포 scaffold 생성/갱신 + 감지된 Claude/Codex 배선 + scaffold 자동 커밋·push 시도를 포함한다.
5. Codex가 배선됐으면 **TUI를 한 번 열어 Trust**가 필요할 수 있음을 안내한다(trusted hash 직접 주입 금지 — 사람 결정).
6. 성공하면 이어서 **`tm-onboard`**(검증+가치)로. 실패(exit≠0)하면 exit code와 메시지를 사람에게 옮기고 멈춘다 — 추측 수리 금지.

> **CLI 경로도 병행 유지**: 새 팀 레포 생성부터면 `tm-mode init`, 클론까지 CLI에 맡기려면 `tm-mode join <clone-url>` — wizard가 대화로 처리하고, 끝나면 에이전트에서 `tm-onboard`.

**스킬(tm-onboard)이 하는 일은 딱 둘뿐:**

1. **설치 검증** — 검증 전용 서브에이전트에 위임(메인은 기다리지 않음)
2. **팀모드 가치 전달** — `infra/skills/base/tm-onboard/value.md`를 읽고 사람에게 전달

스킬은 `install.py`를 직접 호출하지 않으며, 멤버명·org·팀명·역할·에이전트·Obsidian을 묻지 않는다(멤버명 최초 1회 질문은 위 bootstrap **절차**의 몫이지 스킬의 몫이 아니다).

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
