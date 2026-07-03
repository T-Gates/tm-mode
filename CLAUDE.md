# CLAUDE.md — tm-mode

이 레포의 에이전트 운영·셋업 지침은 **[AGENTS.md](AGENTS.md)** 를 단일 소스로 따른다 (Claude Code · Codex 공통).

## 빠른 시작
사용자가 **"이 레포 셋업해줘"**(또는 "팀모드 셋업"·"온보딩")라고 하면, AGENTS.md의 **"첫 접촉"** 절차대로 `tm-onboard` 스킬(`infra/skills/base/tm-onboard/SKILL.md`)을 따른다.

- **설치 경로 둘**: ① clone-and-go — 팀 레포를 클론했으면 AGENTS.md "첫 접촉" bootstrap(dry-run 계획 → 대화 승인 → `install.py --yes`)으로 여기서 셋업 완료 ② CLI — 새 팀: `tm-mode init`, 합류: `tm-mode join <url>`(wizard).
- **스킬(tm-onboard)은 설치 후 검증·브리핑만 한다.** `install.py` 직접 호출·멤버명·팀명 묻기는 스킬이 하지 않는다(설치·멤버명 1회 질문은 AGENTS.md bootstrap 절차 몫).
- 안전: 팀 루트는 `--root` 명시만(env 무신뢰). 실 설정 쓰기는 `--yes`(대화 승인 후) / `--settings`(격리) 게이트. 푸시는 사람 결정.

## 서비스 연결 (L2)
역할 슬롯(issues / chat / docs / calendar)에 서비스를 붙이려면 **`tm-connect` 스킬**(`infra/skills/core/tm-connect/SKILL.md`)을 따른다. tm-onboard 는 첫 가치 직후 *제안*만, 실행은 tm-connect. 발급 안내는 `providers/<provider>.json` 데이터를 읽어 하고, 토큰은 **각자 입력** → 로컬 금고(`infra/credentials.py`, 평문 0600 — 동기화 폴더 금지).

자세한 내용은 [AGENTS.md](AGENTS.md) · 동작 명세는 [docs/spec/](docs/spec/README.md), 백로그·미구현 설계는 [docs/BACKLOG.md](docs/BACKLOG.md) 참조.
