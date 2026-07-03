#!/usr/bin/env python3
"""Codex CLI 어댑터 — 설치 시점 번역기 (스펙 02 §5).

정규형 manifest + codex/events.json 을 읽어 Codex 의 config.toml hooks 블록에
멱등 동기화한다. 번역 코어(events.json 기반, 에이전트 무관)는 Claude 어댑터와
공유하고, 이 파일은 **Codex 고유의 config 포맷(TOML 블록) + 폴백 처리**만 담당한다.

Codex 특성(events.json 으로 데이터 표현 — 코드 분기 하드코딩 금지, §4):
  - PreToolUse/PostToolUse/UserPromptSubmit/SessionStart 를 Codex hooks 에 직접 등록
  - actions.file_edit = "apply_patch"
  - mcp_tool_format = "mcp__{server}__{tool}"

폴백(§11.11): events.json 에서 표현 불가로 선언된 훅은 비활성화하고 [warn] 으로 알린다.
현재 Codex 는 PreToolUse 를 지원하므로 confirm-action/kb-write-guard 같은 차단 훅도
등록 대상이다.

CLI:
  adapter.py sync [--on|--off]   manifest → config.toml (멱등)
  adapter.py uninstall           teammode 블록 제거
"""
from __future__ import annotations

import argparse
import os
import re
import runpy
import shlex
import sys
from pathlib import Path
from typing import Optional


# 번역 코어를 Claude 어댑터에서 재사용 (events.json 기반이라 에이전트 무관)
_CLAUDE_ADAPTER = (Path(__file__).resolve().parents[1] / "claude" / "adapter.py")
_claude_mod = runpy.run_path(str(_CLAUDE_ADAPTER), run_name="__codex_base__")
BaseAdapter = _claude_mod["Adapter"]
_SEALED = _claude_mod["_SEALED"]  # MCP 등록 파일 봉인 센티넬(N3)

# stdout UTF-8 보장 — sync() 가 한글 [warn]/[ok] print. install.py 디스패치(in-process)
# 는 install.py main 이 이미 보정하나, `python adapter.py sync` 직접 실행 시 cp949 콘솔
# 크래시 방어(일관·방어). infra 미발견 시 no-op(다른 훅과 동일 가드 패턴).
_INFRA_DIR = Path(__file__).resolve().parents[2]
if str(_INFRA_DIR) not in sys.path:
    sys.path.insert(0, str(_INFRA_DIR))
try:
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 어댑터는 동작(보정만 스킵)
        return

BLOCK_START = "# teammode-hooks-start"
BLOCK_END = "# teammode-hooks-end"

# issue #41 R1 — legacy/어긋난 마커 자기치유용 패턴들.
# _MARKER_LINE: 모든 teammode 마커 변형(`# teammode-<이름>-start|end`)을 라인 단위로 인식.
#   라인 선두 앵커라 TOML 문자열 값 안의 마커-모양 부분문자열(command = '...')은 안 잡는다.
_MARKER_LINE = re.compile(r"^\s*#\s*teammode-([A-Za-z0-9_]+)-(start|end)\s*$")
# _HOOKS_TABLE: teammode 훅 블록 본문을 이루는 [[hooks.*]] 테이블 헤더(서브테이블 포함).
_HOOKS_TABLE = re.compile(r"^\s*\[\[hooks\.")
# _EVENT_TABLE: 이벤트 단위 테이블 헤더([[hooks.<Event>]] — 서브테이블 [[hooks.X.hooks]] 제외).
_EVENT_TABLE = re.compile(r"^\s*\[\[hooks\.[A-Za-z0-9_-]+\]\]\s*$")
# _ANY_SECTION: 임의 TOML 섹션/테이블 헤더(고아 start 전방 스캔의 정지 경계 판정용).
_ANY_SECTION = re.compile(r"^\s*\[")
# _COMMAND_LINE: 훅 테이블의 command 한 줄 문자열 값(managed SHAPE 증명 P2-3 입력).
_COMMAND_LINE = re.compile(r"""^\s*command\s*=\s*(['"])(.*)\1\s*$""")


