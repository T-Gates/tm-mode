"""Codex normalize 런타임 통역 테스트.

Codex 훅 입력은 Codex 문서의 hook payload 예시처럼 `hook_event_name`,
`tool_name`, `tool_input` 를 사용한다. Claude normalize 코어를 재사용하되
Codex events.json 의 `mcp_tool_format=mcp__{server}__{tool}` 과 `apply_patch` 액션 매핑이
적용되는지 검증한다. Codex session tool-call 기록에서 확인한 top-level `name`/`input`
형태도 apply_patch 호환 입력으로 검증한다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


@pytest.fixture
def env(tmp_path):
    root = tmp_path
    hooks = root / "infra" / "hooks"
    codex = root / "infra" / "agents" / "codex"
    claude = root / "infra" / "agents" / "claude"
    hooks.mkdir(parents=True)
    codex.mkdir(parents=True)
    claude.mkdir(parents=True)

    (codex / "normalize.py").write_text(
        (REPO / "infra" / "agents" / "codex" / "normalize.py").read_text(),
        encoding="utf-8",
    )
    (codex / "events.json").write_text(
        (REPO / "infra" / "agents" / "codex" / "events.json").read_text(),
        encoding="utf-8",
    )
    (claude / "normalize.py").write_text(
        (REPO / "infra" / "agents" / "claude" / "normalize.py").read_text(),
        encoding="utf-8",
    )

    (hooks / "echo-stub.py").write_text(
        "import sys\nd=sys.stdin.read()\nsys.stdout.write(d)\nsys.exit(0)\n",
        encoding="utf-8",
    )
    (hooks / "kb-write-guard.py").write_text(
        (REPO / "infra" / "hooks" / "kb-write-guard.py").read_text(),
        encoding="utf-8",
    )

    def write_manifest(entries):
        (hooks / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")

    def run_normalize(script, raw_input, manifest):
        write_manifest(manifest)
        return subprocess.run(
            [PY, str(codex / "normalize.py"), script],
            input=json.dumps(raw_input),
            capture_output=True,
            text=True,
            cwd=str(root),
            env={**os.environ, "TEAMMODE_HOME": str(root)},
        )

    class E:
        pass

    e = E()
    e.root = root
    e.run = run_normalize
    return e


def test_codex_pretooluse_mcp_payload_maps_to_canonical_tool(env):
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__linear__create_issue",
        "tool_input": {"title": "x"},
    }
    proc = env.run("echo-stub.py", raw, [
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "echo-stub.py"},
    ])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["event"] == "PreToolUse"
    assert out["agent"] == "codex"
    assert out["tool"] == {
        "kind": "mcp",
        "server": "linear",
        "name": "create_issue",
    }


def test_codex_apply_patch_payload_maps_to_file_edit(env):
    raw = {
        "hook_event_name": "PostToolUse",
        "tool_name": "apply_patch",
        "tool_input": {"file_path": "/abs/x.md"},
    }
    proc = env.run("echo-stub.py", raw, [
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"},
    ])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["event"] == "PostToolUse"
    assert out["action"] == "file_edit"
    assert out["files"] == ["/abs/x.md"]


def test_codex_apply_patch_patch_text_extracts_all_paths(env):
    patch = (
        "*** Begin Patch\r\n"
        "*** Update File: README.md\r\n"
        "@@\r\n"
        "-old\r\n"
        "+new\r\n"
        "*** Add File: docs/with space.md\r\n"
        "+hello\r\n"
        "*** Delete File: obsolete.md\r\n"
        "*** Update File: src/old name.py\r\n"
        "*** Move to: src/new name.py\r\n"
        "@@\r\n"
        "-old\r\n"
        "+new\r\n"
        "*** End Patch\r\n"
    )
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {"patch": patch},
    }
    proc = env.run("echo-stub.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"},
    ])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["event"] == "PreToolUse"
    assert out["action"] == "file_edit"
    assert out["files"] == [
        "README.md",
        "docs/with space.md",
        "obsolete.md",
        "src/old name.py",
        "src/new name.py",
    ]


def test_codex_apply_patch_pretooluse_single_outside_file_reaches_guard(env):
    (env.root / ".teammode-active").write_text("", encoding="utf-8")
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: infra/teammode.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )
        },
    }
    proc = env.run("kb-write-guard.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "kb-write-guard.py", "fallback": "runtime",
         "enforcement": "block", "strict": True},
    ])
    assert proc.returncode == 0


def test_codex_apply_patch_pretooluse_single_memory_file_reaches_guard_deny(env):
    (env.root / ".teammode-active").write_text("", encoding="utf-8")
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: memory/secret.md\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )
        },
    }
    proc = env.run("kb-write-guard.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "kb-write-guard.py", "fallback": "runtime",
         "enforcement": "block", "strict": True},
    ])
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_apply_patch_multiple_nonmemory_files_pass(env):
    # 다중 파일이어도 memory/ 를 안 건드리면 통과 — 정상 다중파일 편집 허용.
    # (guard 가 각 파일 개별 판정: memory/ 파일이 하나도 없으면 무영향.)
    (env.root / ".teammode-active").write_text("", encoding="utf-8")
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: infra/a.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** Update File: infra/b.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )
        },
    }
    proc = env.run("kb-write-guard.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "kb-write-guard.py", "fallback": "runtime",
         "enforcement": "block", "strict": True},
    ])
    assert proc.returncode == 0


def test_codex_apply_patch_multiple_files_with_memory_denied(env):
    # 다중 파일 중 하나라도 memory/ 면 차단 — 전수 검사로 [밖, memory/...] 혼합 우회 차단.
    (env.root / ".teammode-active").write_text("", encoding="utf-8")
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: infra/a.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** Update File: memory/secret.md\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )
        },
    }
    proc = env.run("kb-write-guard.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "kb-write-guard.py", "fallback": "runtime",
         "enforcement": "block", "strict": True},
    ])
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_apply_patch_input_field_is_also_treated_as_patch_text(env):
    raw = {
        "hook_event_name": "PostToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "input": (
                "*** Begin Patch\n"
                "*** Update File: README.md\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )
        },
    }
    proc = env.run("echo-stub.py", raw, [
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"},
    ])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["files"] == ["README.md"]


def test_codex_apply_patch_command_field_is_treated_as_real_hook_patch_text(env):
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "command": (
                "*** Begin Patch\n"
                "*** Update File: target.txt\n"
                "@@\n"
                "-alpha\n"
                "+beta\n"
                "*** End Patch\n"
            )
        },
    }
    proc = env.run("echo-stub.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"},
    ])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["action"] == "file_edit"
    assert out["files"] == ["target.txt"]


def test_codex_apply_patch_top_level_name_input_extracts_paths(env):
    raw = {
        "hook_event_name": "PreToolUse",
        "name": "apply_patch",
        "input": (
            "*** Begin Patch\n"
            "*** Update File: README.md\n"
            "@@\n"
            "-old\n"
            "+new\n"
            "*** End Patch\n"
        ),
    }
    proc = env.run("echo-stub.py", raw, [
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"},
    ])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["tool"] == {"kind": "builtin", "name": "apply_patch"}
    assert out["action"] == "file_edit"
    assert out["files"] == ["README.md"]
