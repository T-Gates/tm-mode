#!/usr/bin/env python3
"""teammode check — 단일 검수 도구 3-in-1 (docs/spec/internals.md §6.2).

  lint    — 정적: manifest 정규형·events.json 완전성 등 (엔진 실행 없음)
  verify  — 동적: 골든 시나리오를 우리 툴킷에 실행 (독푸딩 검수)
  conform — 동적+Tier: 같은 골든 시나리오를 임의 구현에 실행 + advisory 순응률로 Tier 산출
            (docs/spec/internals.md §6 conformance kit의 실물)

verify와 conform은 같은 골든 시나리오 정의(conformance/scenarios/)를 공유한다 —
시나리오 = 실행 가능한 스펙. 빈 엔진(no-op)에 돌리면 전부 RED = 엔진의 인수 테스트.

엔진은 argv를 받아 Result(exit_code, stdout, stderr)를 돌려주고 root 아래에
파일 부작용을 내는 하니스 인터페이스만 만족하면 된다 (docs/spec/internals.md §6:
파일 배치·언어 비강제).
"""
from __future__ import annotations

import argparse
import ast
import base64
import glob
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ──────────────────────────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────────────────────────

@dataclass
class Result:
    """엔진 1회 실행 결과."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class Step:
    name: str
    action: dict
    expect: list

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(
            name=d.get("name", ""),
            action=d.get("action", {"kind": "noop"}),
            expect=d.get("expect", []),
        )


@dataclass
class Scenario:
    id: str
    title: str
    tier_signal: str  # "deterministic" | "advisory"
    steps: list
    spec_refs: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(
            id=d["id"],
            title=d.get("title", d["id"]),
            tier_signal=d.get("tier_signal", "deterministic"),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            spec_refs=d.get("spec_refs", []),
        )


@dataclass
class AssertionResult:
    kind: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    id: str
    tier_signal: str
    passed: bool
    assertions: list = field(default_factory=list)


@dataclass
class TierResult:
    compliant: bool
    tier: Optional[int]
    advisory_compliance: float
    deterministic_pass: bool


@dataclass
class Report:
    mode: str
    results: list = field(default_factory=list)
    tier: Optional[TierResult] = None

    @property
    def green(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)


@dataclass
class LintReport:
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c[1] for c in self.checks)


# ──────────────────────────────────────────────────────────────────
# 시나리오 파싱
# ──────────────────────────────────────────────────────────────────

def load_scenarios(scenario_dir) -> list:
    scenario_dir = Path(scenario_dir)
    scenarios = []
    for path in sorted(scenario_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            scenarios.append(Scenario.from_dict(json.load(f)))
    return scenarios


# ──────────────────────────────────────────────────────────────────
# Assertion 평가
# ──────────────────────────────────────────────────────────────────

def _sessions_dir(root: Path, author: str) -> Path:
    return Path(root) / "memory" / "team" / "sessions" / author


def _session_log_files(root: Path, author: str) -> list:
    d = _sessions_dir(root, author)
    if not d.is_dir():
        return []
    # 세션로그 네임스페이스: YYYY-MM-DD 로 시작하는 .md (docs/spec/internals.md §1.3)
    out = []
    for p in d.glob("*.md"):
        stem = p.stem
        if len(stem) >= 10 and stem[:4].isdigit() and stem[4] == "-":
            out.append(p)
    return out


def _eval_assertion(a: dict, root: Path, last: Optional[Result]) -> AssertionResult:
    kind = a.get("kind")
    root = Path(root)

    if kind == "exit_code":
        got = last.exit_code if last else None
        ok = got == a.get("value")
        return AssertionResult(kind, ok, f"exit_code={got} want={a.get('value')}")

    if kind == "stdout_contains":
        text = last.stdout if last else ""
        ok = a.get("value", "") in text
        return AssertionResult(kind, ok, f"stdout missing {a.get('value')!r}" if not ok else "")

    if kind == "stderr_contains":
        text = last.stderr if last else ""
        ok = a.get("value", "") in text
        return AssertionResult(kind, ok, "")

    if kind == "file_exists":
        ok = (root / a["path"]).is_file()
        return AssertionResult(kind, ok, f"missing {a['path']}" if not ok else "")

    if kind == "file_contains":
        p = root / a["path"]
        ok = p.is_file() and a.get("value", "") in p.read_text(encoding="utf-8")
        return AssertionResult(kind, ok, "")

    if kind == "session_log_single_file":
        files = _session_log_files(root, a["author"])
        ok = len(files) == 1
        return AssertionResult(kind, ok, f"{len(files)} session-log files" if not ok else "")

    if kind == "session_log_contains":
        files = _session_log_files(root, a["author"])
        blob = "".join(p.read_text(encoding="utf-8") for p in files)
        ok = a.get("value", "") in blob
        return AssertionResult(kind, ok, "")

    if kind == "state_off":
        # off 상태 영속화: .teammode-active 마커 부재
        ok = not (root / ".teammode-active").exists()
        return AssertionResult(kind, ok, "active marker still present" if not ok else "")

    if kind == "state_on":
        ok = (root / ".teammode-active").exists()
        return AssertionResult(kind, ok, "")

    return AssertionResult(kind or "?", False, f"unknown assertion kind: {kind!r}")


# ──────────────────────────────────────────────────────────────────
# 시나리오 실행
# ──────────────────────────────────────────────────────────────────

def _apply_action(action: dict, engine, root: Path, last: Optional[Result]):
    """동작을 수행하고 (새 last Result) 반환. noop은 이전 Result 유지."""
    kind = action.get("kind", "noop")
    if kind == "command":
        return engine.run(action.get("argv", []))
    if kind == "fs_write":
        p = root / action["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(action.get("content", ""), encoding="utf-8")
        return last
    if kind == "fs_delete":
        # 시나리오 자체 정리(teardown). 공유 root 순차실행에서 한 시나리오의 fixture 가
        # 다음 시나리오로 새지 않게 한다(예: 03 이 연결 issues fixture 를 세운 뒤 원복).
        # root 하위로만 작동(상위 traversal 방지): 정규화 후 root 밖이면 무시.
        # 경계 일치로 판정한다(문자열 prefix 금지 — `/base/root` 가
        # 형제 `/base/root-evil` 의 prefix 이기도 한 우회를 막는다).
        p = (root / action["path"]).resolve()
        root_r = root.resolve()
        try:
            inside = p.relative_to(root_r) is not None
        except ValueError:
            inside = False
        if inside and p.is_file():
            p.unlink()
        return last
    if kind == "noop":
        return last
    # 알 수 없는 action — 실패 신호를 위해 비정상 Result
    return Result(exit_code=127, stderr=f"unknown action kind: {kind!r}")


def run_scenario(scenario: Scenario, engine, root) -> ScenarioResult:
    root = Path(root)
    last: Optional[Result] = None
    all_assertions = []
    passed = True
    for step in scenario.steps:
        last = _apply_action(step.action, engine, root, last)
        for a in step.expect:
            ar = _eval_assertion(a, root, last)
            all_assertions.append(ar)
            if not ar.passed:
                passed = False
    return ScenarioResult(scenario.id, scenario.tier_signal, passed, all_assertions)


# ──────────────────────────────────────────────────────────────────
# Tier 산출 (§11.11)
# ──────────────────────────────────────────────────────────────────

def compute_tier(results: list) -> TierResult:
    """결정적 시나리오가 전부 통과해야 호환. advisory 순응률로 Tier 등급.

    Tier 1 = advisory 100% / Tier 2 = advisory 부분 / Tier 3 = advisory 0.
    결정적 실패가 하나라도 있으면 compliant=False (Tier 미산정).
    """
    det = [r for r in results if r.tier_signal == "deterministic"]
    adv = [r for r in results if r.tier_signal == "advisory"]

    deterministic_pass = all(r.passed for r in det) if det else True

    if adv:
        advisory_compliance = sum(1 for r in adv if r.passed) / len(adv)
    else:
        advisory_compliance = 1.0

    if not deterministic_pass:
        return TierResult(False, None, advisory_compliance, deterministic_pass)

    if advisory_compliance >= 1.0:
        tier = 1
    elif advisory_compliance > 0.0:
        tier = 2
    else:
        tier = 3
    return TierResult(True, tier, advisory_compliance, deterministic_pass)


# ──────────────────────────────────────────────────────────────────
# 모드 디스패치
# ──────────────────────────────────────────────────────────────────

def run_mode(mode: str, engine, root, scenario_dir=None) -> Report:
    if scenario_dir is None:
        scenario_dir = Path(__file__).resolve().parent / "scenarios"
    scenarios = load_scenarios(scenario_dir)
    results = [run_scenario(s, engine, root) for s in scenarios]
    report = Report(mode=mode, results=results)
    if mode == "conform":
        report.tier = compute_tier(results)
    return report


# ── lint (정적) ──

def _lint_manifest_canonical(root: Path) -> tuple:
    """manifest.json에 에이전트 고유 표기(mcp__, Write|Edit 등)가 없는지 (docs/spec/internals.md §2, K4)."""
    manifest_path = Path(root) / "infra" / "hooks" / "manifest.json"
    if not manifest_path.is_file():
        return ("manifest 정규형", True, "manifest 없음 — 건너뜀")
    text = manifest_path.read_text(encoding="utf-8")
    forbidden = ["mcp__", "Write|Edit", "apply_patch"]
    hits = [tok for tok in forbidden if tok in text]
    return ("manifest 정규형", not hits,
            f"에이전트 고유 표기 발견: {hits}" if hits else "")


# 토큰키 린트 (L2-A A.3, P0-4). .gitignore 는 죽은 방어 — 토큰/비밀을 담은
# 파일이 추적 트리(팀 루트)에 진입하면 코드 검사가 강제로 막는다.
#
# 범위(좁게, 거짓양성 차단): 이 린트는 "config 류 데이터 파일이 평문 비밀을 담는"
# 사고만 잡는다 — 산문(docs/spec/)·코드(*.py)·BUILD-LOG 등에서 'token'/'secret' 단어를
# 쓰는 건 정상이므로 검사 대상이 아니다(lint 범위 = config/credentials 데이터 파일).
#   대상: team.config.json·team.config.*.json(.example 포함) + .gitignore 비밀 패턴
#         이름을 가진 추적 파일(*credentials*·*.token·*secret*).
#   판정: 토큰성 키(token/secret/password/api_key/<x>key)에 **비어있지 않은 값**이
#         붙은 줄. resource_fields 식별자(database_id·calendar_id·channel_id)는
#         'key' 미포함 + 비밀 아님 → 안 걸린다.
# 토큰성 키 매칭. `key` 거짓양성(monkey/donkey/turkey 등) 차단을 위해 `key` 는
# 단어경계(접두 없음)이거나 `_`/`-` 구분자 뒤(api_key·access-key)일 때만 인정한다.
# apikey 는 명시 어휘로 별도 허용. passphrase 도 비밀 어휘에 포함.
_SECRET_KEY_RE = re.compile(
    r'(?:["\']|\b)'                             # 따옴표 또는 단어경계로 키 시작 고정
    r'(?:'
    r'(?:[a-z0-9]+[_-])*'                       # 선택 접두 (api_, access_, bot_ …)
    r'(?:token|secret|password|passwd|passphrase|apikey|api[_-]?key)'
    r'|'
    r'(?:[a-z0-9]+[_-])key'                     # …_key / …-key (구분자 필수)
    r'|key'                                      # 독립 'key'(앞의 \b 가 단어경계 강제)
    r')'
    r'["\']?\s*[:=]\s*'                         # JSON ':' 또는 env '='
    r'["\']?(?P<val>[^"\'\s,}#]+)',             # 비어있지 않은 값
    re.IGNORECASE,
)
# 값이 비밀이 아님이 명백한 placeholder (example/문서용). 키 이름이 비밀이어도
# 값이 이 화이트리스트면 통과(예시 config 가 비밀 습관을 가르치지 않게 — 단, 빈/문서값만).
_SECRET_VALUE_ALLOW = {"null", "true", "false", "none", "...", "<...>",
                       "changeme", "your-token-here", "todo", "placeholder",
                       "tbd", "example", "redacted", "xxx"}


def _secret_hit_line(line: str) -> bool:
    """라인에 토큰성 키 + 비어있지 않은(비-placeholder) 값이 있는가."""
    for m in _SECRET_KEY_RE.finditer(line):
        val = m.group("val").strip().strip("\"'").lower()
        if val and val not in _SECRET_VALUE_ALLOW:
            return True
    return False


# ── 자작 MCP(infra/mcp/**/*.py) 전용 토큰 리터럴 딥스캔 (P4-B) ──
#
# P1 에서 handlers/ 폐기와 함께 handlers/*.py 전용 토큰 딥스캔을 제거했다. 그러나 L2
# 재설계에서 infra/mcp/<provider>/ 는 **자작 MCP 보관소**다 — 공식 MCP 가 없을 때 AI 가
# provider API 를 감싼 MCP 서버를 작성해 두는 곳이고, 토큰은 env/금고(0600)에서만 받아야
# 한다(archive "MCP 마련" 4단계). 자작 MCP 소스에 토큰 리터럴이 embed 될 수 있으므로,
# 일반 데이터-파일 린트(.py 는 skip)와 별개로 infra/mcp/**/*.py 를 **데이터처럼** 강제
# 딥스캔한다. 키-이름 기반이 아니라 **값의 접두사**(Bearer·sk-·xoxb- …)로 판단하며,
# tokenize 기반으로 문자열 분할·bytes·base64·f-string 우회까지 탐지한다.
# (P1 에서 지운 handlers 딥스캔과 동등 로직 — 대상 디렉토리만 handlers/ → infra/mcp/.)
#
# infra/mcp/ 가 비어있을 수 있다(자작 MCP 아직 없음) — 그 경우 no-op(대상 없으면 통과).
_HANDLER_TOKEN_RE = re.compile(
    r'(?:["\'])'
    r'(?P<val>'
    # Authorization: Bearer <실토큰>. 토큰 문자류 15+ 만 매치 — 안전한 헤더 조립
    # (f"Bearer {tok}" · "Bearer {}".format() · "Bearer %s")은 보간 문자({}/%/공백)가
    # 토큰류 char-class 밖이라 자연히 제외된다. 진짜 리터럴 토큰만 잡는다.
    r'Bearer\s+[A-Za-z0-9._/+\-]{15,}'
    r'|sk-[A-Za-z0-9_-]{10,}'           # OpenAI / Anthropic sk- 키 (하이픈 포함)
    r'|xoxb-[\w-]{10,}'                  # Slack bot token
    r'|xoxp-[\w-]{10,}'                  # Slack user token
    r'|xoxa-[\w-]{10,}'                  # Slack workspace token
    r'|xoxr-[\w-]{10,}'                  # Slack refresh token
    r'|gh[pousr]_[A-Za-z0-9]{10,}'      # GitHub personal/oauth/user/server/refresh token
    r'|github_pat_[A-Za-z0-9_]{10,}'    # GitHub fine-grained PAT
    r'|AKIA[A-Z0-9]{16}'                 # AWS access key ID
    r'|ASIA[A-Z0-9]{16}'                 # AWS temporary access key ID
    r'|Basic\s+[A-Za-z0-9+/]{10,}={0,2}'  # HTTP Basic base64 인증
    r'|[0-9a-f]{40}'                     # 40자 hex (git SHA / 레거시 토큰)
    r')'
    r'(?:["\'])',
    re.IGNORECASE,
)

# bytes literal 안의 토큰 패턴 (b"sk-proj-..." 우회)
_HANDLER_TOKEN_BYTES_RE = re.compile(
    r'b["\']'
    r'(?P<val>'
    r'sk-[A-Za-z0-9_-]{10,}'
    r'|xox[bpar]-[\w-]{10,}'
    r'|gh[pousr]_[A-Za-z0-9]{10,}'
    r'|github_pat_[A-Za-z0-9_]{10,}'
    r'|AKIA[A-Z0-9]{16}'
    r'|ASIA[A-Z0-9]{16}'
    r'|Bearer\s+[A-Za-z0-9._/+\-]{15,}'
    r'|Basic\s+[A-Za-z0-9+/]{10,}={0,2}'
    r'|[0-9a-f]{40}'
    r')'
    r'["\']',
    re.IGNORECASE,
)

# base64 디코딩 후 재검사용 — base64로 인코딩된 토큰을 숨기는 우회 탐지
_BASE64_CANDIDATE_RE = re.compile(
    r'["\']([A-Za-z0-9+/]{20,}={0,2})["\']'
)

# 연결 합산 후 토큰이 되는 문자열 분할 우회 탐지.
# Bearer 는 뒤에 토큰류 char 가 따라올 때만(실토큰 조립/분할) 매치 — f"Bearer {tok}"·
# "Bearer %s" 처럼 보간 placeholder 가 따라오는 안전 헤더는 제외.
_CONCAT_TOKEN_RE = re.compile(
    r'(?:xox[bpasr]|sk-proj|Bearer\s+[A-Za-z0-9._/+\-]|github_pat_|AKIA|ASIA|gh[pousr]_)',
    re.IGNORECASE,
)


def _decode_base64_token(s: str):
    """base64 문자열을 디코딩해 토큰성 값이 있으면 반환, 아니면 None."""
    try:
        pad = (4 - len(s) % 4) % 4
        decoded = base64.b64decode(s + "=" * pad).decode("utf-8", errors="replace")
        return decoded
    except Exception:
        return None


def _extract_string_inner(raw: str):
    """Python 문자열 토큰 raw 값에서 내부 값을 추출. 실패 시 None."""
    try:
        val = ast.literal_eval(raw)
        if isinstance(val, (str, bytes)):
            return (val.decode("utf-8", errors="replace")
                    if isinstance(val, bytes) else val)
        return None
    except Exception:
        return None


def _raw_token_match(s: str) -> bool:
    """문자열 s 에 토큰 패턴이 있는지 (placeholder 정확일치 제외)."""
    s_lower = s.strip().lower()
    if s_lower in _SECRET_VALUE_ALLOW:
        return False
    if _HANDLER_TOKEN_RE.search(f'"{s}"'):
        return True
    if _HANDLER_TOKEN_BYTES_RE.search(f'b"{s}"'):
        return True
    if _CONCAT_TOKEN_RE.search(s) and len(s) >= 15:
        return True
    return False


def _handler_secret_hit_line_raw(line: str) -> bool:
    """단일 라인에서 토큰 리터럴 직접값 탐지 (tokenize 미사용 폴백·라인보고용)."""
    if _HANDLER_TOKEN_BYTES_RE.search(line):
        for m in _HANDLER_TOKEN_BYTES_RE.finditer(line):
            val = m.group("val").strip().lower()
            if val not in _SECRET_VALUE_ALLOW:
                return True
    for m in _HANDLER_TOKEN_RE.finditer(line):
        val = m.group("val").strip().lower()
        if val not in _SECRET_VALUE_ALLOW:
            return True
    for m in _BASE64_CANDIDATE_RE.finditer(line):
        decoded = _decode_base64_token(m.group(1))
        if decoded and _raw_token_match(decoded):
            return True
    return False


def _handler_secret_hit_multiline(source: str) -> bool:
    """tokenize 실패 시 폴백: 라인 기반 패턴 검사."""
    for line in source.splitlines():
        if _handler_secret_hit_line_raw(line):
            return True
    return False


def _source_has_handler_token(source: str) -> bool:
    """자작 MCP 소스 전체를 tokenize 기반으로 스캔(분할·bytes·base64·f-string 우회 포함).

    placeholder 값(전체 정확일치)은 허용. (P1 폐기 handlers 딥스캔과 동등.)
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # malformed = 차단이 안전 — 라인 기반 폴백.
        return _handler_secret_hit_multiline(source)

    string_values: list = []
    prev_was_string = False
    _FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)

    for tok_type, tok_string, _ts, _te, _tl in tokens:
        if tok_type == tokenize.COMMENT:
            inner = tok_string.lstrip("#").strip()
            if _raw_token_match(inner):
                return True
            prev_was_string = False
            continue

        if _FSTRING_MIDDLE is not None and tok_type == _FSTRING_MIDDLE:
            if _CONCAT_TOKEN_RE.search(tok_string):
                return True
            if _raw_token_match(tok_string):
                return True
            continue

        if tok_type == tokenize.STRING:
            raw = tok_string
            if raw.startswith(("b'", 'b"', "B'", 'B"', "rb", "br", "RB", "BR")):
                inner = _extract_string_inner(raw)
                if inner and _raw_token_match(inner):
                    return True
                prev_was_string = False
                continue
            _FSTRING_PREFIXES = ("f'", 'f"', "F'", 'F"',
                                 "rf'", 'rf"', "RF'", 'RF"',
                                 "fr'", 'fr"', "FR'", 'FR"')
            if raw.startswith(_FSTRING_PREFIXES):
                if _handler_secret_hit_line_raw(raw):
                    return True
                if _CONCAT_TOKEN_RE.search(raw):
                    return True
                prev_was_string = False
                continue
            inner = _extract_string_inner(raw)
            if inner is None:
                if _handler_secret_hit_line_raw(raw):
                    return True
                prev_was_string = False
                continue
            if inner.strip().lower() in _SECRET_VALUE_ALLOW:
                prev_was_string = False
                continue
            if _raw_token_match(inner):
                return True
            for m in _BASE64_CANDIDATE_RE.finditer(raw):
                decoded = _decode_base64_token(m.group(1))
                if decoded and _raw_token_match(decoded):
                    return True
            if prev_was_string:
                accumulated = (string_values[-1] + inner) if string_values else inner
                if _CONCAT_TOKEN_RE.search(accumulated) and _raw_token_match(accumulated):
                    return True
                if string_values:
                    string_values[-1] = accumulated
                else:
                    string_values.append(inner)
            else:
                string_values.append(inner)
            prev_was_string = True
            continue

        if tok_type == tokenize.OP and tok_string == "+":
            continue  # string + string 연결 — prev_was_string 유지

        if tok_type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.ENCODING):
            prev_was_string = False

    return False


