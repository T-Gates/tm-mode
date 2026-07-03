# Changelog

이 파일은 tm-mode의 주요 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따릅니다.

## [Unreleased]

### Added

- clone-and-go: 팀 레포 클론 → 에이전트 "셋업해줘"로 셋업 완료 — AGENTS.md 첫 접촉 bootstrap(dry-run 계획 → **대화 승인** → `--yes` 실설치 → Codex Trust 안내 → tm-onboard). 설치 상태 판정(config+members+agents)·CLI 경로 병행 유지. README/INSTALL/spec 갱신.
- async push (#45): auto-commit 훅의 동기 구간을 로컬 커밋까지로 축소 — push 는 XDG push-pending ledger + detach `push-worker`(per-team lock·drain loop·**plain-push-only**, 로컬 히스토리 무접촉) 가 담당. 가시화 3중: session-start pending×ahead 판정(재kick/stale 자동정리/보수경고) + UserPromptSubmit pending-age 경고(30분 스로틀) + auto-commit 잔존 pending 경고 1줄. manifest 30s→20s(index.lock full retry worst 실계산).
- `tm-import-memory` 스킬 — 외부 문서(docs 슬롯) → 팀 memory 대량 업로드: preview 확인 게이트·주제 병합·`## 출처` 절·route upsert 선행 (#51)
- memory 허용 폴더: 루트 INDEX 라우팅 맵 등재 최상위 폴더 동적 허용 — 팀 고유 도메인(`fundraise/` 등) write/delete 가능 (#51)

- **v1 Phase 1** (#31): install 동사(role-by-verb) 재편, 호스트 메시지 i18n, push 결과 가시화
- `memory route {upsert|remove}` 동사 — 루트 INDEX 라우팅 맵 관리 (#16)
- Codex PreToolUse 훅 지원 + kb-write-guard 파일별 판정 (#17)
- session-log-remind systemMessage를 ux config로 옵트아웃 가능하게 (#25)
- tm-context: 세션로그 심층읽기 + L2 이슈/캘린더 `tm-<provider>` 직접조회 (#13), 멤버 이모지 렌더 복구 (#14)

### Removed

- `soma/` 내장 정적 허용 제거 — 특정 팀 도메인의 제품 하드코딩(오염). 팀 전용 최상위 memory 폴더는 이제 `memory route upsert` 로 루트 INDEX 에 등재해야 `memory write/delete` 가 허용된다(#51). 거부 시 등록 명령 힌트를 stderr 로 안내.

### Changed

- memory 정적 허용 폴더 = 범용 스캐폴드 3개(`product`·`team`·`team/decisions`)만 — 팀 전용 도메인은 route 등재 기반 동적 허용으로 일원화 (#51)
- spec_version 0.2 → 0.3 — `memory`·`util` 동사 스펙 명문화(§3.6·§3.7) (#51)

- 라이선스를 Apache-2.0으로 전환 (#27)

### Fixed

- `memory write` 가 `INDEX.md` 파일명을 거부하지 않던 비대칭(엔진 관리 파일 덮어쓰기 가능) (#51)

- install: 디스패치 게이트 agent-aware(`--codex --config` 인정) + `--root`→`--team-root` 번역, plain `sync`가 기존 on/off 상태 보존(statusMessage·trust 해시 유지), 비호스티드 provider 의 기존 MCP 서버 감지 안내, 사문 `--check-mcp` 제거 (#3)
- sync: 세션 시작 시 reconcile, push 실패 표면화, non-noreply 이메일 경고 (#30)
- mcp: 공식 hosted MCP(notion/linear)를 http로 등록, 수동 attach 안내 (#29)
- codex: 훅에 `TEAMMODE_MEMBER` 전달, fallback 리마인더 무한반복 중단 (#28)
- memory: 세션로그 백링크 위키링크에서 `memory/` 접두 제거 (#22)
- git_ops: auto-commit push가 non-ff 거부 시 fetch+rebase 자동복구 (#19)
- memory delete 동사: INDEX.md 없는 폴더에서 커밋 abort 버그 (#15)
- statusline: 래핑 멱등화 — off→on 토글 시 배너 누적(double-wrap) 차단 (#10)
