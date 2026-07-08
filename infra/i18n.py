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