# 비밀이 절대 들어가면 안 되는 추적 **데이터** 파일 패턴 (이름 기반).
_SECRET_TARGET_GLOBS = ("team.config.json", "team.config.*.json",
                        "*credentials*", "*secret*", "*.token",
                        ".env", ".env.*")

# `*credentials*`·`*secret*` 는 이름 부분일치라 소스 코드 모듈(infra/credentials.py 등)까지
# 과(過)매칭한다. 이 린트의 대상은 평문 토큰이 들어갈 수 있는 **데이터 파일**이지 소스가
# 아니다. 코드/문서 확장자는 스캔에서 제외해 함수 인자명(`token`/`key`) 같은 정상 식별자를
# 거짓 양성으로 잡지 않게 한다.
#   ⚠️ 정정(P2-2): 소스(.py 등)에 **하드코딩된 리터럴 토큰**은 이 린트가 잡지 못한다 —
#   본 린트는 데이터 파일 한정이고, 마스킹 테스트(tests/test_credentials_l2e.py)는
#   credentials.py 가 토큰을 *출력/예외/로그에 흘리지 않음*만 강제할 뿐, 임의 소스에
#   박힌 리터럴 토큰 자체는 검출 대상이 아니다. 즉 소스 하드코딩 토큰에 대한 코드-레벨
#   백스톱은 **없다**(git 백스톱은 .gitignore `*credentials*`/`*secret*` 가 데이터 위치에
#   한정). 후속 작업자는 "소스가 보호된다"고 오인하지 말 것.
_SECRET_TARGET_SKIP_SUFFIXES = (".py", ".pyc", ".pyi", ".md", ".txt", ".rst",
                                ".ipynb", ".sh", ".toml", ".cfg", ".ini")


