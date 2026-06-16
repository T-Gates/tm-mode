# AGENTS.md — teammode

이 레포는 **teammode**(크로스에이전트 팀 협업 툴킷)다. 에이전트(Claude Code · Codex)가 이 파일을 읽고 셋업·운영을 안내한다.

## 첫 접촉: "셋업해줘" / "온보딩"
사용자가 팀모드를 켜달라고 하면 **`tm-onboard` 스킬**을 따른다(`infra/skills/base/tm-onboard/SKILL.md`). 스킬이 없거나 못 쓰는 환경이면 아래를 직접 실행한다:

```bash
# 1) 상태 판별: team.config.json(또는 memory/) 있으면 팀원, 없으면 도입자
# 2) 부트스트랩 (결정적 — install.py 가 스캐폴드·훅·env·verify 를 다 한다)
python infra/install.py --root . --yes                          # 도입자
python infra/install.py --root . --member-name <영문이름> --yes  # 팀원
# 3) 첫 가치: 팀 상황 보여주기
python infra/teammode.py context --root . --json                # → 사람 말로 요약
# 4) (물어보고) Obsidian 볼트 등록 — opt-in, 키 0
python infra/install.py --root . --register-obsidian
```

- **install.py 가 기계적인 건 다 한다. 에이전트는 그걸 호출하고 결과를 사람 말로 옮긴다 — 단계를 손으로 재현하지 말 것.**
- L1(세션로그·맥락주입)을 먼저 보여주고, 서비스 연결(이슈 트래커·채팅·문서·캘린더)은 **나중**에 사람이 원할 때.

## 서비스 연결: "연결해줘" / "서비스 붙여줘"
역할 슬롯(issues / chat / docs / calendar)에 서비스를 붙이려면 **`tm-connect` 스킬**을 따른다(`infra/skills/base/tm-connect/SKILL.md`). tm-onboard 는 첫 가치 직후 연결을 *제안*만 하고, 실제 연결(토큰 안내·금고 저장·config 슬롯 기록·재배선)은 tm-connect 가 한다.

- 발급 링크·단계·연결방식은 `providers/<provider>.json` 의 `token_guide`·`auth`·`default_scope`·`resource_fields` 를 **데이터로 읽어** 안내한다(하드코딩 금지).
- **각자 입력(v0.1)**: 각 멤버가 자기 토큰을 직접 입력 → 로컬 금고(`infra/credentials.py`, 0600). 팀 자동공유 없음.
- ⚠️ 평문 금고이므로 **동기화 폴더(Syncthing/Dropbox 등) 금지.**

## 안전 (필수)
- 팀 루트는 **`--root` 명시로만.** 환경변수(`TEAMMODE_HOME` 등)를 신뢰하지 않는다.
- 실 에이전트 설정(`~/.claude/settings.json`)·셸 프로파일·`obsidian.json`은 **`--yes`(실설치) 또는 `--settings`(격리) / `--register-obsidian` 동의** 없이는 건드리지 않는다.
- 막히면(exit≠0) 사유를 사람에게 옮기고 멈춘다. 추측 수리 금지.

## 운영 (셋업된 팀 레포에서)
- 팀 작업 맥락은 **세션로그**로 남는다. 직접 파일을 만들지 말고 엔진(`teammode.py log`)/훅이 기록하게 한다(날짜·frontmatter·06시컷 자동).
- 세션 시작 시 `session-start.py` 훅이 팀원별 최근 세션로그를 자동 주입한다 — 팀 현황은 거기서 파악.
- 푸시·PR은 사람이 결정. 에이전트가 임의 푸시하지 않는다.

## 핵심 파일
- 엔진: `infra/teammode.py` (동사 on/off/log/context/pull/commit/update)
- 셋업: `infra/install.py` (+ `install_lib.py`)
- 훅: `infra/hooks/` · 어댑터: `infra/agents/<name>/` · 스킬: `infra/skills/`
- 동작이 예상과 다르면 설계 스펙([설치·부트스트랩](docs/spec/onboarding.md) · [온보딩 스킬](docs/spec/skills.md)) 확인.
