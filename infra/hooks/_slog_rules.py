"""_slog_rules — 세션로그 규칙 텍스트의 단일 소스 (compact hook context).

session-start.py(SessionStart 1회 주입)와 session-log-remind.py(compact 리마인더의
참조 문구)가 이 모듈을 공유한다. 리마인더가 "(규칙: 세션 시작 주입 참조)"라고
가리키는 블록이 바로 SESSION_LOG_RULES 다 — 두 훅이 각자 문구를 들고 있으면
드리프트하므로 여기 한 곳에만 둔다.

배경: Codex 가 additionalContext 를 "hook context:" 로 화면에 그대로 노출한다.
장문 룰셋(위치·Read/Edit 방법·frontmatter·06시 컷·전체 Read 금지·log 동사 금지·
개인내용 제외)이 N번째 프롬프트마다 반복되면 화면이 도배된다. 해결은 숨김이 아니라
**압축 주입** — 리마인더는 동적 상태(N/경로/offset)만 1~3줄, 규칙은 세션 시작 1회.

시블링 임포트: 훅은 스크립트로 실행되므로 스크립트 디렉토리(infra/hooks/)가
sys.path[0] 에 자동 포함된다 — auto_pull/git_ops 와 동일 패턴. 임포트 실패는
각 훅이 advisory 폴백으로 처리한다(훅은 절대 세션을 막지 않는다).
"""
from __future__ import annotations

# 리마인더(compact)가 상세 규칙 위치를 가리키는 참조 문구.
RULES_REF = "(규칙: 세션 시작 주입 참조)"

# SessionStart 가 세션당 1회 주입하는 압축 규칙 블록 (최대 6줄).
SESSION_LOG_RULES = (
    "--- 세션로그 규칙 (리마인더의 '규칙: 세션 시작 주입 참조'가 가리키는 블록) ---\n"
    "- 하루 1파일: memory/team/sessions/<이름>/YYYY-MM-DD.md — 06시 컷"
    "(00:00~05:59는 전날 날짜), -late 등 분리 금지\n"
    "- frontmatter(author/date/summary) 필수 — <이름>은 members.md의 영문 이름"
    "(OS 사용자명 아님)\n"
    "- 이어쓰기는 끝부분만 Read(offset)+Edit — 전체 Read·log 동사 금지(컨텍스트 절약·충실도)\n"
    "- 본인 세션로그는 가드 예외 — append뿐 아니라 직접 수정·재구성·요약 갱신 가능\n"
    "- 팀 작업만 기록(개인 내용 제외) — 현재 작업 레포의 ./memory/ 에는 쓰지 말 것"
)