def lint_no_tracked_secrets(root, *, files=None) -> tuple:
    """config/credentials 데이터 파일에 평문 토큰키가 들어가면 거부 (P0-4).

    .gitignore 보다 강제력 있는 코드 검사. files 주입 시 그 파일들만 검사(테스트 격리).
    미지정 시 root 하위에서 _SECRET_TARGET_GLOBS 에 매칭되는 추적 파일만 스캔.
    반환: (검사명, 통과여부, 상세) — _lint_manifest_canonical 과 동일 tuple 형.
    """
    root = Path(root)
    if files is None:
        # gitignore 된 것만 제외하고 스캔 — tracked + untracked-not-ignored.
        # gitignored 캐시(.codex-ref 등 외부 레퍼런스)는 제외(fs 전체 rglob 금지).
        # 비-git 디렉토리(tmp 테스트 등)는 rglob fallback.
        import subprocess, fnmatch
        scan = []
        try:
            for args in (["ls-files", "-z"],
                         ["ls-files", "--others", "--exclude-standard", "-z"]):
                out = subprocess.run(["git", "-C", str(root)] + args,
                                     capture_output=True, text=True, timeout=5)
                if out.returncode == 0:
                    scan += [root / rel for rel in out.stdout.split("\0") if rel]
        except (OSError, subprocess.SubprocessError):
            scan = []
        if not scan:  # 비-git: rglob fallback (.git 제외)
            scan = [p for pat in _SECRET_TARGET_GLOBS
                    for p in root.rglob(pat) if ".git" not in p.parts]
        candidates = []
        seen = set()
        for p in scan:
            if not p.is_file():
                continue
            name = p.name
            if name.endswith(".example"):
                continue  # placeholder 관례 (.env.example 등) — 비밀 아님
            if name.endswith(_SECRET_TARGET_SKIP_SUFFIXES):
                continue  # 소스/문서 — 데이터 파일 린트 대상 아님(부분일치 과매칭 방지)
            if not any(fnmatch.fnmatch(name, pat) for pat in _SECRET_TARGET_GLOBS):
                continue
            if p not in seen:
                seen.add(p)
                candidates.append(p)
        # P4-B: infra/mcp/**/*.py — 자작 MCP 보관소. suffix skip(.py) 없이 강제 딥스캔.
        # _SECRET_TARGET_SKIP_SUFFIXES 에 .py 가 있어 일반 소스는 데이터-린트에서 빠지지만,
        # 자작 MCP 코드는 토큰이 직접 embed 될 수 있어 "데이터처럼 취급"한다(P1 폐기 handlers
        # 딥스캔과 동등 — 대상만 handlers/ → infra/mcp/). 비어있으면 no-op(대상 없으면 통과).
        # .venv·__pycache__ 등은 prune(거짓양성·외부 패키지 차단).
        _PRUNE_DIRS = frozenset({".venv", "venv", "__pycache__", ".git", "site-packages"})
        mcp_files: set = set()
        mcp_dir = root / "infra" / "mcp"
        if mcp_dir.is_dir():
            for hp in sorted(mcp_dir.rglob("*.py")):
                if not hp.is_file():
                    continue
                if any(part in _PRUNE_DIRS for part in hp.parts):
                    continue
                if hp not in seen:
                    seen.add(hp)
                    candidates.append(hp)
                    mcp_files.add(hp)
    else:
        # files= 입력을 먼저 정규화 — 상대경로는 root / f 로 변환 (codex fault injection #4).
        # 절대경로라면 그대로, 상대경로라면 root 기준으로 해석한다.
        # 이렇게 해야 candidates/mcp 분류가 동일 객체를 기준으로 동작한다.
        candidates = [
            Path(f) if Path(f).is_absolute() else root / f
            for f in files
        ]
        # files= 경로에서도 infra/mcp/ 하위 .py 를 자작 MCP 로 분류해 딥스캔 우회 차단.
        mcp_files = set()
        mcp_dir = root / "infra" / "mcp"
        if root.is_dir() and mcp_dir.is_dir():
            for p in candidates:
                try:
                    Path(p).resolve().relative_to(mcp_dir.resolve())
                    mcp_files.add(Path(p))
                except (ValueError, OSError):
                    pass
        else:
            # root 가 tmp 등일 때: 경로 문자열로 infra/mcp/ 포함 여부 판정.
            for p in candidates:
                parts = Path(p).parts
                if "mcp" in parts and "infra" in parts:
                    mcp_files.add(Path(p))

    hits = []
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = p
        # P4-B: infra/mcp/**/*.py 는 전용 tokenize 딥스캔 우선(분할·bytes·base64·f-string
        # 우회 포함). 라인 기반 _secret_hit_line 은 비-MCP 데이터 파일용으로 유지.
        if p in mcp_files:
            if _source_has_handler_token(text):
                line_hit = False
                for i, line in enumerate(text.splitlines(), 1):
                    if _handler_secret_hit_line_raw(line):
                        hits.append(f"{rel}:{i}")
                        line_hit = True
                if not line_hit:
                    # tokenize 가 잡았지만 라인별로 못 잡은 경우(분할·base64 우회).
                    hits.append(f"{rel}:?")
        else:
            for i, line in enumerate(text.splitlines(), 1):
                if _secret_hit_line(line):
                    hits.append(f"{rel}:{i}")
    return ("토큰키 추적 거부", not hits,
            f"토큰키 진입 발견(.gitignore 우회 위험): {hits}" if hits else "")


