"""tm-mode i18n — stdlib 경량 메시지 카탈로그(dict). gettext 미사용(.mo 배치 부담).

Phase 1(2026-07): 골격 + 구조(비위저드) 메시지. 위저드 프롬프트는 배포 단계에서 확장.
fallback = en_US(1b 결정). 새 메시지는 두 언어 모두 채운다.

PR-i1(훅 주입물 i18n) 확장:
- team_lang(team_root) / team_lang_from_config(config): 팀 locale(team.config.json 의
  team.locale)을 훅 주입 언어 "ko"|"en" 으로 정규화. 폴백 계약(codex 문답 확정) —
  config 읽힘 + team.locale 없음 → ko(구팀 무변화), config 없음/파싱 실패/루트 invalid
  → en(제품 기본).
- hook_* 키: 훅이 주입하는 문자열의 **영어판**만 카탈로그에 둔다. 한국어 원문은 각
  훅 호출부의 리터럴이 단일 소스다(구팀 무변화 계약 — ko 출력은 신설 코드 경로를
  타지 않고 종전 문자열 그대로, i18n.py 부재(부분 배포) 시에도 ko 로 무해 강등).
  세션로그 규칙 본문의 ko/en 은 infra/hooks/_slog_rules.py 가 단일 소스(별도 유지).
"""
import json as _json
import os as _os

_DEFAULT = "en_US"

