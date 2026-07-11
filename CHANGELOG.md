# Changelog

이 파일은 tm-mode의 주요 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따릅니다.

## 0.1.3 — 2026-07-08

- `tm-mode update [path]` launcher subcommand: existing team repos can now run the PyPI/pipx launcher to sync the repo engine from upstream (`--dry-run` and `--force` pass through to `infra/teammode.py update`).
- Session-start engine update notice: active teams now get an actionable notice when local `NOTICE.md` differs from upstream, with a throttled fetch so long-running `on` teams are not silently left behind.
- Contributor CI gate docs: PR template and CONTRIBUTING now spell out Python 3.9 compatibility, local environment isolation, fake git remote branch setup, identity hygiene, and post-main-merge full-suite reruns.

## 0.1.2 — 2026-07-07

- **Install wizard, redesigned**: clack-style rail UI with arrow-key widgets, vivid palette (stdlib ANSI, zero deps), context lines and key hints on every step, `◇ answer` echoes, URL Step 0 (`tm-mode join` without arguments now asks), and `init` fully matching the same style.
- **English by default, Korean preserved**: engine output, docs, skills, and agent entry docs (AGENTS/CLAUDE/INSTALL) are now English; skill/entry triggers are bilingual (Korean phrases kept). Hook injections pick ko/en automatically from `team.locale` (existing teams unchanged).
- **Public hygiene**: fixtures and history fully anonymized; a CI guard (`tests/test_no_identity_leaks.py`) now blocks real-environment identifiers. Repo history was rewritten accordingly (fresh clones recommended).
- Codex `hooks.json` coexistence notice + spec contract; backlog moved to GitHub Issues (label `design`).

## [0.1.1] - 2026-07-06

### Added
- Agent one-liner entry point: paste the repo URL into Claude Code/Codex — README "For AI agents" gives agents a deterministic, approval-gated setup procedure (entry contract is now three-way: URL one-liner / clone-and-go / CLI).
- npm shim `npx tm-mode` (tag-pinned cli.py runner, zero deps) + npm OIDC publish job.

### Changed
- Setup wizard copy rewritten in English (calm, consistent tone); README is English-first with the Korean edition inline (home-anchor toggle).
- Codex placeholder MCP entries are now comments — a command-less real table bricked codex config loading (fatal "invalid transport"); existing brick tables self-heal on next sync.

### Fixed
- validation sync v2: safe deletion of upstream-removed files (blob-history + terminal-removal judgement, raw-copy backups).
- `[Y/n]` prompts now treat "no" as no.

## [Unreleased]

### Fixed