# ── 스킬 본문 정규형 린트 (K7, SPEC §2.12·§7.3) ──
#
# 스킬 본문(SKILL.md)은 **시맨틱/역할 어휘만** 쓴다(§7.3): "이슈 트래커 MCP 에서 조회"
# 처럼. 두 가지를 거부한다:
#   (a) `mcp__*` 직표기 — 에이전트 고유 툴명 형식(§2.12 lint 대상).
#   (b) 제품명 직표기 — 역할 슬롯은 도구 중립 어휘(issues/chat/docs/calendar)로만
#       말해야 하므로, 본문에 특정 제품명(linear·slack·notion·google …)을 박으면 위반.
#
# 제품명 목록은 **데이터로** 끌어온다(하드코딩 최소화 — providers/<name>.json 의
# provider 값 = 정규 서버명 == 제품 식별자). 새 provider 팩이 추가되면 그 이름도
# 자동으로 금지어가 된다. 더해 팩에 없을 수 있는 흔한 경쟁 제품명도 소수 고정으로 막는다.
_SKILL_EXTRA_PRODUCTS = (
    "jira", "asana", "trello", "discord", "teams",
)


def _provider_product_names(root: Path) -> set:
    """providers/<name>.json 의 provider 값(= 제품 식별자) 집합 + 흔한 경쟁 제품명."""
    names = set(_SKILL_EXTRA_PRODUCTS)
    pdir = Path(root) / "providers"
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.json")):
            # stem(파일명) == provider(항등 불변식, §2.5) — JSON 파싱 없이 안전하게 stem.
            names.add(f.stem.lower())
    return names


