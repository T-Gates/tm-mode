# CLAUDE.md — teammode

이 레포의 에이전트 운영·셋업 지침은 **[AGENTS.md](AGENTS.md)** 를 단일 소스로 따른다 (Claude Code · Codex 공통).

## 빠른 시작
사용자가 **"이 레포 셋업해줘"**(또는 "팀모드 셋업"·"온보딩")라고 하면, AGENTS.md의 **"첫 접촉"** 절차대로 `tm-onboard` 스킬(`infra/skills/base/tm-onboard/SKILL.md`)을 따른다 — 없으면 `python infra/install.py --root . --member-name <영문이름> --yes` 직접 실행.

- **install.py가 기계적인 건 다 한다.** 에이전트는 호출하고 결과를 사람 말로 옮긴다 — 단계를 손으로 재현하지 않는다.
- 안전: 팀 루트는 `--root` 명시만(env 무신뢰). 실 설정 쓰기는 `--yes`/`--settings` 게이트. 푸시는 사람 결정.

자세한 내용은 [AGENTS.md](AGENTS.md) · 동작 명세는 [SPEC.md 미포함 시 spec/](spec/) 참조.
