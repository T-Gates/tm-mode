#!/usr/bin/env python3
"""Codex normalize 심 — 런타임 통역사 (스펙 02 §6).

Codex 원어 훅 입력 JSON(stdin) → 정규 스키마(§6.1) → 공통 스크립트 stdin 전달.
변환 코어는 Claude normalize 와 공유(events.json 역매핑이 에이전트 무관). 이 파일은
Codex events.json 을 가리키도록 경로만 고정한다.

⚠️ 주의(스펙 02 부록 B / 초안 §12 미결): **Codex 실 훅 입력 JSON 스키마는 미확인**이다.
현 구현은 Claude 와 유사한 `{hook_event_name, tool_name, tool_input, prompt}` 형태를
가정한다. Codex 실환경 캡처 후 v0.2 에서 매핑을 확정한다(부록 B 이월 항목).
PreToolUse 는 events.json 에서 null(미지원)이라 Codex 에 애초 등록되지 않으므로
차단 시맨틱 전파는 이 에이전트에서 비활성이다.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # agents/codex
_CLAUDE_NORMALIZE = HERE.parents[0] / "claude" / "normalize.py"

# Claude normalize 모듈을 임포트해 함수를 재사용한다. 함수들의 __globals__(실제
# 모듈 네임스페이스)에 경로 상수를 다시 바인딩해야 Codex events.json·manifest 를 본다.
import importlib.util

_spec = importlib.util.spec_from_file_location("_claude_normalize", _CLAUDE_NORMALIZE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

# Codex 컨텍스트로 경로 상수 치환 (events.json·hooks 위치)
_base.HERE = HERE
_base.INFRA = HERE.parents[1]
_base.HOOKS_DIR = HERE.parents[1] / "hooks"
_base.MANIFEST = HERE.parents[1] / "hooks" / "manifest.json"
_base.EVENTS = HERE / "events.json"

# stdout/stderr UTF-8 보장 — Claude normalize main 을 그대로 재사용하며, 그 main 은
# 진입부에서 _ensure_utf8_io() 를 호출한다. _ensure_utf8_io 는 Claude 모듈
# 네임스페이스(io_encoding 가드 임포트 포함)에서 해석되므로 Codex 재방출도 동일하게
# 보정된다(내부 훅 stdout/stderr 재방출의 cp949 크래시 방지, 다른 훅과 동일 가드 패턴).
main = _base.main


if __name__ == "__main__":
    raise SystemExit(main())