def lint_skill_canonical(root, *, files=None) -> tuple:
    """스킬 본문에 `mcp__*`·제품명 직표기가 없는지 (K7, SPEC §2.12·§7.3).

    files 주입 시 그 파일들만 검사(테스트 격리). 미지정 시 infra/skills/**/SKILL.md
    (단 infra/skills/util/** 은 면제 — 아래 참조).
    반환: (검사명, 통과여부, 상세) — 다른 lint 함수와 동일 tuple 형.
    """
    root = Path(root)
    if files is None:
        skills_dir = root / "infra" / "skills"
        # util/ 는 면제 — 인스턴스 소유 커스터마이즈 계층이다. 팀이 자기 팀의 실제
        # 연결 서비스를 문서화하는 util 스킬(예: 일정 스킬이 그 팀의 캘린더 제품과
        # mcp__ 툴을 그대로 적는 것)에는 제품명·mcp__ 직표기가 정당하다.
        # K7(§2.12·§7.3)의 역할어휘 규칙은 제품 스킬(base/core)의 provider-불가지
        # 원칙을 지키는 것이 목적이므로 util 에는 적용하지 않는다.
        # files 명시 주입 시(아래 else)는 필터링하지 않는다 — 검사 대상은 호출자 몫.
        util_dir = skills_dir / "util"
        candidates = sorted(
            p for p in skills_dir.rglob("SKILL.md")
            if util_dir not in p.parents
        )
    else:
        candidates = [Path(f) for f in files]

    products = _provider_product_names(root)
    # 제품명은 단어경계로 매칭(대소문자 무시) — 'googler' 같은 부분일치 거짓양성 차단.
    prod_re = re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(p) for p in sorted(products)) + r")(?![A-Za-z])",
        re.IGNORECASE,
    ) if products else None

    hits = []
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # frontmatter 의 트리거 예문에도 제품명/mcp__ 가 새지 않게 본문 전체를 검사한다
        # (프롬프트로 주입되는 description 도 본문의 일부 — §2.12 의 시맨틱 참조 원칙 적용).
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = p
        for i, line in enumerate(text.splitlines(), 1):
            if "mcp__" in line:
                hits.append(f"{rel}:{i} (mcp__ 직표기)")
            if prod_re is not None:
                for m in prod_re.finditer(line):
                    hits.append(f"{rel}:{i} (제품명 '{m.group(1)}')")
    return ("스킬 본문 정규형", not hits,
            f"역할어휘 위반(mcp__·제품명 직표기): {hits}" if hits else "")