- Auto-committed session logs again publish through the bounded foreground path: a non-fast-forward rejection now reaches the existing fetch → `rebase --autostash` → re-push recovery instead of being stranded behind the plain-push-only worker. Failed publication remains non-blocking and records the detailed sync warning plus pending-worker fallback. Pending ledger compare-and-delete is now protected by a short OS lock so a concurrent writer's new nonce cannot be deleted.
- npm publication is disabled by default and now requires the explicit repository opt-in `NPM_PUBLISH_ENABLED=true`; PyPI patch tags therefore skip the npm job until package ownership and Trusted Publishing are deliberately enabled.
- `tm on`'s auto-update summary line no longer mixes languages: the `엔진 업데이트됨:`/`Engine updated:` prefix now follows team locale via `infra/i18n.py` (was hardcoded Korean, so it always prefixed the English-canonical `NOTICE.md`'s first bullet — mixed-language output for every team). ko-locale teams with a local `NOTICE.ko.md` now read their summary from it instead of the English `NOTICE.md`. Same treatment for the sibling `auto_update_on_start` lines (dirty-skip, validation-available, commit-failed). Audited the rest of the engine/hook/launcher runtime-string surface for the same class of bug — see the tracking note below for the larger, deferred cleanup.
- `tm-mode update`'s entire output (engine sync + validation sync, `_run_validation_sync`/`cmd_update` in `infra/teammode.py`) was unconditionally Korean regardless of team locale — an en-locale team reported an all-Korean transcript. All prose now routes through `infra/i18n.py`; git status diffs and the suggested `git commit -m '...'` command stay literal (not translatable content) in both languages.
- Codex's own hook `statusMessage` label (`[<team>] 팀모드 ON`, baked into `~/.codex/config.toml` at install/sync time) was hardcoded Korean regardless of team locale — a live Codex session showed `"Running SessionStart hook: [T-Gates] 팀모드 ON"` even for an en-locale team. The team-name portion (team-authored, from `team.config.json`) is untouched; only the fixed `팀모드 ON`/`Team Mode ON` product label now follows team locale. Note: this string is written once at `sync`/install time — teams that changed locale need to re-run `install.py`/`adapter.py sync` to pick up the new label.
- `write_introducer_config`'s default `greeting`/`farewell` values (written once at team creation) were hardcoded Korean even when the caller passed `locale="en_US"` — a brand-new en-locale team got a Korean default greeting baked into `team.config.json`. Defaults now follow the `locale` the function already receives; a team's own customized greeting/farewell (via `tm-customize`) is untouched, since this only affects the one-time default at creation. `_personality_customized`'s comparison (used by the onboarding gate) was updated to recognize *either* the ko or en default formula as "not customized," so an en-locale team isn't wrongly flagged as having customized their greeting just for using the (correct, English) default.
- `cmd_off`'s farewell fallback (`tm-mode off — 상태 저장됨`, shown when no `farewell` is configured) and its per-agent uninstall-failure `[warn]` line were hardcoded Korean; both now follow team locale. The `farewell` field itself (team-authored/customizable via `tm-customize`) is passed through unchanged in either language — only the product-owned fallback/warning text is a translation candidate.
- `cmd_on`'s agent-wiring-failure `[warn]` line and its three util-skill install `[warn]` lines (invalid/traversal-rejected, source missing, link failed) were hardcoded Korean; all now follow team locale. Known remaining gap: the traversal-rejected warning still interpolates a Korean detail from the shared `_validate_author()` validator (12+ call sites across the engine) — that validator's own i18n is out of scope for this branch and tracked with the rest of the long-tail cleanup.
- **i18n long-tail cleanup, continued** (closes out the remaining known gaps from the two entries above): all four shared path/name validators (`_validate_author`, `_validate_filename_chars`, `_validate_knowledge_path`, `_validate_route_path` — 13+ call sites) now take a `lang` parameter and route their return strings; `_resolve_member_fallback`'s own `[warn]` line (member auto-resolution failure) is fixed too. The rest of the engine's user-facing output now follows team locale as well: `cmd_log`, `cmd_pull`, `cmd_commit`, `cmd_context`, `cmd_issue`, `cmd_memory_unlock`, `cmd_util`, `cmd_knowledge` (memory write/delete — ~35 lines), `cmd_route`, and `main()`'s required-argument errors (`--author`/`--text`/`--message` missing, `--settings`/`--install` missing). The two earliest `main()` messages (no verb given; `--root` not given) are intentionally left hardcoded English rather than routed — at that point no `team_root` exists yet, so there is no `team.config.json` to resolve a locale from; treated like `install.py`'s English-by-default CLI-usage text instead.
  All four runtime hooks (`session-start.py`, `session-log-remind.py`, `confirm-action.py`, `auto-commit.py`) had the shared "TEAMMODE_HOME is not a valid team root" warning routed too; `session-start.py`'s reconcile-conflict/skip and push-pending-recovery messages are fixed, including the sync-warning *marker content* itself (previously Korean even for en-locale teams, since the marker gets read back later into the already-i18n'd `hook_ss_sync_warn` wrapper — a mixed-language line otherwise). `auto-commit.py` had no i18n scaffolding at all; added it from scratch, matching its siblings. **`push-worker.py`** (the detached async-push process) turned out to be a fourth, previously-missed writer of that same sync-warning marker — its non-fast-forward and drain-limit-reached marker strings were still hardcoded Korean; fixed with the same scaffolding (install.py's own marker writes were checked and are already language-neutral, no change needed).
  Both hook-manifest adapters' Codex/Claude `sync()` "unsupported event" `[warn]` line are now routed (previously only the Codex one was — the Claude adapter has an identical string with no grouping logic). The Codex adapter's version specifically had a subtler bug: its warn-spam suppression mechanism used to regex-parse the *rendered* Korean warning string to group repeated `(script, event)` pairs into a one-line summary — translating the string for en-locale teams would have silently broken that regex and the grouping would just stop collapsing duplicates with no test catching it. `sync()` was restructured to collect `(script, event, blocked)` as plain data first and render the grouped/singular message once, in the target locale, after grouping.
  **Known remaining gaps** (both `infra/agents/codex/adapter.py` and `infra/agents/claude/adapter.py`'s `sync()`, marked inline with `i18n backlog` comments): the MCP-role-slot-not-connected `[info]`, MCP-alias-not-guaranteed `[warn]`, matcher-not-expressible `[warn]`, and the `[sync] N hooks registered` / `[ok] no changes` summary lines are still hardcoded Korean in both adapters. Tracked for the next long-tail pass, not fixed in this branch.
- 팀 인스턴스에 제품 GitHub Actions workflow가 남지 않도록 전면 차단: ① 제품 workflow 전 job에 `if: github.repository == 'T-Gates/tm-mode'` 가드(템플릿 복사 직후 초기 커밋에서도 no-op), ② 모든 설치 경로(init/join/clone-and-go)가 지나는 install 관문에서 `.github/workflows` 제거+push(공용 `strip_template_workflows()` — dir/file/symlink 안전, timeout, push 실패 시 정직한 안내+재시도 복구), ③ sync pathspec denylist로 upstream 재유입 차단. 제품 repo/fork checkout(github.com origin이 `T-Gates/tm-mode` 또는 repo명 `tm-mode`)은 절대 건드리지 않음. 구버전 팀 repo의 `infra/git_ops.py`에 함수가 없어도 packaged CLI 폴백으로 동작.

### Added

- `tm-mode update [path]` launcher subcommand — thin passthrough to `infra/teammode.py update` (`--dry-run`/`--force` pass through; team root resolved from cwd only, no parent walk-up, same contract as `install.py`'s `_resolve_root`). Closes a gap where `infra/teammode.py` already told users to run `tm-mode update` (validation-sync guidance) but the launcher CLI had no such command. session-start hook also gained a read-only, no-fetch actionable notice (`tm-mode update`로 적용하세요) when the local `NOTICE.md` differs from upstream's — closes the "instance stays `on` forever, never told the engine fell behind" gap (`auto_update_on_start` only fires from `cmd_on`).
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

- 내장 정적 허용 폴더 1종 제거 — 특정 팀 전용 값의 제품 하드코딩(오염). 팀 전용 최상위 memory 폴더는 이제 `memory route upsert` 로 루트 INDEX 에 등재해야 `memory write/delete` 가 허용된다(#51). 거부 시 등록 명령 힌트를 stderr 로 안내.

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