class Adapter(BaseAdapter):
    """Codex 어댑터 — 번역 코어는 상속, config 포맷·폴백만 재정의."""

    # install-skills(L2-C): Codex 스킬 경로는 spec 에 명문화돼 있지 않다 — claude 의
    # ~/.claude/skills 와 대칭으로 ~/.codex/skills 를 가정한다(주석으로 추정 명시).
    # 실 경로가 다르면 v0.2 에서 이 상수만 고치면 된다(install_skills 로직은 부모 상속).
    DEFAULT_SKILLS_DIR = "~/.codex/skills"

    def __init__(self, *args, **kwargs):
        # member: Codex hook command 에 TEAMMODE_MEMBER 를 prefix 로 박기 위한 멤버명.
        # Claude 는 settings.json env(inject_member_env_settings)로 닿지만, Codex 의 command
        # hook 에는 env 필드가 없다(공식 hooks 문서). 대신 Codex 는 command 를 셸로 실행하므로
        # (문서가 command 에 `$(...)` 명령치환 예시를 보임 — 셸 경유 근거) build_command 가
        # `env VAR=val <command>` prefix 로 안전 전달한다. None 이면 prefix 없음(하위호환).
        self.member = kwargs.pop("member", None)
        # member_fallback(issue #41 R2): 엔진(teammode.py)의 자동 해석 체인
        # (env TEAMMODE_MEMBER → claude settings.json env)이 넘기는 폴백 멤버명.
        # 우선순위는 명시 self.member > 기존 config prefix(자가치유) > member_fallback —
        # 이미 박힌 prefix 가 per-agent 최고 충실도라 폴백이 그걸 덮지 않는다.
        self.member_fallback = kwargs.pop("member_fallback", None)
        # N3: Codex 는 MCP 를 ~/.codex/config.toml 의 [mcp_servers.*] 블록으로 등록하므로
        # 부모(claude)가 상속시키는 mcp_config_path(=~/.claude.json) 를 절대 쓰지 않는다.
        # 상속된 실경로가 latent footgun 으로 새지 않게 봉인 — 부모 _read_mcp_config/
        # install_mcp(claude.json 경로)를 잘못 호출하면 무동작/NotImplementedError 로 막힌다.
        # (codex 는 _read_mcp_servers·install_mcp 를 config.toml 기반으로 전부 재정의함.)
        kwargs["mcp_config_path"] = _SEALED
        super().__init__(*args, **kwargs)

    def build_command(self, entry: dict) -> str:
        """기본 command 에 TEAMMODE_MEMBER·TEAMMODE_HOME env prefix 를 붙인다(Codex hook 전용).

        Codex 는 hook command 를 셸로 실행하고(공식 hooks 문서가 command 에 `$(...)` 명령
        치환 예시를 보이는 것이 근거) command hook 에 env 필드가 없으므로, 멀티멤버 팀에서
        '나'를 가르는 TEAMMODE_MEMBER(session-log-remind·kb-write-guard 의 단일 소스)를
        `env VAR=val <command>` prefix 로 전달한다. member 가 self.member·기존 prefix 둘 다
        없거나 형식이 이상하면 member 는 생략한다(하위호환·fail-safe).
        self.member 가 None 이면 현재 config.toml 에 박힌 기존 prefix 를 재사용한다(self-healing,
        `_existing_member_prefix`) — member 없이 도는 `tm on/off` resync 가 prefix 를 떨구지 않게. 값은 ascii 영숫자로 시작하는
        '-_' 단일 토큰만 허용 — command 가 셸로 실행되므로 공백/메타문자 토큰은 인젝션 위험이
        있어 거부한다(session-log-remind `_valid_member_name` 과 동일 규칙).

        TEAMMODE_HOME(issue #9b): 훅은 env/cwd 만 읽으므로(`__file__` 폴백 없음 — #28 의
        제외 사유는 오판) 셸 프로파일만으로는 셸 종류·스냅샷 스테일에 종속된다. 여기서
        같은 prefix 채널에 핀한다. member 와 달리 값이 자유 문자열(경로)이므로
        **shlex.quote 로 쿼팅**해 공백/따옴표/비ASCII 경로를 셸에 정확히 전달한다.
        self-healing 파서는 불필요 — 값은 생성자가 항상 받는 self.team_root 에서 sync
        마다 재파생된다(레포 이동 시 다음 sync 가 자동 갱신). TOML 한 줄 문자열로 표현
        불가한 제어문자(개행 등) 경로만 핀 생략(_home_prefix_value, 프로파일 폴백).
        ⚠️ `VAR=val cmd` 는 POSIX 셸 전제 — Windows 미작동(0002 migration 문서의
        known-limitation 참조, Windows 는 setx 채널이 담당).
        """
        command = super().build_command(entry)
        assigns = []
        # self.member(install/`tm on --member` 경로) 우선, 없으면 self-healing 으로 현재
        # config.toml 에 이미 박힌 prefix 를 재사용 — member 없이 도는 `tm on/off` resync 가
        # prefix 를 떨구는 회귀를 막는다(issue #26, codex review). 둘 다 없으면 엔진의
        # 자동 해석 체인이 넘긴 member_fallback(issue #41 R2 — env/claude settings 유래)
        # 을 마지막으로 쓴다. 셋 다 없으면 prefix 생략(발명 금지, 하위호환).
        member = (self.member or self._existing_member_prefix()
                  or self.member_fallback)
        if member and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", member):
            assigns.append(f"TEAMMODE_MEMBER={member}")
        home = self._home_prefix_value()
        if home:
            assigns.append(f"TEAMMODE_HOME={shlex.quote(home)}")
        if assigns:
            return "env " + " ".join(assigns) + f" {command}"
        return command

    def _home_prefix_value(self) -> Optional[str]:
        """prefix 에 핀할 팀루트 절대경로 — 표현 불가 경로만 None(핀 생략, 프로파일 폴백).

        shlex.quote 가 공백/따옴표/메타문자/비ASCII 를 전부 안전 쿼팅하므로 값 검증으로
        걸러낼 필요가 없다. 유일한 예외는 개행/CR — config.toml 의 command 는 한 줄
        TOML 문자열(_toml_str)이라 표현 자체가 불가하다. 이 병리적 경로는 [warn] 1회
        출력 후 미핀(훅은 셸 프로파일/cwd 폴백으로 동작, 이슈 #9a 경고가 표면화).
        """
        home = str(self.team_root)
        if "\n" in home or "\r" in home:
            if not getattr(self, "_warned_home_unpinnable", False):
                self._warned_home_unpinnable = True
                print(f"[warn] 팀루트 경로에 개행 문자가 있어 TEAMMODE_HOME 을 hook "
                      f"command 에 핀하지 못했습니다(셸 프로파일 폴백): {home!r}")
            return None
        return home

    def _member_mismatch_warning(self) -> Optional[str]:
        """kept prefix ≠ 환경 폴백일 때의 [warn] 문구 — 경고만, 동작 무변경(issue #46 A3).

        build_command 의 우선순위(명시 self.member > 기존 prefix 자가치유 > member_fallback)
        가 기존 prefix 를 유지하는데, 폴백 체인(env TEAMMODE_MEMBER/claude settings)이
        **다른** 검증 통과 후보를 내놓으면 조용한 불일치가 생긴다 — 사용자는 환경값이
        반영됐다고 믿기 쉽다. 그 경우에만 교정 커맨드를 담은 [warn] 문구를 돌려준다.
        억제(None): 명시 --member(사용자가 이미 결정) / 폴백 부재·형식 무효(비교 대상
        없음 — 검증 정규식은 build_command 와 동일 규칙 공유) / 값 일치. sync 가
        _write_block 으로 옛 블록을 덮어쓰기 **전**에 호출해야 기존 prefix 가 보인다.
        """
        if self.member:
            return None
        fallback = self.member_fallback
        if not (isinstance(fallback, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", fallback)):
            return None
        kept = self._existing_member_prefix()
        if kept is None or kept == fallback:
            return None
        return (f"[warn] codex hook member prefix({kept}) ≠ 환경({fallback}) — "
                f"교정: tm on --member {fallback}")

    def _existing_member_prefix(self) -> Optional[str]:
        """현재 config.toml 의 managed hook 블록에서 기존 `env TEAMMODE_MEMBER=<x>` 를 파싱.

        self.member 가 None 일 때 build_command 가 호출(self-healing) — caller(예: member
        없이 `tm on` 하는 cmd_on)가 누구든 한 번 박힌 prefix 를 유지하게 한다. 안전을 위해
        **teammode-hooks 마커 블록 범위 안**에서만 찾고(사용자 다른 hook 오염 방지), 값은
        검증 정규식과 동일한 안전 토큰만 매칭한다. sync 가 _write_block 으로 블록을 덮어쓰기
        **전**에 _read_config() 가 옛 블록을 돌려주므로 이 파싱이 성립한다. 부재/깨짐 → None.
        """
        try:
            existing = self._read_config()
        except Exception:  # noqa: BLE001 — 설정 읽기 실패는 prefix 미보존(무해)로 강등
            return None
        if not existing:
            return None
        m = re.search(
            re.escape(BLOCK_START) + r"(.*?)" + re.escape(BLOCK_END), existing, re.S)
        scope = m.group(1) if m else ""
        if not scope:
            return None
        # command 라인의 셸 토큰만 신뢰 — 블록 통짜 텍스트 매칭은 quoted
        # TEAMMODE_HOME 값 안의 member-모양 부분문자열(예: 경로에 'env
        # TEAMMODE_MEMBER=x' 포함)을 오인할 수 있다(codex P2). 선두 `env` 뒤의
        # 실제 할당 토큰에서만 값을 취하고, 검증 정규식으로 재확인한다.
        for cm in re.finditer(r"command\s*=\s*(['\"])(.*?)\1", scope):
            try:
                tokens = shlex.split(cm.group(2))
            except ValueError:
                continue
            if not tokens or tokens[0] != "env":
                continue
            for tok in tokens[1:]:
                if "=" not in tok:
                    break  # env 할당 구간 종료(커맨드 본문 시작)
                key, _, val = tok.partition("=")
                if key == "TEAMMODE_MEMBER" and re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_-]*", val):
                    return val
        return None

    def sync(self, mode: Optional[str] = None) -> list:
        changes = []
        warnings = []
        infos = []
        wanted = self._wanted_entries(mode)

        # config services 1회 로드 — 빈 슬롯 우선 규칙(§2.9/§7.2)·install-mcp 선행(§2.7).
        # None = config 파일 부재 → 빈 슬롯 규칙 미적용(L1 동작 보존). claude 와 동형.
        services = self._load_services()

        toml_entries = []  # (codex_event, matcher_or_None, command, timeout_s)
        for entry in wanted:
            event = self.translate_event(entry["event"])
            match = entry.get("match")
            fallback = entry.get("fallback", "drop")
            enforcement = entry.get("enforcement", "advisory")

            if event is None:
                # 이벤트 미지원 → drop (+ warn). enforcement=block 이면 차단 상실 명시(§11.11).
                extra = " (block 강제 상실)" if enforcement == "block" else ""
                warnings.append(
                    f"[warn] {entry['script']}: {self.events.get('agent')} "
                    f"미지원(이벤트 {entry['event']}){extra} → 비활성")
                continue

            # ── MCP 매처 전처리(B.2 / §2.9 빈 슬롯 우선 + §2.7 install-mcp 선행) ──
            # services 가 dict 로 주어졌을 때만 적용(파일 부재 시 L1 동작 보존).
            if isinstance(match, dict) and "mcp" in match and isinstance(services, dict):
                canonical = match["mcp"].get("server")
                if not self._mcp_server_connected(canonical, services):
                    infos.append(
                        f"[info] {entry['script']}: '{canonical}' 역할 슬롯 미연결 "
                        f"→ MCP 매처 생략(빈 슬롯, 슬롯 연결 후 sync 재실행)")
                    continue
                if not self._mcp_alias_guaranteed(canonical):
                    warnings.append(
                        f"[warn] {entry['script']}: '{canonical}' MCP 별칭 미보장"
                        f"(install-mcp 선행 필요) → 이 매처만 생략")
                    continue

            matcher, expressible = self.translate_match(match)
            if not expressible:
                if fallback == "runtime":
                    matcher = None
                else:
                    warnings.append(
                        f"[warn] {entry['script']}: {self.events.get('agent')} "
                        f"매처 표현 불가 → 비활성")
                    continue

            command = self.build_command(entry)
            # manifest 의 timeout 은 **초** 단위(직접 기록). Codex config.toml 도 초 단위이므로
            # 변환 없이 그대로 쓴다. 미지정이면 None → TOML 에 timeout 행 생략.
            timeout_s = entry.get("timeout") or None
            toml_entries.append((event, matcher, command, timeout_s))

        # issue #46 A3: 자가치유로 유지되는 기존 prefix ≠ 환경 폴백이면 [warn] 1줄.
        # 반드시 _write_block **전**에 판정(옛 블록의 기존 prefix 가 아직 보이는 시점).
        # 렌더할 엔트리가 없으면 prefix 가 '유지'되는 게 아니므로 경고 대상 아님.
        if toml_entries:
            mismatch = self._member_mismatch_warning()
            if mismatch:
                warnings.append(mismatch)

        block = self._render_block(toml_entries, mode=mode)
        changed = self._write_block(block)
        if changed:
            changes.append(f"[sync] Codex 훅 {len(toml_entries)}개 등록")

        # warn 도배 방지: 같은 이벤트 미지원으로 발생한 warn 들을 묶어 1줄 요약 출력.
        # 형식 "[warn] {script}: {agent} 미지원(이벤트 {event})..." 을 파싱해 집계.
        # 다른 패턴(MCP 별칭 미보장, 매처 표현 불가)은 그대로 출력(드문 케이스, 도배 아님).
        import re as _re
        _unsupported_pat = _re.compile(
            r"^\[warn\] (.+?): .+ 미지원\(이벤트 ([^)]+)\)"
        )
        grouped: dict = {}   # (script, event) → [warn_msg, ...]
        other_warns: list = []
        for w in warnings:
            m = _unsupported_pat.match(w)
            if m:
                key = (m.group(1), m.group(2))
                grouped.setdefault(key, []).append(w)
            else:
                other_warns.append(w)
        # 묶인 warn 출력: N개면 1줄 요약, 1개면 그대로
        agent_name = self.events.get("agent", "")
        for (script, event), msgs in grouped.items():
            n = len(msgs)
            if n == 1:
                print(msgs[0])
            else:
                # block 강제 상실이 하나라도 있으면 표기
                has_block = any("block 강제 상실" in msg for msg in msgs)
                extra = " — block 강제 비활성" if has_block else ""
                print(f"[warn] {script}: {agent_name} {event} 미지원 {n}개{extra} → 비활성")
        for w in other_warns:
            print(w)
        for i in infos:
            print(i)
        if not changed and not warnings and not infos:
            changes.append("[ok] 변경 없음")
        return changes

    def _get_status_message(self) -> str:
        """Codex hook statusMessage 문자열 — '[<팀명>] 팀모드 ON'.

        팀명은 team.config.json team.name 에서 동적 생성(하드코딩 금지).
        """
        return f"[{self._get_team_name()}] 팀모드 ON"

    def _render_block(self, entries: list, mode: Optional[str] = None) -> str:
        """teammode hooks TOML 블록을 렌더링한다.

        statusMessage는 mode=="on" 일 때만 삽입한다.
        spec(internals.md §2.7): off는 mode없는 base entry 유지 — statusMessage 없음.
        """
        lines = [BLOCK_START, ""]
        for event, matcher, command, timeout_s in entries:
            lines.append(f"[[hooks.{event}]]")
            if matcher:
                lines.append(f'matcher = "{matcher}"')
            lines.append("")
            lines.append(f"[[hooks.{event}.hooks]]")
            lines.append('type = "command"')
            # 커맨드는 normalize 경유(§5.1-2). TOML 문자열로 그대로.
            lines.append(f"command = {self._toml_str(command)}")
            if timeout_s is not None:
                lines.append(f"timeout = {timeout_s}")
            # statusMessage: mode=="on" 일 때만 삽입(팀명 동적 생성, 하드코딩 금지).
            # off 경로에서는 base entry에 statusMessage 없음 (spec §2.7).
            if mode == "on":
                status_msg = self._get_status_message()
                lines.append(f"statusMessage = {self._toml_str(status_msg)}")
            lines.append("")
        lines.append(BLOCK_END)
        return "\n".join(lines)

    @staticmethod
    def _toml_str(s: str) -> str:
        # 큰따옴표가 들어있는 커맨드는 TOML literal(작은따옴표) 문자열로 안전 표현
        if "'" not in s:
            return "'" + s + "'"
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _read_config(self) -> str:
        if self.settings_path.is_file():
            return self.settings_path.read_text(encoding="utf-8")
        return ""

    def _purge_legacy_markers(self, existing: str) -> str:
        """teammode 마커 잔재를 걷어내 '정상 소유물만 남은' 텍스트를 돌려준다(issue #41 R1).

        실측 사고(2026-07-03, Acme): 과거 버전이 `# teammode-hooks-start` …
        `# teammode-mcp-end` 로 **마커 이름이 어긋난** 블록을 남겼고, 정상 쌍만 찾는
        _write_block 이 인식 실패 → 새 블록 append → 훅 2중 등록(구 블록은 prefix 없음 +
        stale timeout). 이 함수가 resync/uninstall 의 쓰기 전에 항상 돌아, 어떤 잔재가
        있어도 수동 수술 없이 '정상 블록 정확히 1개'로 수렴하게 한다.

        유지(정상 소유물 — 안 지움):
          - 문서상 **첫 번째** 정상 hooks-start↔hooks-end 쌍: _write_block 의 교체 앵커
            (제자리 교체 의미·멱등 보존). 두 번째 이후 hooks 쌍은 append 사고의 잔해 → 제거.
            단, 범위 안에 mcp 마커가 중첩된 hooks 쌍은 앵커로 못 쓴다(codex P2-1 —
            통짜 교체가 안쪽 MCP 블록을 죽임): hooks 마커 + 소유 증명 테이블만 제거.
          - 정상 mcp-start↔mcp-end 쌍: _write_mcp_block 소유 — 훅 sync 가 살아있는 MCP
            등록을 지우면 안 된다.
        제거(잔재):
          - 이름이 어긋난 start↔end 쌍(예: hooks-start↔mcp-end) — 스택 쌍짓기(가장
            가까운 start↔end)라 비탐욕 최단 범위. 중첩된 정상 쌍은 안쪽부터 짝지어져
            보존된다.
          - 알 수 없는 이름의 정상 쌍(과거 네이밍 변형).
          - 고아 end 마커: 마커 라인만.
          - 고아 start 마커: 마커 라인 + 보수 전방 스캔(_orphan_start_span) 범위.
        판단 불가한 내용은 남긴다 — 잔재 일부가 남는 쪽 오류는 다음 sync 가 다시 시도
        하지만, 사용자 설정을 지우는 쪽 오류는 복구 불가이기 때문.
        """
        if "teammode-" not in existing:
            return existing
        lines = existing.split("\n")
        markers = []  # (line_idx, 마커이름, start|end)
        # 멀티라인 문자열 상태 추적(codex P2-2): TOML 멀티라인 문자열('''/\"\"\") **안의**
        # 마커-모양 라인은 사용자 데이터다(예: command 가 다른 파일에 마커를 쓰는 스크립트).
        # 라인 스캔이 그걸 진짜 마커로 오인하면 사용자 설정이 지워지므로, 구분자 등장을
        # 라인마다 순차 소비해 안/밖 상태를 토글하고 '안'에서는 마커 인식을 끈다.
        # ── 스캐너 휴리스틱의 한계(의도된 보수 근사, 완전한 TOML 파서가 아님) ──
        #   - 한 줄(basic/literal) 문자열 값 안의 ''' / """ 부분문자열(예: x = "a '''")
        #     을 멀티라인 개시로 오인할 수 있다 → 이후 마커 인식이 꺼진다.
        #   - basic 멀티라인 안의 이스케이프(\" 등)·주석 안의 구분자도 액면 그대로 센다.
        #   오인의 방향은 항상 '마커를 놓쳐 잔재가 남는' 쪽(fail-safe) — 잔재는 다음
        #   sync 가 재시도하고, 사용자 설정을 지우는 쪽 오류는 발생하지 않는다.
        in_ml = None  # None=문자열 밖, 아니면 현재 열린 구분자("'''" 또는 '"""')
        for i, ln in enumerate(lines):
            if in_ml is None:
                m = _MARKER_LINE.match(ln)
                if m:
                    # 마커 라인은 정규식상 따옴표를 못 담는다 — 상태 갱신 불필요.
                    markers.append((i, m.group(1), m.group(2)))
                    continue
            rest = ln
            while True:
                if in_ml is None:
                    p1 = rest.find("'''")
                    p2 = rest.find('"""')
                    if p1 == -1 and p2 == -1:
                        break
                    if p2 == -1 or (p1 != -1 and p1 < p2):
                        in_ml, rest = "'''", rest[p1 + 3:]
                    else:
                        in_ml, rest = '"""', rest[p2 + 3:]
                else:
                    p = rest.find(in_ml)
                    if p == -1:
                        break  # 이 줄에서 안 닫힘 — 다음 줄도 문자열 안
                    rest = rest[p + 3:]
                    in_ml = None
        if not markers:
            return existing
        # 스택 쌍짓기: end 는 가장 가까운 미결 start 와 짝 — 어긋난 이름 조합도 쌍이 된다.
        pairs = []        # (start_idx, end_idx, start_name, end_name)
        orphan_ends = []  # 짝 없는 end 마커 라인
        stack = []        # 미결 start 들
        for idx, name, kind in markers:
            if kind == "start":
                stack.append((idx, name))
            elif stack:
                s_idx, s_name = stack.pop()
                pairs.append((s_idx, idx, s_name, name))
            else:
                orphan_ends.append(idx)
        orphan_starts = stack  # EOF 까지 짝 못 찾은 start 들

        keep: set = set()
        remove: set = set()
        hooks_anchor_seen = False
        for s, e, s_name, e_name in sorted(pairs):
            if s_name == e_name == "hooks" and not hooks_anchor_seen:
                # codex P2-1: 정상 hooks 쌍이라도 그 **범위 안에** mcp 마커가 있으면
                # (손상 레이아웃: hooks-start … mcp-start … mcp-end … hooks-end)
                # 앵커로 keep 하면 안 된다 — _write_block 이 hooks 범위를 통짜 교체해
                # 안쪽의 살아있는 MCP 블록까지 죽인다. 이 경우 hooks 마커 두 줄 +
                # 소유 증명된 훅 테이블(고아 start 와 동일한 보수 규칙)만 걷어내고
                # 안쪽 정상 mcp 쌍은 그대로 둔다 → 다음 쓰기가 새 블록을 append.
                inner_mcp = [i for i, name, _k in markers
                             if s < i < e and name == "mcp"]
                if inner_mcp:
                    remove.add(s)
                    remove.add(e)
                    remove.update(self._owned_table_span(lines, s + 1))
                    remove.update(self._owned_table_span(lines, max(inner_mcp) + 1))
                else:
                    hooks_anchor_seen = True   # 교체 앵커(문서상 첫 정상 hooks 쌍)
                    keep.update(range(s, e + 1))
            elif s_name == e_name == "mcp":
                keep.update(range(s, e + 1))
            else:
                remove.update(range(s, e + 1))
        for idx in orphan_ends:
            remove.add(idx)
        for idx, _name in orphan_starts:
            remove.update(self._orphan_start_span(lines, idx))
        remove -= keep  # 제거 범위에 중첩된 정상 소유물은 보존
        if not remove:
            return existing
        return "\n".join(ln for i, ln in enumerate(lines) if i not in remove)

    def _orphan_start_span(self, lines: list, idx: int) -> set:
        """고아 start 마커의 제거 범위(라인 인덱스 집합) — 보수적으로 산정.

        end 마커가 없어 블록 경계를 모른다. 근거 있는 범위만 지운다: 마커 라인 자체 +
        직후에 이어지는 소유 증명된 훅 테이블 연속 구간(_owned_table_span).
        """
        span = {idx}
        span.update(self._owned_table_span(lines, idx + 1))
        return span

    def _owned_table_span(self, lines: list, start: int) -> set:
        """start 부터 이어지는 **소유 증명된 [[hooks.*]] 테이블 연속 구간**의 라인 집합.

        마커 없는(고아 start·중첩 손상) 상태에서 삭제 범위를 정하는 공용 스캐너.
        선택 근거(보수 규칙): _render_block 이 렌더하는 블록 본문은 전부 normalize.py
        경유 [[hooks.*]] 테이블이므로, '소유 테이블 연속 구간'이 옛 블록 본문의 안전한
        상계다. 정지 경계 — 다른 마커 라인, hooks 아닌 TOML 섹션, 테이블 밖 일반 텍스트,
        **소유 증명 실패한 hooks 테이블**(사용자 훅일 수 있음 — 절대 안 지운다).

        소유 증명(codex P2-3): is_owned 의 느슨한 꼬리 판정(agents/codex/normalize.py
        부분문자열)만으로는 사용자가 직접 normalize.py 를 경유시킨 훅도 잡힌다.
        이 삭제 경로 **만은** managed command SHAPE(_is_managed_command_shape:
        `[env KEY=VAL…] <python> <normalize.py> <manifest 의 알려진 스크립트>`)를
        요구한다 — 증명 실패면 잔재를 남긴다(다음 sync 재시도, 무해). is_owned 자체는
        다른 소비처(재sync 갱신 판정 등)가 있으므로 여기서 바꾸지 않는다.
        """
        span: set = set()
        known = self._known_hook_scripts()
        n = len(lines)
        i = start
        while i < n:
            ln = lines[i]
            if not ln.strip():
                i += 1
                continue
            if _MARKER_LINE.match(ln) or not _HOOKS_TABLE.match(ln):
                break  # 다른 마커/비-hooks 내용 — 블록 본문 아님, 스캔 종료
            # 이벤트 테이블 1그룹: 이 헤더부터 다음 이벤트 테이블/비-hooks 섹션/마커 전까지
            # ([[hooks.X.hooks]] 서브테이블·matcher/command/timeout 행은 같은 그룹).
            j = i + 1
            while j < n:
                nxt = lines[j]
                if (_MARKER_LINE.match(nxt) or _EVENT_TABLE.match(nxt)
                        or (_ANY_SECTION.match(nxt) and not _HOOKS_TABLE.match(nxt))):
                    break
                j += 1
            group = lines[i:j]
            if not self._group_provably_managed(group, known):
                break  # 소유 증명 없는 테이블 — 사용자 훅일 수 있으므로 여기서 멈춤
            span.update(range(i, j))
            i = j
        return span

    def _known_hook_scripts(self) -> set:
        """hooks manifest 가 선언한 스크립트 파일명 집합 — managed SHAPE 판정의 화이트리스트.

        모드 무관 전체 엔트리(on 전용 포함) — 옛 블록엔 현재 모드에 없는 스크립트도
        남아 있을 수 있다. manifest 읽기 실패 → 빈 집합(삭제 증명 불가 = 삭제 안 함).
        """
        try:
            manifest = self._load_manifest()
        except Exception:  # noqa: BLE001 — manifest 부재/깨짐은 '증명 불가'로 강등
            return set()
        return {e.get("script") for e in manifest
                if isinstance(e, dict) and isinstance(e.get("script"), str)}

    def _group_provably_managed(self, group: list, known_scripts: set) -> bool:
        """테이블 그룹 라인들에 managed SHAPE 의 command 가 하나라도 있으면 True."""
        for ln in group:
            m = _COMMAND_LINE.match(ln)
            if not m:
                continue
            val = m.group(2)
            if m.group(1) == '"':
                # basic 문자열 최소 언이스케이프(managed 커맨드는 _toml_str 산출물이라
                # \\" 와 \\\\ 만 나온다). 그 외 이스케이프는 액면 유지 — 매칭 실패 시
                # 방향은 '삭제 안 함'(fail-safe).
                val = val.replace('\\"', '"').replace("\\\\", "\\")
            if self._is_managed_command_shape(val, known_scripts):
                return True
        return False

    def _is_managed_command_shape(self, command: str, known_scripts: set) -> bool:
        """command 가 teammode 가 렌더하는 모양인지 — 고아 삭제 전용 소유 증명(P2-3).

        요구 형태(build_command 의 산출 형태 전 세대 포함):
          [env KEY=VAL …] <python 인터프리터> <팀 루트의 normalize.py> <알려진 스크립트> [args…]
        - env prefix: 선택. `env` 뒤 `KEY=VAL` 할당 토큰 연속(셸 env(1) 문법).
        - 인터프리터: basename 이 python* (python3, python3.11, /path/python.exe …).
        - normalize.py 경로: is_owned 와 동일 판정(절대경로 or agents/codex/normalize.py 꼬리).
        - 스크립트: basename 이 manifest 선언 스크립트명 중 하나.
        어긋나면 False — '증명 불가 = 우리 것 아님'으로 삭제를 멈춘다.
        """
        if not known_scripts:
            return False
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        i = 0
        if i < len(tokens) and tokens[i] == "env":
            i += 1
            while i < len(tokens) and re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[i], re.S):
                i += 1
        if len(tokens) - i < 3:
            return False
        interp, norm, script = tokens[i], tokens[i + 1], tokens[i + 2]
        interp_base = os.path.basename(interp.replace("\\", "/")).lower()
        if not interp_base.startswith("python"):
            return False
        if not self.is_owned(norm):
            return False
        return os.path.basename(script.replace("\\", "/")) in known_scripts

    def _write_block(self, block: str) -> bool:
        original = self._read_config()
        # issue #41 R1: 정상 쌍 탐색 전에 legacy 잔재(어긋난 쌍·고아 마커·중복 블록)를
        # 먼저 걷어낸다 — 어떤 잔재가 있어도 결과는 항상 '정상 블록 1개'(append 금지).
        existing = self._purge_legacy_markers(original)
        pattern = re.compile(
            r"\n*" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n*",
            re.S)
        m = pattern.search(existing)
        if m:
            # 블록 앞에 사용자 콘텐츠가 있으면 두 줄 띄움, 없으면 선행 개행 없음 — 멱등 보장
            prefix = "\n\n" if existing[:m.start()].strip() else ""
            updated = existing[:m.start()] + prefix + block + "\n" + existing[m.end():]
        else:
            base = existing.rstrip()
            updated = (base + "\n\n" + block + "\n") if base else (block + "\n")
        if updated == original:
            return False
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(updated, encoding="utf-8")
        return True

    # ── install-mcp (§2.8) — Codex 방식: config.toml [mcp_servers.*] 관리형 블록 ──
    #
    # Codex 는 MCP 서버를 ~/.codex/config.toml 의 [mcp_servers.<name>] 섹션으로 등록한다
    # (claude 의 ~/.claude.json top-level mcpServers 와 다른 포맷 — 이 차이를 어댑터가 흡수).
    # 훅 블록(# teammode-hooks-*)과 동일 파일이므로 별도 마커 블록(# teammode-mcp-*)으로
    # 격리해 멱등 교체한다. teammode 는 MCP 서버 자체를 제작·유지하지 않는다(§7.4).
    #
    # 등록 항목(issue #20): Codex [mcp_servers.*] 는 stdio(command/args) 외에도
    # streamable HTTP(`url = "..."`)를 지원한다 — 팩 mcp.transport=="http" 면 공식
    # 호스티드 MCP(notion/linear 등)를 url 로 실제 등록한다. 호스티드도 기동 커맨드도
    # 없는 provider(slack/google 등)는 추측 금지 — register_hint placeholder 만 두고
    # install-mcp 메시지로 수동 등록 예시를 안내한다.
    # Codex 도 PreToolUse 를 표현하므로 confirm 훅은 hooks 블록에 등록된다.
    # install-mcp 는 서버 등록만 책임지고, 차단 강제력은 hooks/normalize 경로가 맡는다.

    MCP_BLOCK_START = "# teammode-mcp-start"
    MCP_BLOCK_END = "# teammode-mcp-end"

    def _read_mcp_servers(self) -> dict:
        """config.toml 의 teammode-mcp 블록에서 등록된 정규 서버명 집합을 파싱.

        부모(claude)의 ~/.claude.json 기반 구현을 Codex TOML 블록 기반으로 재정의.
        값은 부모 _mcp_alias_guaranteed 가 보는 `{"_teammode_managed": True}` 형태로 맞춘다.
        """
        existing = self._read_config()
        pattern = re.compile(
            re.escape(self.MCP_BLOCK_START) + r"(.*?)" + re.escape(self.MCP_BLOCK_END),
            re.S)
        m = pattern.search(existing)
        servers: dict = {}
        if not m:
            return servers
        for sm in re.finditer(r"\[mcp_servers\.([^\]]+)\]", m.group(1)):
            name = sm.group(1).strip().strip('"')
            servers[name] = {"_teammode_managed": True}
        return servers

    def _toml_str_list(self, items: list) -> str:
        """문자열 리스트 → TOML 배열 리터럴(args 등). 각 항목은 _toml_str 로 안전 인용."""
        return "[" + ", ".join(self._toml_str(str(a)) for a in items) + "]"

    def _render_mcp_server_lines(self, alias: str, pack, canonical=None) -> list:
        """단일 provider 의 [mcp_servers.<alias>] TOML 섹션 라인들.

        archive "MCP 마련" + §2.8 공식/자작 동일 처리:
          - 팩 mcp.transport=="http" + url 이면(issue #20) → Codex streamable HTTP 등록
            (`url = "..."`). notion/linear 등 공식 호스티드 MCP. 기동 커맨드/추측 불필요.
          - 팩 mcp 에 실 기동 데이터(command/args 또는 path)가 있으면 → command·args 를
            실제로 적어 Codex 가 기동 가능한 등록을 한다.
          - 없으면(P2 미기재) → 소유 마커 + register_hint placeholder 만(자리만 + 안내).
            추측 패키지명/repo 금지.
        섹션 키는 별칭 `tm-<provider>`(resolve_server_alias, §2.8-2), `_canonical_server`
        는 정규 서버명을 담는다(별칭이 아님 — 역추적·소유 식별용). canonical 미지정 시
        하위 호환으로 alias 를 그대로 쓴다. 소유 마커는 어느 경우든 유지.
        """
        if canonical is None:
            canonical = alias
        hint = pack.mcp.get("register_hint", "") if pack else ""
        lines = [f"[mcp_servers.{alias}]"]
        lines.append("_teammode_managed = true")
        lines.append(f"_canonical_server = {self._toml_str(canonical)}")
        lines.append(f"_register_hint = {self._toml_str(hint)}")
        src = (pack.mcp.get("source") if pack else None)
        url = self._mcp_http_url(pack)  # 부모(claude) 상속 — 호스티드 판정 공유
        if url is not None:
            # Codex streamable HTTP 서버: `url` + (OAuth 는 최초 사용 시 인터랙티브).
            lines.append(f"url = {self._toml_str(url)}")
            if isinstance(src, str) and src:
                lines.append(f"_mcp_source = {self._toml_str(src)}")
            return lines
        launch = self._mcp_launch_command(pack)  # 부모(claude) 상속 — 팩 mcp 해석 공유
        if launch is not None:
            command, args = launch
            lines.append(f"command = {self._toml_str(command)}")
            if args:
                lines.append(f"args = {self._toml_str_list(args)}")
            if isinstance(src, str) and src:
                lines.append(f"_mcp_source = {self._toml_str(src)}")
        return lines

    def _render_mcp_block(self, providers_with_packs: list) -> str:
        """연결 provider 목록 → teammode-mcp TOML 블록 문자열.

        providers_with_packs 항목은 (alias, pack) 또는 (alias, canonical, pack).
        후자면 _canonical_server 에 정규 서버명을, 섹션 키엔 별칭을 쓴다.
        """
        lines = [self.MCP_BLOCK_START, ""]
        for item in providers_with_packs:
            if len(item) == 3:
                alias, canonical, pack = item
            else:  # 하위 호환: (alias, pack) — canonical=alias
                alias, pack = item
                canonical = alias
            lines.extend(self._render_mcp_server_lines(alias, pack, canonical=canonical))
            lines.append("")
        lines.append(self.MCP_BLOCK_END)
        return "\n".join(lines)

    def _write_mcp_block(self, block: str) -> bool:
        original = self._read_config()
        # issue #41 R1: 훅 블록과 동일 파일이므로 MCP 쓰기 경로도 legacy 잔재를 치유
        # (install-mcp 가 sync 보다 선행 — §2.7 — 하므로 어느 쪽이 먼저 돌아도 수렴).
        existing = self._purge_legacy_markers(original)
        pattern = re.compile(
            r"\n*" + re.escape(self.MCP_BLOCK_START) + r".*?"
            + re.escape(self.MCP_BLOCK_END) + r"\n*", re.S)
        m = pattern.search(existing)
        if m:
            prefix = "\n\n" if existing[:m.start()].strip() else ""
            updated = existing[:m.start()] + prefix + block + "\n" + existing[m.end():]
        else:
            base = existing.rstrip()
            updated = (base + "\n\n" + block + "\n") if base else (block + "\n")
        if updated == original:
            return False
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(updated, encoding="utf-8")
        return True

    # [P1 삭제] handlers/role_server 폐기 — _render_teammode_toml() 제거.
    # teammode 단일 MCP 서버(role_server 기동) TOML 블록 자체가 사라졌다.
    # TODO P4: 벤더 MCP 등록기 — 공식/자작 MCP 마련 + 정규 서버명 alias 등록 정합.

    def install_mcp(self) -> list:
        """config services 의 연결 provider 를 Codex config.toml [mcp_servers.*] 로 등록. 멱등.

        claude 와 동일 계약(services 읽기·`tm-<provider>` 별칭 등록·멱등·빈 슬롯 [info])이되,
        등록 포맷만 Codex TOML 블록으로 재정의.
        """
        import providers as _prov  # 부모와 동일 모듈(infra/ on sys.path)
        changes = []
        services = self._load_services()
        connected = []
        if isinstance(services, dict):
            for slot in services.values():
                if isinstance(slot, dict):
                    prov = slot.get("provider")
                    if isinstance(prov, str) and prov.strip() and prov not in connected:
                        connected.append(prov)

        providers_with_packs = []
        aliases = []  # N2: 실제 변경과 무관하게 "등록 대상" provider 별칭 추적.
        for provider in connected:
            try:
                pack = _prov.lookup(provider, providers_dir=self.providers_dir)
            except Exception:
                pack = None
            if pack is None:
                changes.append(f"[info] {provider}: provider 팩 없음 → MCP 등록 생략")
                continue
            alias = self.resolve_server_alias(provider)
            # (alias, canonical, pack): 섹션 키=별칭(tm-<provider>), _canonical_server=정규명.
            providers_with_packs.append((alias, provider, pack))
            aliases.append(alias)

        # [P1 삭제] handlers/role_server 폐기 — teammode 서버 공존 분기 제거.
        if providers_with_packs:
            # 블록 본문 구성: 벤더 provider alias 만 등록. 공식/자작 동일 처리 —
            # 팩에 기동 데이터(command/args/path) 있으면 실 등록, 없으면 placeholder.
            block = self._render_mcp_block(providers_with_packs)
            changed = self._write_mcp_block(block)
            # N2: claude 와 대칭 — 실제 파일 변경 시에만 [mcp] 등록, 멱등 무변경은
            # [ok]. (_write_mcp_block 의 changed 반환값을 반영, 거짓 등록 보고 금지.)
            if changed:
                for alias, _canonical, pack in providers_with_packs:
                    if self._mcp_http_url(pack) is not None:
                        changes.append(
                            f"[mcp] {alias} 등록(공식 호스티드 MCP: "
                            f"{self._mcp_http_url(pack)})")
                    elif self._mcp_launch_command(pack) is not None:
                        changes.append(f"[mcp] {alias} 등록(기동 커맨드)")
                    else:
                        # 호스티드도 기동 커맨드도 없음(slack/google 등) — teammode 가 자동
                        # 등록 못 함. placeholder 는 관리 별칭(alias=`tm-<provider>`)으로만
                        # 잡히고 **연결되지 않는다**. 안내도 관리 별칭 기준으로 정직하게
                        # (codex review P2-a): claude 와 일관되게 같은 별칭으로 직접 붙이도록.
                        changes.append(
                            f"[mcp] {alias} placeholder 등록(공식 호스티드 MCP 부재 → "
                            f"teammode 자동 등록 불가, 이 placeholder 는 연결되지 않음). "
                            f"직접 쓰려면 같은 관리 별칭으로 수동 연결: "
                            f"`codex mcp add {alias} -- <MCP 서버 기동 커맨드>` "
                            f"(register_hint 참고)")
            else:
                changes.append(f"[ok] 변경 없음 ({len(aliases)}개 provider 등록됨)")
        else:
            # 연결 provider 없음 → 기존 teammode-mcp 블록 제거(멱등 빈상태).
            # 안전(P1-1): 블록이 없으면(부재 config 포함) 파일 무접촉 — pattern.search 가
            # 없을 때 write 하지 않으므로 빈 슬롯에서 config.toml 을 touch 하지 않는다.
            existing = self._read_config()
            pattern = re.compile(
                r"\n*" + re.escape(self.MCP_BLOCK_START) + r".*?"
                + re.escape(self.MCP_BLOCK_END) + r"\n*", re.S)
            if pattern.search(existing):
                self.settings_path.write_text(pattern.sub("\n", existing),
                                              encoding="utf-8")
                changes.append("[remove-mcp] teammode MCP 블록")
        if not changes:
            changes.append("[info] 연결된 MCP provider 없음 (빈 슬롯)")
        return changes

    def uninstall(self) -> list:
        existing = self._read_config()
        # issue #41 R1: uninstall 도 legacy 잔재(어긋난 쌍·고아 마커)를 함께 걷어낸다 —
        # 재설치 전 수동 수술 불요. 정상 블록 제거는 아래 기존 패턴이 담당.
        cleaned = self._purge_legacy_markers(existing)
        pattern = re.compile(
            r"\n?" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?",
            re.S)
        updated = pattern.sub("\n", cleaned)
        # MCP 블록도 함께 제거(역순 제거 — 등록 흔적 전부)
        mcp_pattern = re.compile(
            r"\n?" + re.escape(self.MCP_BLOCK_START) + r".*?"
            + re.escape(self.MCP_BLOCK_END) + r"\n?", re.S)
        updated2 = mcp_pattern.sub("\n", updated)
        if updated2 != existing:
            self.settings_path.write_text(updated2, encoding="utf-8")
            return ["[remove] teammode 훅 블록"]
        return ["[ok] 제거할 블록 없음"]