def run_lint(root) -> LintReport:
    root = Path(root)
    checks = []
    checks.append(_lint_manifest_canonical(root))
    checks.append(lint_no_tracked_secrets(root))
    checks.append(lint_skill_canonical(root))
    return LintReport(checks=checks)


# ──────────────────────────────────────────────────────────────────
# 실제 엔진 어댑터 (CLI에서 verify/conform 시 사용)
# ──────────────────────────────────────────────────────────────────

class SubprocessEngine:
    """엔진 CLI를 subprocess로 호출하는 실엔진 하니스.

    engine_cmd: argv 앞에 붙는 실행 prefix (예: ["python3", "infra/teammode.py"]).
    cwd: 엔진 작업 디렉토리(= 팀 루트). 파일 부작용도 여기 기준.
    """

    def __init__(self, engine_cmd: list, cwd):
        self.engine_cmd = list(engine_cmd)
        self.root = Path(cwd)

    # 격리에 필요한 최소 ambient 변수만 통과시킨다. 그 외(특히 TEAMMODE_HOME·
    # TGATES_HOME 같은 팀 루트 지시 변수)는 절대 상속하지 않는다 — `env -i` 정신.
    # ※ 엔진은 더 이상 env 로 팀 루트를 받지 않는다(P1) — 팀 루트는 `--root` 로 명시
    #   전달한다. env 화이트리스트는 2차 방어선(혹 다른 구현이 env 를 읽어도 누수 0).
    _PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
                    "SYSTEMROOT", "PATHEXT", "TZ", "PYTHONPATH", "TERM")

    def _isolated_env(self) -> dict:
        """ambient를 차단하고 필수 변수만 담은 env. 팀 루트 지시 변수는 통과 안 됨."""
        return {k: os.environ[k] for k in self._PASSTHROUGH if k in os.environ}

    def run(self, argv) -> Result:
        # 엔진을 run root(=검사 대상 팀 루트)에 고정한다. 팀 루트는 `--root` 명시 인자로
        # 전달하고(P1: env 비신뢰), env 화이트리스트로 ambient TEAMMODE_HOME/TGATES_HOME
        # 누수도 차단한다(이중 방어, docs/spec/internals.md §1.2). 첫 토큰(동사) 뒤에 --root 를 끼운다.
        argv = list(argv)
        if argv:
            full = self.engine_cmd + [argv[0], "--root", str(self.root)] + argv[1:]
        else:
            full = self.engine_cmd + ["--root", str(self.root)]
        proc = subprocess.run(
            full,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self._isolated_env(),
        )
        return Result(proc.returncode, proc.stdout, proc.stderr)


