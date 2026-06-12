# teammode 자율 빌드 체크리스트 (goal)

> 시작: 2026-06-13 01:25 / 모드: 무인 dev-cycle (구현→적대적 검수→반영 루프)
> 규칙: 슬라이스 순서대로. 각 슬라이스는 검수 "수정할 내역 없음" 나올 때까지 루프. 푸시 금지(Jane 판단). 항목별 커밋(conventional + Co-Authored-By).
> 스펙 소스: `/home/jane-doe/work/extras/acme/teammode/spec/{01,02,03}-*.md` + `teammode-adapter-spec-draft.md`

## 기준선
- [x] 빈 레포, 테스트 0개 = 기준선 0

## 슬라이스 1 — 검수 도구 우선 (골든 시나리오 + 러너)
- [x] 1.1 `conformance/scenarios/` — 골든 시나리오 5개 선언적 명세 (on→배너 / context 주입 / issue 생성 / log 누적 / off 저장)
- [x] 1.2 `conformance/check.py` — 3모드 러너 골격: `lint`(정적) `verify`(동적 시나리오) `conform`(임의구현+Tier). 우선 verify/lint 동작
- [x] 1.3 `tests/test_check.py` — 시나리오 파싱·통과/실패 판정·Tier 산출 (RED→GREEN)
- [x] 1.4 빈 엔진에 `verify` 실행 → 전부 RED 확인(=인수 테스트로 박힘), 결과를 BUILD-LOG.md에 기록
- [x] 1.5 검수 통과("수정할 내역 없음")

## 슬라이스 2 — Claude 어댑터 수직 슬라이스
- [ ] 2.1 `infra/hooks/manifest.json` — 정규형 샘플(PostToolUse+file_edit, SessionStart, PreToolUse+mcp; enforcement/fallback 필드 포함)
- [ ] 2.2 `infra/agents/claude/events.json` — 번역표(events 매핑, actions.file_edit→`Write|Edit`, config_file)
- [ ] 2.3 `infra/agents/claude/adapter.py` — `sync` 구현(파싱→번역→settings.json upsert, normalize 경유 배선, 멱등, 제거)
- [ ] 2.4 `infra/install.py` — 디스패처 골격(`--claude`→adapter 위임, 분기로직 0)
- [ ] 2.5 `tests/test_adapter_claude.py` — 6케이스(정규엔트리/action번역/mcp번역/멱등/제거/normalize경유)
- [ ] 2.6 검수 통과
- [ ] 2.7 `verify` 재실행 → on/off 시나리오 일부 GREEN 전환 확인

## 슬라이스 3 — normalize 런타임 + 공통 훅 1종 (stretch)
- [ ] 3.1 `infra/agents/claude/normalize.py` — 입력 JSON→정규 스키마 변환 + 자가 필터
- [ ] 3.2 공통 훅 1종 이식(session-log-remind 또는 auto-commit) — 정규 스키마만 인지
- [ ] 3.3 `tests/test_normalize.py`
- [ ] 3.4 검수 통과

## 슬라이스 4 — Codex 어댑터 + 폴백 (stretch)
- [ ] 4.1 `infra/agents/codex/{events.json,adapter.py,normalize.py}` (PreToolUse null + fallback)
- [ ] 4.2 `tests/test_adapter_codex.py` — 폴백·enforcement 축소 검증
- [ ] 4.3 검수 통과 + 크로스에이전트 시나리오 GREEN

## 마감 (사람 몫 — 에이전트 금지)
- [ ] 푸시/PR 판단 (Jane)
- [ ] 세션로그·계획서 반영