# ── CLI (디스패처가 호출) ──

def _default_paths():
    here = Path(__file__).resolve().parent
    team_root = here.parents[2]
    return {
        "agent_dir": str(here),
        "manifest_path": str(team_root / "infra" / "hooks" / "manifest.json"),
        "team_root": str(team_root),
    }


def main(argv=None) -> int:
    _ensure_utf8_io()  # 한글 [warn]/[ok] print 가 cp949 콘솔에서 크래시 방지(직접 실행 방어)
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="codex-adapter")
    p.add_argument("--config", default=os.path.expanduser("~/.codex/config.toml"))
    # --python 기본 None → 설치 시점 sys.executable 해석 (W-B, BaseAdapter 와 일관)
    p.add_argument("--python", default=None)
    # --team-root: 팀 레포 루트 절대경로. wire_agents 가 명시 전달해 __file__ 추론 어긋남 해소
    # (S0). 없으면 기존 기본값(here.parents[2]) 유지 — 하위 호환.
    p.add_argument("--team-root", default=None)
    # team.config.json·providers/ 경로 — 기본은 team_root 상대, 테스트는 tmp 주입.
    # (Codex MCP 등록은 --config 의 config.toml 안 블록이므로 별도 --mcp-config 불요.)
    p.add_argument("--team-config", default=None)
    p.add_argument("--providers-dir", default=None)
    # install-skills 스킬 디렉토리 — 기본 None(실호스트 ~/.codex/skills), 격리/테스트는 tmp.
    p.add_argument("--skills-dir", default=None)
    # --member: Codex hook command 에 TEAMMODE_MEMBER prefix 로 박을 멤버명(install 이 전달).
    # 미지정이면 prefix 없이 기존 command(하위호환). 값 검증·prefix 는 build_command 에서.
    p.add_argument("--member", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--on", action="store_true")
    sp.add_argument("--off", action="store_true")
    sub.add_parser("uninstall")
    sub.add_parser("install-mcp")
    sub.add_parser("install-skills")
    args = p.parse_args(argv)

    # 명시적 --team-root 가 빈 문자열/공백이면 조용히 기본값으로 폴백하지 않고 명확히 거부.
    if args.team_root is not None and args.team_root.strip() == "":
        print("[error] --team-root 에 빈 문자열/공백을 지정할 수 없습니다.", file=sys.stderr)
        return 1

    d = _default_paths()
    adapter = Adapter(
        agent_dir=d["agent_dir"],
        manifest_path=d["manifest_path"],
        settings_path=args.config,
        python=args.python,
        team_root=args.team_root if args.team_root is not None else d["team_root"],
        config_path=args.team_config,
        providers_dir=args.providers_dir,
        skills_dir=args.skills_dir,
        member=args.member,
    )
    if args.cmd == "sync":
        mode = "on" if args.on else ("off" if args.off else None)
        for c in adapter.sync(mode=mode):
            print(c)
    elif args.cmd == "uninstall":
        for c in adapter.uninstall():
            print(c)
        for c in adapter.uninstall_skills():
            print(c)
    elif args.cmd == "install-mcp":
        for c in adapter.install_mcp():
            print(c)
    elif args.cmd == "install-skills":
        for c in adapter.install_skills():
            print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