def _print_report(report: Report) -> int:
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.id} ({r.tier_signal})")
        if not r.passed:
            for a in r.assertions:
                if not a.passed:
                    print(f"        ✗ {a.kind}: {a.detail}")
    if report.tier is not None:
        t = report.tier
        if t.compliant:
            print(f"\nTier {t.tier} — advisory 순응률 {t.advisory_compliance:.0%}")
        else:
            print("\n비호환: 결정적 시나리오 실패")
    print(f"\n{'GREEN' if report.green else 'RED'}: "
          f"{sum(1 for r in report.results if r.passed)}/{len(report.results)} 통과")
    return 0 if report.green else 1


def _ensure_utf8_io() -> None:
    """stdout/stderr UTF-8 보장 — Windows native 인코딩(cp949)에서 한글 print 크래시 방지.

    infra/io_encoding 을 재사용(단일 소스)하되, conformance 를 infra 에 강결합하지 않도록
    경로 추가는 lazy. 못 찾으면 조용히 무동작(검수가 import 실패로 깨지지 않게).
    """
    try:
        infra = Path(__file__).resolve().parent.parent / "infra"
        if str(infra) not in sys.path:
            sys.path.insert(0, str(infra))
        from io_encoding import ensure_utf8_io
        ensure_utf8_io()
    except Exception:
        pass