MESSAGES = {
    "en_US": {
        "done_installed":
            "[done] Install complete. Run `tm on` (or /tm) to turn team mode on.",
        "verify_ok":
            "[verify] Install verified OK — members={n} (team mode is off).",

        # ── 훅 주입물(PR-i1) — en 전용(ko 원문은 각 훅 호출부가 단일 소스) ──
        # session-start
        "hook_ss_header":
            "[teammode] Team mode active — session start context:",
        "hook_ss_sync_warn":
            "⚠️ [sync warning] Local commits have not been pushed to origin — "
            "risk of diverging from teammates. Check and resolve via "
            "`teammode pull`/manual cleanup: {warn}",
        "hook_ss_sync_status":
            "--- origin sync status (team-shared) --- ahead {ahead} / "
            "behind {behind}",
        "hook_ss_sync_ahead_suffix":
            " (local commits not pushed)",
        "hook_ss_engine_update_available":
            "[teammode] An engine update is available upstream — run `tm-mode update` to apply it.",
        "hook_ss_stale_home_warn":
            "[teammode] TEAMMODE_HOME is not a valid team root: {root} — "
            "if the repo moved/was renamed, update TEAMMODE_HOME in your shell profile",
        "hook_ss_reconcile_conflict_marker":
            "Session-start reconcile conflict (rebase abort) — manual cleanup needed: {detail}",
        "hook_ss_reconcile_conflict_print":
            "[teammode] Session reconcile failed: diverged from origin then hit a rebase "
            "conflict — manual cleanup needed. behind={behind} ahead={ahead}",
        "hook_ss_reconcile_skipped":
            "[teammode] Session reconcile skipped (non-fatal): {action} — {detail}",
        "hook_ss_pending_merge_conflict_marker":
            "Push-pending reconcile conflict (merge abort) — manual cleanup "
            "needed: {detail}",
        "hook_ss_pending_reconcile_failed_marker":
            "Automatic push-pending reconcile deferred — manual review needed: "
            "{action} — {detail}",
        "hook_ss_pending_reconcile_skipped":
            "[teammode] Automatic push-pending reconcile skipped (non-fatal): "
            "{action} — {detail}",
        "hook_ss_push_pending_no_upstream":
            "[teammode] A push is still pending but the remote can't be judged — "
            "restarting the worker (will use `push -u` if this is a new branch).",
        "hook_ss_push_worker_restart_failed":
            "[teammode] Worker restart failed — will retry on the next commit/session.",
        "hook_ss_push_pending_ahead":
            "[teammode] A previous session's push is still pending (ahead={ahead}) — "
            "restarting the worker.",
        "hook_ss_push_pending_rekick":
            "[teammode] A previous session's push is still pending — restarting "
            "the worker for its stored destination.",
        "hook_ss_push_pending_checkout_mismatch":
            "Push pending targets a different checkout; preserving it on the "
            "current branch: {targets}",
        "hook_ss_push_pending_checkout_mismatch_print":
            "[teammode] Preserved push pending for another checkout. Switch to "
            "that branch to retry: {targets}",
        # session-log-remind (UserPromptSubmit) — #45 pending-age 경고
        "hook_rm_push_pending_age":
            "[teammode] A push has been pending for {minutes} minutes — the worker "
            "may have been lost. It will retry on the next edit commit, and a "
            "session restart is guaranteed to retry it.",

        # push-worker.py (detach push process, #45) — sync-warning marker content.
        # These get read back later into hook_ss_sync_warn's {warn} slot, so they
        # must render in the target locale (same class of bug as the
        # session-start/auto-commit marker fixes).
        "push_worker_non_ff_marker":
            "push pending; non-fast-forward — delegated to session-start reconcile",
        "push_worker_drain_limit_marker":
            "push pending; worker drain limit reached — commit burst or repeated "
            "rewrite (session-start recovery will retry)",
        "push_worker_checkout_mismatch_marker":
            "push pending targets a different checkout; preserved on the current "
            "branch: {targets}",

        # auto-commit (PostToolUse/file_edit) — scaffolding added from scratch
        # (long-tail cluster). The commit message itself ("chore(teammode): ..."
        # is a git artifact and is NOT routed — already English, stays as-is.
        "hook_ac_push_worker_disabled":
            "[teammode] push-worker disabled (TEAMMODE_DISABLE_PUSH_WORKER) — "
            "push is delegated to session-start recovery.",
        "hook_ac_push_worker_start_failed":
            "[teammode] push-worker failed to start — the pending push will be "
            "retried at session start.",
        "hook_ac_prior_push_pending":
            "[teammode] A prior auto-commit's push is still pending — "
            "foreground publication will retry it; the worker remains fallback.",
        "hook_ac_prior_push_other_checkout":
            "[teammode] Preserving push pending for another checkout while "
            "publishing this edit separately: {targets}",
        "hook_ac_push_failed_marker":
            "Auto-commit push failed (commit preserved): {detail}",
        "hook_ac_push_failed_print":
            "[teammode] Auto-commit push failed — the commit was preserved and "
            "a pending retry was recorded: {detail}",
        "hook_ac_pending_write_failed_marker":
            "Could not safely update push-pending state; the commit was preserved, "
            "but automatic push recovery was not scheduled. Original push failure: {detail}",
        "hook_ac_pending_write_failed_print":
            "[teammode] Could not safely update push-pending state; the commit was "
            "preserved, but automatic push recovery was not scheduled. Original push failure: {detail}",
        "hook_ac_commit_deferred_marker":
            "Auto-commit deferred (changes remain uncommitted): {detail}",
        "hook_ac_commit_deferred_print":
            "[teammode] Auto-commit deferred — changes remain uncommitted: "
            "{detail}",

        # ── 엔진(teammode.py) 출력 — en 전용(ko 원문은 각 호출부 리터럴이 단일 소스,
        #    hook_* 와 동일 계약). auto_update_on_start(`tm on`) 이 찍는 줄들.
        "engine_auto_update_dirty_skip":
            "[auto-update] Uncommitted changes in the target paths — skipping the "
            "automatic update. Review it, then commit or revert to have it applied "
            "on the next `on`.",
        "engine_auto_update_validation_available":
            "Validation update available: {n_up} to update, {n_del} to delete — "
            "apply with `tm-mode update`.",
        "engine_auto_update_commit_failed":
            "[auto-update] Automatic commit failed (changes remain staged) — "
            "review and commit manually: {detail}",
        "engine_auto_update_engine_updated":
            "Engine updated: {summary}",
        "engine_auto_update_engine_updated_no_summary":
            "Engine updated",

        # ── `tm-mode update`(cmd_update + _run_validation_sync) 출력 — en 전용,
        #    같은 계약(ko 원문은 호출부 리터럴). 실사용자 리포트(전부 한국어 출력)로
        #    발견 — git status 글자(M/A/D)·경로·git commit -m '...' 커맨드 자체는
        #    번역 대상 아님(그대로 둠), 그 주변 설명문만 라우팅한다.
        "cmd_update_dirty_abort":
            "tm-mode update — aborted: {detail}.\n"
            "  The sync target(s) ({paths}) have uncommitted changes. Overwriting "
            "would lose them.\n"
            "  Commit or revert first, then run again (human judgment required).",
        "cmd_update_fetch_failed_skip":
            "tm-mode update — skipped (non-fatal): {detail}.\n"
            "  If the upstream remote is missing, install.py registers it. Manual "
            "registration:\n"
            "  git remote add {remote} {url}",
        "cmd_update_dry_run_will_change":
            "tm-mode update [dry-run] — files that would change on sync ({paths}):",
        "cmd_update_dry_run_excluded_util":
            "  excluded: infra/skills/util (instance-owned util skills — protected)",
        "cmd_update_dry_run_preview_only":
            "  (preview only — nothing changed. Re-run without --dry-run to apply.)",
        "cmd_update_dry_run_up_to_date":
            "tm-mode update [dry-run] — already up to date (no changes).",
        "cmd_update_engine_up_to_date":
            "tm-mode update — engine: already up to date.",
        "cmd_update_engine_sync_done":
            "tm-mode update — engine file sync complete ({paths}, staged). Changed files:",
        "cmd_update_review_and_commit_hint":
            "  Changes are staged (no automatic commit/push). Review, then commit "
            "directly:\n"
            "  git commit -m 'chore: sync teammode engine from upstream'",
        "cmd_update_validation_shallow_skip":
            "tm-mode update — validation: skipped, shallow clone (engine sync is fine).",
        "cmd_update_validation_dry_run_targets":
            "tm-mode update [dry-run] — validation sync targets ({n_safe}):",
        "cmd_update_validation_dry_run_none":
            "tm-mode update [dry-run] — validation: nothing to update.",
        "cmd_update_validation_dry_run_delete_candidates":
            "tm-mode update [dry-run] — validation delete candidates ({n_del}) "
            "(staged delete after backup):",
        "cmd_update_validation_dry_run_skip_preserved":
            "  preserved (skip) ({n_skip}) — local modification/instance-only:",
        "cmd_update_validation_applied_updated":
            "{n} updated",
        "cmd_update_validation_applied_deleted":
            "{n} deleted",
        "cmd_update_validation_apply_done":
            "tm-mode update — validation sync complete: {parts} (staged).",
        "cmd_update_validation_forced_overwrite":
            " force-overwrote {n}",
        "cmd_update_validation_backup":
            " backup: {path}",
        "cmd_update_validation_restore_hint":
            " (restore: git apply '<backup>/restore.patch' or copy from files/)",
        "cmd_update_validation_apply_failed":
            "tm-mode update — validation apply failed (non-fatal): {detail}",
        "cmd_update_validation_up_to_date":
            "tm-mode update — validation: already up to date (nothing to update).",
        "cmd_update_validation_skip_same_as_before":
            "  validation preserved (skip) ({n}) — unchanged since last time.",
        "cmd_update_validation_skip_preserved_detail":
            "  validation preserved (skip) ({n}) — local modification/instance-only, "
            "not overwritten (see the full list with --dry-run, force with --force):",

        "adapter_codex_status_team_mode_on":
            "Team Mode ON",

        # ── infra/agents/codex/adapter.py 의 sync() 경고(long tail, task 4) ──
        "adapter_codex_home_unpinnable_warn":
            "[warn] TEAMMODE_HOME could not be pinned into the hook command "
            "because the team root path has a newline character (falling back "
            "to shell profile): {home!r}",
        "adapter_codex_event_unsupported":
            "[warn] {script}: {agent} does not support event {event} — disabled{extra}",
        "adapter_codex_event_unsupported_block_lost":
            " (block enforcement lost)",
        "adapter_codex_event_unsupported_grouped":
            "[warn] {script}: {agent} does not support {event} for {n} entries — "
            "disabled{extra}",
        "adapter_codex_event_unsupported_grouped_block_lost":
            " — block enforcement disabled",

        # ── infra/agents/claude/adapter.py 의 sync() 경고 (B 지적 — codex 형제와
        #    동형이지만 grouping 메커니즘은 없음, 단순 치환) ──
        "adapter_claude_event_unsupported":
            "[warn] {script}: {agent} does not support event {event} — disabled",

        # ── install_lib.write_introducer_config 의 greeting/farewell **기본값**
        # (§4.4·부록 A.3) — 신규 팀 생성 시점의 locale 을 따른다. 팀이 tm-customize
        # 로 이미 바꾼 뒤에는 팀 커스텀 텍스트가 되어 이 카탈로그와 무관해진다.
        "install_default_greeting":
            "{name} Team Mode ON",
        "install_default_farewell":
            "Great work today — {name}",

        # ── cmd_off 출력(엔진) — "ON" 라벨(adapter_codex_status_team_mode_on)의
        #    OFF 대칭짝. farewell 자체(팀 커스텀 필드)는 이 카탈로그와 무관 — 폴백/
        #    경고 문구만 라우팅한다.
        "cmd_off_agent_uninstall_failed":
            "[warn] {agent} agent uninstall failed → skipped: {err}",
        "cmd_off_no_farewell_fallback":
            "tm-mode off — state saved",

        # ── cmd_on 출력(엔진) — greeting(팀 커스텀 필드)은 그대로 출력(번역 금지),
        #    배선/util 스킬 실패 [warn] 만 제품 고정 어휘라 라우팅한다.
        "cmd_on_agent_wiring_failed":
            "[warn] {agent} agent wiring failed → skipped: {err}",
        "cmd_on_util_skill_invalid":
            "[warn] util skill '{skill}' invalid (traversal risk) → skipped: {err}",
        "cmd_on_util_skill_missing":
            "[warn] util skill '{skill}' source missing → skipped",
        "cmd_on_util_skill_link_failed":
            "[warn] util skill '{skill}' link failed ({dir}) → skipped: {err}",

        # ── 엔진 long tail(#104 후속) — cmd_log/cmd_pull/cmd_commit ──
        "cmd_log_deprecated":
            "[deprecated] Instead of the `log` verb, write the session log "
            "directly via Read (tail offset)+Edit (saves context, keeps "
            "fidelity). This verb is kept for backward compatibility only.",
        "cmd_log_path_escape":
            "[error] The log path escapes the sessions directory.",
        "cmd_log_recorded":
            "tm-mode log — recorded {author}/{date}.md",
        "cmd_pull_updated":
            "tm-mode pull — updated: {detail}",
        "cmd_pull_skipped":
            "tm-mode pull — skipped (non-fatal): {detail}",
        "cmd_commit_push_failed_suffix":
            " (push failed — commit preserved)",
        "cmd_commit_push_pending_marker":
            "tm-mode commit push failed (commit preserved): {detail}",
        "cmd_commit_pending_write_failed_marker":
            "Could not safely update push-pending state; the commit was preserved, "
            "but automatic push recovery was not scheduled. Original push failure: {detail}",
        "cmd_commit_pending_write_failed":
            "[warning] tm-mode commit: could not safely update push-pending "
            "state; the commit was preserved, but automatic push recovery was "
            "not scheduled. Original push failure: {detail}",
        "cmd_commit_done":
            "tm-mode commit — committed{suffix}: {detail}",
        "cmd_commit_skipped":
            "tm-mode commit — skipped (non-fatal): {detail}",

        # ── cmd_context ──
        "cmd_context_no_index":
            "(no INDEX.md)",
        "cmd_context_members_header":
            "--- members (most recent work-day log summary per member) ---",
        "cmd_context_no_summary":
            "(no summary — legacy log)",
        "cmd_context_no_logs":
            "(no session logs — nothing to summarize)",

        # ── cmd_issue ──
        "cmd_issue_slot_not_connected":
            "[info] The issues slot is not connected. Connect "
            "services.issues in team.config.json (tm-connect).",

        # ── cmd_memory_unlock ──
        "cmd_memory_unlock_bad_subaction":
            "[error] memory unlock: requires a begin or end sub-action — "
            "usage: teammode.py memory unlock {begin|end} --root <team-root>",
        "cmd_memory_unlock_guard_load_failed":
            "[error] memory unlock: could not load infra/hooks/kb-write-guard.py "
            "(single source for the flag path convention).",
        "cmd_memory_unlock_no_session_id":
            "[error] memory unlock: could not determine the session id — no "
            "CLAUDE_SESSION_ID/CLAUDE_CODE_SESSION_ID env and no SessionStart "
            "relay file. Run this from within an agent session.",
        "cmd_memory_unlock_begin_write_failed":
            "[error] memory unlock begin: failed to create the flag — {exc}",
        "cmd_memory_unlock_begin_done":
            "teammode memory unlock begin — edit window open (session={session_id}, "
            "source={source}, TTL {ttl}s): {flag}",
        "cmd_memory_unlock_end_remove_failed":
            "[error] memory unlock end: failed to remove the flag — {exc}",
        "cmd_memory_unlock_end_done":
            "teammode memory unlock end — edit window closed (session={session_id}): {flag}",

        # ── cmd_util ──
        "cmd_util_member_required":
            "[error] util {action}: --member <name> is required.",
        "cmd_util_skill_required":
            "[error] util {action}: --skill <skill-name> is required.",
        "cmd_util_add_skill_not_found":
            "[error] util add: '{skill}' is not an existing util skill.",
        "cmd_util_add_containment_rejected":
            "[error] util add: '{skill}' source path points outside the util "
            "directory (containment rejected).",
        "cmd_util_immediate_apply_skip":
            "teammode util {action} — immediate apply skipped "
            "(needs --settings or --install; applies on the next `on`)",
        "cmd_util_add_registered":
            "teammode util add — {skill} registered (member: {member})",
        "cmd_util_remove_removed":
            "teammode util remove — {skill} removed (member: {member})",
        "cmd_util_unknown_action":
            "[error] util: unknown action: {action!r}. One of list/add/remove.",

        # ── cmd_knowledge (memory write/delete) ──
        "cmd_memory_write_folder_required":
            "[error] memory write: --folder is required.",
        "cmd_memory_write_filename_required":
            "[error] memory write: --filename is required.",
        "cmd_memory_write_content_required":
            "[error] memory write: --content is required.",
        "cmd_memory_write_author_required":
            "[error] memory write: --author is required.",
        "cmd_memory_write_weight_required":
            "[error] memory write: --weight is required (no guessing).",
        "cmd_memory_write_weight_invalid":
            "[error] memory write: --weight must be one of {valid}: {weight!r}",
        "cmd_memory_write_content_bad_char_surrogate":
            "[error] memory write: --content has a disallowed character "
            "(surrogate U+{cp:04X}). Control/format/surrogate characters are rejected.",
        "cmd_memory_write_content_bad_char":
            "[error] memory write: --content has a disallowed character "
            "(U+{cp:04X}, category={cat}). Control/format/surrogate characters "
            "are rejected.",
        "cmd_memory_write_no_change":
            "teammode memory write — no change (idempotent): {folder}/{filename}",
        "cmd_memory_write_file_write_failed":
            "[error] memory write: file write failed — {exc}",
        "cmd_memory_write_index_and_rollback_failed":
            "[error] memory write: INDEX update failed AND file rollback also "
            "failed — INDEX: {exc} / rollback: {rb_exc}",
        "cmd_memory_write_index_failed_rolled_back":
            "[error] memory write: INDEX update failed (file rolled back) — {exc}",
        "cmd_memory_route_not_registered_hint":
            "[hint] '{top}' is not registered in the root INDEX — register: {cmd}",
        "cmd_memory_write_commit_failed":
            "[warning] memory write: commit failed — {detail}",
        "cmd_memory_write_done_not_committed":
            "teammode memory write — {folder}/{filename} done (not committed)",
        "cmd_memory_write_push_failed":
            "[warning] memory write: push failed (local commit preserved) — {detail}",
        "cmd_memory_write_done":
            "teammode memory write — {folder}/{filename} done",
        "cmd_memory_delete_path_required":
            "[error] memory delete: --path <memory/relative-path> is required.",
        "cmd_memory_delete_author_required":
            "[error] memory delete: --author is required.",
        "cmd_memory_delete_dotdot_forbidden":
            "[error] memory delete: the path cannot contain '..': {path!r}",
        "cmd_memory_delete_symlink_escape":
            "[error] memory delete: memory/ points outside team_root "
            "(symlink escape blocked)",
        "cmd_memory_delete_bad_path_chars":
            "[error] memory delete: the path has a disallowed character — {exc}",
        "cmd_memory_delete_index_md_forbidden":
            "[error] memory delete: INDEX.md cannot be deleted directly: {path!r}",
        "cmd_memory_delete_filename_invalid":
            "[error] memory delete: --path filename validation failed — {err}",
        "cmd_memory_delete_folder_required":
            "[error] memory delete: only files under an allowed folder can be "
            "deleted. Allowed: {allowed}",
        "cmd_memory_delete_folder_blocked":
            "[error] memory delete: folder '{folder}' is not a deletion target "
            "(hook/tm-context-managed path)",
        "cmd_memory_delete_folder_not_allowed":
            "[error] memory delete: folder '{folder}' is not allowed. Allowed: "
            "{allowed} (and top-level folders registered in the root INDEX)\n"
            "[hint] register first: {cmd}",
        "cmd_memory_delete_folder_segment_invalid":
            "[error] memory delete: folder segment not allowed: {seg!r} in {folder!r}",
        "cmd_memory_delete_path_escapes":
            "[error] memory delete: the path escapes memory/: {path!r}",
        "cmd_memory_delete_file_absent":
            "teammode memory delete — file absent (idempotent): {path}",
        "cmd_memory_delete_stat_failed":
            "[error] memory delete: failed to check file status — {exc}",
        "cmd_memory_delete_index_update_failed":
            "[error] memory delete: INDEX update failed — {exc}",
        "cmd_memory_delete_unlink_and_rollback_failed":
            "[error] memory delete: file deletion failed AND INDEX rollback also "
            "failed — unlink: {exc} / rollback: {rb_exc}",
        "cmd_memory_delete_unlink_failed_rolled_back":
            "[error] memory delete: file deletion failed (INDEX rolled back) — {exc}",
        "cmd_memory_delete_commit_failed":
            "[warning] memory delete: commit failed — {detail}",
        "cmd_memory_delete_done_not_committed":
            "teammode memory delete — {path} deleted (not committed)",
        "cmd_memory_delete_push_failed":
            "[warning] memory delete: push failed (local commit preserved) — {detail}",
        "cmd_memory_delete_done":
            "teammode memory delete — {path} deleted",
        "cmd_memory_unknown_action":
            "[error] memory: unknown action: {action!r}. One of write/delete.",

        # ── cmd_route (memory route upsert/remove) ──
        "cmd_route_upsert_path_required":
            "[error] memory route upsert: --path is required.",
        "cmd_route_upsert_desc_required":
            "[error] memory route upsert: --desc is required (no guessing the "
            "2-column description).",
        "cmd_route_upsert_author_required":
            "[error] memory route upsert: --author is required.",
        "cmd_route_upsert_index_update_failed":
            "[error] memory route upsert: INDEX update failed — {exc}",
        "cmd_route_upsert_no_change":
            "teammode memory route upsert — no change (idempotent): {path}",
        "cmd_route_upsert_done":
            "teammode memory route upsert — {path} registered",
        "cmd_route_remove_path_required":
            "[error] memory route remove: --path is required.",
        "cmd_route_remove_author_required":
            "[error] memory route remove: --author is required.",
        "cmd_route_remove_index_update_failed":
            "[error] memory route remove: INDEX update failed — {exc}",
        "cmd_route_remove_no_row":
            "teammode memory route remove — no such row (idempotent): {path}",
        "cmd_route_remove_done":
            "teammode memory route remove — {path} removed",
        "cmd_route_unknown_subaction":
            "[error] memory route: unknown sub-action: {sub!r}. One of upsert/remove.",
        "cmd_route_commit_failed":
            "[warning] memory route: commit failed — {detail}",
        "cmd_route_not_committed_suffix":
            " (not committed)",
        "cmd_route_push_failed":
            "[warning] memory route: push failed (local commit preserved) — {detail}",

        # ── main() required-arg errors (usage/--root not-given messages stay
        #    hardcoded English at the call site — no team_root exists yet to
        #    resolve a locale from, so there's nothing to route) ──
        "main_log_author_required":
            "[error] log: --author <name> is required.",
        "main_log_text_required":
            "[error] log: --text <content> is required.",
        "main_commit_message_required":
            "[error] commit: --message <message> is required.",
        "main_settings_or_install_required":
            "[error] one of --settings <path> (isolated mode) or --install "
            "(real install) is required. Without an explicit choice, this "
            "will not write to the real ~/.claude/settings.json.",

        # ── shared validators (_validate_author/_validate_filename_chars/
        #    _validate_knowledge_path/_validate_route_path) — 13+ call sites across
        #    the engine. lang threaded directly into each (see breadcrumb comment
        #    at each definition in teammode.py for the design rationale). ──
        "validate_author_empty":
            "author is empty.",
        "validate_author_path_sep":
            "author cannot contain a path separator: {author!r}",
        "validate_author_dot_segment":
            "author cannot be {author!r}.",
        "validate_author_absolute":
            "author cannot be an absolute path: {author!r}",
        "validate_author_leading_char":
            "author must start with an alphanumeric character: {author!r}",
        "validate_author_non_ascii":
            "author must use ASCII characters only (letters/digits/allowed "
            "symbols): {author!r}",
        "validate_author_bad_char":
            "author has a disallowed character: {author!r}",
        "validate_filename_empty":
            "filename is empty.",
        "validate_filename_path_sep":
            "filename cannot contain a path separator: {filename!r}",
        "validate_filename_not_allowed":
            "filename is not allowed: {filename!r}",
        "validate_filename_bad_char":
            "filename has a disallowed character: {filename!r}",
        "validate_filename_non_ascii":
            "filename must use ASCII characters only: {filename!r}",
        "validate_filename_kebab_failed":
            "filename validation failed: {err}",
        "validate_knowledge_symlink_escape":
            "memory/ points outside team_root (symlink escape blocked): "
            "{memory_dir} not under {real_root}",
        "validate_knowledge_folder_blocked":
            "folder '{folder}' is not a memory storage target (hook/tm-context-"
            "managed path): blocked list: {blocked}",
        "validate_knowledge_folder_not_allowed":
            "folder '{folder}' is not allowed. Allowed: {allowed} (and their "
            "subfolders, or a top-level folder registered in the root INDEX "
            "routing map)",
        "validate_knowledge_register_hint":
            "\n[hint] register first: {cmd}",
        "validate_knowledge_folder_bad_segment":
            "folder has a disallowed segment: {seg!r} in {folder!r}",
        "validate_knowledge_folder_non_ascii":
            "folder segment must use ASCII characters only: {seg!r}",
        "validate_knowledge_folder_bad_char":
            "folder segment has a disallowed character: {seg!r}",
        "validate_knowledge_index_md_reserved":
            "INDEX.md is managed by the engine — it cannot be written via "
            "memory write.",
        "validate_knowledge_path_escapes":
            "the path escapes memory/: {folder}/{filename}",
        "validate_route_path_empty":
            "the path is empty.",
        "validate_route_path_absolute":
            "an absolute path is not allowed: {path!r}",
        "validate_route_path_dotdot":
            "the path cannot contain '..': {path!r}",
        "validate_route_path_bad_char":
            "the path has a disallowed character: {path!r}",
        "validate_route_symlink_escape":
            "memory/ points outside team_root (symlink escape blocked).",
        "validate_route_normalize_failed":
            "path normalization failed — {exc}",
        "validate_route_path_escapes":
            "the path escapes memory/: {path!r}",
        "resolve_member_fallback_warn":
            "[warn] Automatic member resolution failed (neither the "
            "TEAMMODE_MEMBER env var nor claude settings.json had it) — a "
            "codex hook with no existing prefix will not get a member recorded. "
            "You can set it explicitly with `tm on --member <name>`.",

        "hook_ss_index_header":
            "--- Team memory INDEX ---",
        "hook_ss_members_header":
            "--- Recent work by member (summary) ---",
        "hook_ss_no_summary":
            "(no summary — legacy log)",
        "hook_ss_no_logs":
            "(No session logs yet — start recording in "
            "memory/team/sessions/<name>/ from your first task.)",

        # session-log-remind
        "hook_rm_time_line":
            "[teammode] Current time: {date}({weekday}) {time} KST",
        "hook_rm_base_guide":
            " Manage your session log directly in the team root's "
            "memory/team/sessions/<name>/ via Read (tail offset)+Edit "
            "(do not use log verbs — saves context, keeps fidelity). "
            "Your own session log is exempt from the guard, so beyond appending "
            "you can also edit, restructure, and update its summary directly. "
            "<name> is your English name in members.md (not your OS username). "
            "One file per day (YYYY-MM-DD.md; no split files like -late), "
            "frontmatter (author/date/summary) required. "
            "Dates follow the 06:00 cutoff — if the time above is 00:00-05:59 "
            "use yesterday's file, from 06:00 on use today's. "
            "Do not write to ./memory/ of the current working repo. "
            "Capture not just what you did but the reasoning, discarded "
            "alternatives, blockers, and next steps in one flow. "
            "For routine additions Read only the last 20 lines; full Read only "
            "for major restructuring or summary updates. "
            "Team work only — no personal content.",
        "hook_rm_kit_new":
            " The session log file does not exist yet — create it with "
            "Write({p}, ...) containing frontmatter (author/date/summary) plus "
            "the first entry, without Read.",
        "hook_rm_kit_append":
            " To append: Read({p}, offset={off}, limit=25) to read only the "
            "tail, then add with Edit. If the summary (frontmatter) needs "
            "updating, also Read({p}, offset=1, limit=6). "
            "No log verbs or full-file Read — last 20 lines only.",
        "hook_rm_compact_new":
            "{p} missing: create it with Write containing frontmatter "
            "(author/date/summary) + the first entry",
        "hook_rm_compact_append":
            "{p}: read only the tail (Read offset={off}, limit=25) and append "
            "with Edit",
        "hook_rm_fallback_action":
            "Read only the tail of today's file in memory/team/sessions/<name>/ "
            "and append with Edit (<name> is your English name in members.md)",
        "hook_rm_compact_body":
            "Session log not written — {count} prompts so far. {action}. {ref}",
        "hook_rm_strong_full_head":
            "⛔ Session log not updated for 30+ minutes ({count} prompts "
            "without a session log update). As your first action:",
        "hook_rm_strong_compact":
            "⛔ Session log not updated for 30+ minutes — {body}",
        "hook_rm_weak_full_head":
            "Session log not written — {count} prompts so far.",
        "hook_rm_sys_strong":
            "⛔ Session log not written — {count} prompts so far. "
            "Record it as your first action",
        "hook_rm_sys_weak":
            "📝 Session log not written — {count} prompts so far",

        # kb-write-guard
        "hook_kb_deny_parse":
            "Failed to parse hook input — blocking conservatively (fail-closed).",
        "hook_kb_deny_not_dict":
            "Hook input is not a JSON object (dict) — blocking conservatively "
            "(fail-closed).",
        "hook_kb_deny_files_not_list":
            "Malformed input — the files field is not a list (fail-closed). "
            "Retry with the canonical schema or use the tm-manage-memory skill.",
        "hook_kb_deny_files_item":
            "Malformed input — a files element is not a string (fail-closed). "
            "Retry with the canonical schema or use the tm-manage-memory skill.",
        "hook_kb_deny_raw_not_dict":
            "Malformed input — the raw field is not a dict (fail-closed). "
            "Retry with the canonical schema or use the tm-manage-memory skill.",
        "hook_kb_deny_tool_input_not_dict":
            "Malformed input — raw.tool_input is not a dict (fail-closed). "
            "Retry with the canonical schema or use the tm-manage-memory skill.",
        "hook_kb_deny_no_path":
            "Could not determine the memory/ path — blocking conservatively "
            "(fail-closed). Retry with a canonical schema that includes the "
            "file path, or use the tm-manage-memory skill.",
        "hook_kb_deny_resolve_error":
            "Error while checking the memory/ path — blocking conservatively "
            "(fail-closed). Use the tm-manage-memory skill.",
        "hook_kb_deny_direct_edit":
            "Direct edits under memory/ are not allowed. "
            "The KB (memory base) follows the 'verbs only' principle — instead "
            "of direct Edit/Write, engine verbs record to shared team memory "
            "without conflicts. "
            "Add, update, or delete memory only through the tm-manage-memory "
            "skill (engine: python infra/teammode.py memory write …).",
        "hook_kb_stderr_blocked":
            "[teammode] KB write blocked: {reason}",
        "hook_edit_lease_deny_busy":
            "The file edit was not started because another session is changing "
            "the shared checkout. Retry shortly: {detail}",
        "hook_edit_lease_deny_unavailable":
            "The edit synchronization module could not be loaded, so the file "
            "edit was blocked conservatively. Retry after resynchronizing hooks.",
        "hook_edit_lease_deny_identity":
            "The hook payload has no exact session/tool identity, so safe automatic "
            "reconciliation cannot be guaranteed. Resynchronize the agent hooks "
            "and retry.",
        "hook_edit_lease_stderr_blocked":
            "[teammode] File edit deferred: {reason}",

        # confirm-action
        "hook_ca_deny_manifest":
            "Manifest load failed — blocking for safety (fail-closed). "
            "Check manifest.json.",
        "hook_ca_deny_marker_mismatch":
            "Marker mismatch — argv={argv} != manifest={manifest} "
            "(suspected miswiring; fail-closed block).",
        "hook_ca_deny_confirm":
            "{server}/{name} requires human confirmation "
            "(teammode confirm-action). If this action is intended, approve it "
            "explicitly and retry.",
        "hook_ca_stderr_blocked":
            "[teammode] Blocked: {reason}",
    },
    "ko_KR": {
        "done_installed":
            "[done] 설치 완료. 팀모드를 켜려면 `tm on`(또는 /tm) 하세요.",
        "verify_ok":
            "[verify] 설치 검증 OK — members={n} (팀모드는 꺼둠).",
    },
}


