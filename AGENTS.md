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
- L1(세션로그·맥락주입)을 먼저 보여주고, 서비스 연결(Linear·Slack…)은 **나중**(L2, 아직 준비 중)이라고 안내.

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
- 동작이 예상과 다르면 설계 스펙(`spec/04-install.md`·`spec/05-onboard-skill.md`) 확인.