def main(argv=None) -> int:
    # 한글 PASS/FAIL·에러 메시지가 비-UTF8 stdout(Windows)에서 크래시하지 않도록 진입 즉시 보정.
    _ensure_utf8_io()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="teammode check", description=__doc__)
    parser.add_argument("mode", choices=["lint", "verify", "conform"])
    parser.add_argument("--root", default=".", help="팀 루트 (검사 대상 레포)")
    parser.add_argument("--engine", default=None,
                        help="엔진 실행 prefix (예: 'python3 infra/teammode.py'). "
                             "verify/conform에 필요")
    parser.add_argument("--scenario-dir", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()

    if args.mode == "lint":
        report = run_lint(root)
        for name, ok, detail in report.checks:
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
        return 0 if report.ok else 1

    if args.engine is None:
        print("[error] verify/conform에는 --engine 이 필요합니다.", file=sys.stderr)
        return 2
    engine_cmd = args.engine.split()
    # 레퍼런스 엔진(teammode.py)은 settings 경로를 명시로만 받는다(P2). 검수는 실
    # ~/.claude 를 절대 건드리면 안 되므로, run root 하위 격리 settings 를 주입한다.
    # --settings 를 모르는 타 구현은 미지 플래그로 무시한다(§2 C2: 플래그 비강제).
    # 사용자가 이미 --settings 를 넣었으면 덮어쓰지 않는다.
    if "--settings" not in engine_cmd:
        engine_cmd = engine_cmd + ["--settings", str(root / ".teammode-settings.json")]
    engine = SubprocessEngine(engine_cmd, root)
    sdir = Path(args.scenario_dir) if args.scenario_dir else None
    report = run_mode(args.mode, engine, root, scenario_dir=sdir)
    return _print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
