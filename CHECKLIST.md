# CHECKLIST — P1 핫픽스 묶음 (BACKLOG③ 출시 후, 2026-06-18)

기준선: **779 passed** (TMPDIR flaky 수정 후 그린 확정). 최종 = 779 + 신규 green.
구현=sonnet / 검수=codex(이종) / 직렬 / 푸시=Jane 게이트.

## S1 — knowledge 입력 견고성  (`infra/teammode.py`) ✅ 종결 (811 passed)
- [x] write/delete 파일 I/O `try/except` → exit2 친화메시지 (긴파일명 OSError·권한 PermissionError)
- [x] `_validate_author` + filename `isascii()` 강제 (한글 author/filename 통과 차단)
- [x] knowledge content 제어문자 거부 (C1/Cf/surrogate 포함 — codex 1차)
- [x] delete filename 검증·NUL 안전·folder isascii (codex 1차 추가)
- [x] write/delete atomic 정합성·temp 누수 방지 (codex 2차)
- 검수: codex 2라운드(1차 3건+2차 2건 반영). #3 동시write race는 백로그(단일CLI순차모델). 메인 직접확인 종결.

## S2 — 거버넌스(kb-write-guard) 경미  (`infra/hooks/kb-write-guard.py`) ✅ 종결
- [x] 상대경로 fail-closed (normpath `../` 오판 수정 포함)
- [x] memory 내부→밖 symlink 경계 (raw+resolve union)
- [x] malformed input fail-closed (top-level dict·files 타입/다중요소·tool_input)
- 검수: codex 2라운드(1차 normpath/fail-closed 2건 + 재검수 top-level dict/files다중 2건 반영). 메인 직접확인 종결.

## S3 — 윈도우 미세갭  (`infra/install.py`, tm-onboard SKILL) ✅ 종결
- [x] `install.py --help`가 `--root` 없이 exit0 출력 (신규 테스트 5)
- [x] tm-onboard에 `git config user.name/email` 안내
- [x] (P2) AGENTS.md에 PowerShell git stderr 주의
- 검수: trivial(문서2 + argparse1)이라 codex 생략, 테스트 + 메인 직접확인.

## 마감 (사람 몫)
- [x] 전체 pytest green — 779 → 828 (신규 49)
- [ ] Jane diff 검토 → push 결정
- [ ] (이후) 윈도우 실호스트 검증 (push 후 pull)
