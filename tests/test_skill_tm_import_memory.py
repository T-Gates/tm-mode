"""#51 PR-B — tm-import-memory 스킬 계약 테스트.

스킬은 산문이라 실행 검증 불가 — 계약 문구를 문맥 앵커와 함께 고정한다(드리프트 방지).
계약(이슈 #51 설계 확정 + codex 검수 반영):
  - 외부 문서(docs 슬롯) 대량 import 전담, provider 중립(역할 어휘 원칙)
  - preview 단일 확인 게이트 = weight·route desc 일괄 승인
  - 상한 20페이지/깊이 2, 주제별 병합(1:1 금지, ~10파일)
  - 저장은 엔진 memory write 경유(직접 Edit/Write 금지), 본문 `## 출처` 절
  - 신규 최상위 폴더는 route upsert 선행(--path·--desc·--author 전 인자)
  - 부분 재실행은 기존 파일 읽어 출처 단위 갱신(전체 교체 유실 방지)
  - 🔥 자동 제안 금지
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "infra" / "skills" / "core" / "tm-import-memory" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _description() -> str:
    m = re.match(r"^---\nname: tm-import-memory\ndescription: (.+?)\n---\n",
                 _text(), re.S)
    assert m, "frontmatter(name/description) 형식 위반"
    return m.group(1)


def test_skill_exists_with_frontmatter():
    assert SKILL.is_file(), "core/tm-import-memory/SKILL.md 부재"
    _description()


def test_triggers_role_vocabulary():
    """트리거는 역할 어휘 중심 — '메모리 업로드' 필수, docs 슬롯 언급."""
    desc = _description()
    assert "메모리 업로드" in desc
    assert "docs slot" in desc
    assert "tm-manage-memory" in desc  # 경계 명시


def test_provider_neutral_wording():
    """본문은 provider 중립 — services.docs.provider 를 읽어 말하는 원칙 명문화."""
    text = _text()
    assert "services.docs.provider" in text, "provider 이름의 소스(config) 미명시"
    assert "do not hardcode" in text
    # 출처 표기는 provider placeholder — 특정 제품명 고정 표기가 아님
    assert "(<docs provider name>, collected YYYY-MM-DD)" in text


def test_limits_in_scope_section():
    """상한(20페이지·깊이 2)이 범위 파악 절에 있고, 초과 시 선택 진행."""
    text = _text()
    scope = text.split("### 1.")[1].split("### 2.")[0]
    assert "20 pages" in scope and "depth 2" in scope
    assert "do not proceed without list confirmation" in scope.lower()


def test_preview_gate_covers_weight_and_route_desc():
    """preview 단일 확인이 weight 와 route desc 승인을 겸함 + 📎 기본·🔥 금지."""
    text = _text()
    gate = text.split("### 2.")[1].split("### 3.")[0]
    assert "preview" in text.lower() and "**one** confirmation" in gate
    assert "📎" in gate
    assert "Never auto-propose 🔥" in gate
    assert "--desc" in gate or "desc" in gate  # route 설명까지 이 게이트에서 승인
    assert "no 1:1" in text  # 병합 규칙은 §4(Sources)로 이동 — 규칙 존재 계약은 전문 기준


def test_route_upsert_full_command():
    """route upsert 지시는 실행 가능한 전체 인자(--path·--desc·--author) 명시."""
    text = _text()
    save = text.split("### 4.")[1].split("### 5.")[0]
    assert "memory route upsert" in save
    for flag in ("--path", "--desc", "--author"):
        assert flag in save, f"route upsert 지시에 {flag} 부재(실행 불가 지시)"


def test_memory_write_via_engine_and_no_direct_edit():
    """저장은 엔진 memory write 경유 + memory/ 직접 Edit/Write 금지 + `## 출처` 절."""
    text = _text()
    save = text.split("### 4.")[1].split("### 5.")[0]
    assert "memory write" in save
    assert "## Sources" in save
    assert "directly Edit/Write" in save  # ⛔ Do not directly Edit/Write memory/


def test_partial_rerun_merge_rule():
    """부분 재실행: 기존 파일 읽어 해당 출처만 갱신 — 전체 교체 유실 방지 규칙."""
    text = _text()
    assert "Partial rerun" in text
    assert "read the existing file first" in text
    assert "update only the rerun source/page portion" in text


def test_fanout_subagent_boundaries():
    """fan-out 계약: 본문은 서브에이전트만, 서브는 파일을 직접 쓰지 않음."""
    text = _text()
    fanout = text.split("### 3.")[1].split("### 4.")[0]
    assert "subagent" in fanout.lower()
    assert "Does not write files directly" in fanout


def test_unconnected_docs_slot_stops_at_tm_connect():
    """미연결이면 tm-connect 안내 후 정지 — 연결 실행은 이 스킬 밖."""
    text = _text()
    step0 = text.split("### 0.")[1].split("### 1.")[0]
    assert "tm-connect" in step0
    assert "**stop**" in step0