def resolve_lang(locale=None) -> str:
    """로캘 문자열(예 'ko_KR','en_US.UTF-8','ko')을 카탈로그 키로. 미지원 시 en_US."""
    if locale:
        base = str(locale).split(".")[0].split("@")[0].strip()
        if base in MESSAGES:
            return base
        lang = base.split("_")[0].lower()
        for key in MESSAGES:
            if key.split("_")[0].lower() == lang:
                return key
    return _DEFAULT


def team_lang_from_config(config) -> str:
    """파싱된 team.config.json(dict) → 훅 주입 언어 "ko"|"en" (PR-i1 계약).

    - config 가 dict 아님(없음/파싱 실패/루트 invalid 를 호출부가 None 등으로 전달)
      → "en" (제품 기본)
    - team.locale 없음/빈 값 → "ko" (locale 필드가 없던 구팀 무변화 계약)
    - locale 정규화: ko* → "ko", 그 외 전부 → "en"
    """
    if not isinstance(config, dict):
        return "en"
    team = config.get("team")
    locale = team.get("locale") if isinstance(team, dict) else None
    if locale is None or (isinstance(locale, str) and not locale.strip()):
        return "ko"
    return "ko" if str(locale).strip().lower().startswith("ko") else "en"


def team_lang(team_root) -> str:
    """팀 루트의 team.config.json 에서 훅 주입 언어("ko"|"en")를 읽는다.

    config 없음/파싱 실패/루트가 JSON object 아님 → "en".
    config 는 읽히는데 team.locale 없음 → "ko" (team_lang_from_config 계약).
    런타임 훅용 — env 를 보지 않고 **명시된 team_root** 만 읽는다.
    """
    try:
        path = _os.path.join(str(team_root), "team.config.json")
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, ValueError):
        return "en"
    if not isinstance(data, dict):
        return "en"
    return team_lang_from_config(data)


def t(key, lang=None, **fmt) -> str:
    """키→현지화 문자열. lang 미지원/키 없음이면 en_US→키 원문 폴백. **fmt 로 포맷."""
    catalog = MESSAGES.get(resolve_lang(lang), MESSAGES[_DEFAULT])
    template = catalog.get(key, MESSAGES[_DEFAULT].get(key, key))
    try:
        return template.format(**fmt) if fmt else template
    except (KeyError, IndexError):
        return template
