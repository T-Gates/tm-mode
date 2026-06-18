---
name: tm-knowledge
description: Use when the user wants to load team knowledge into context. Triggers on "지식 불러와", "팀 지식", "메모리 로드", "knowledge", "지식 로드".
---

# tm-knowledge — 팀 지식 로드

## Overview

`memory/` 하위 지식의 **INDEX(요약)를 컨텍스트에 로드**한다. 지식 로드의 목적은 "무엇이 어디 있는지" 맥락 파악이고, 그건 INDEX만으로 충분하다. 개별 파일 전문은 그 깊이가 실제로 필요한 작업에 들어갈 때만 읽는다.

## When to Use

- "지식 불러와", "팀 지식", "메모리 로드", "knowledge", "지식 로드"
- "DB 스펙 좀 봐봐", "코드 컨벤션 읽어" 같이 **특정 문서를 콕 집은** 요청 → 제안 건너뛰고 그 파일만 바로 로드

## 절차

1. **레포 최신화**: 팀 루트에서 `python infra/teammode.py pull --root .` 실행(엔진 동사) — 다른 팀원이 push한 지식 반영.
2. **INDEX 계층 발견 및 로드** — 먼저 `memory/INDEX.md`를 폴더 지도로 읽는다. 그다음 `find memory -name INDEX.md`로 하위 INDEX를 실제 발견해 **전부** 읽는다. teammode는 제품 구조에 무관하므로 경로를 하드코딩하지 않고 동적으로 발견한다. INDEX는 각 문서의 한 줄 요약을 담고 있어 이것만으로 "무엇이 어디 있는지"가 파악된다.
3. **요약 제시** — 로드된 INDEX 기반으로 "지금 어떤 지식이 있는지"를 그룹별로 정리해 보여주고, **"특정 주제를 깊이 보려면 말해달라"**고 안내.
4. **전문 로드는 요청 시에만** — 사용자가 콕 집으면 그 파일만 Read.

## 규칙

- **기본은 INDEX만.** 폴더 전체·"전부" 전문 로드는 사용자가 명시적으로 요청할 때만. 묻지도 않고 전문을 컨텍스트에 들이붓지 않는다.
- **바이너리(pdf·jpg·png 등)는 절대 Read하지 않는다.** 경로·건수만 안내.
- **대용량 참조 폴더**(`prior-art` 등 자체 INDEX/README 보유) — README/INDEX만. 개별 파일은 명시 요청 시. **"다" / "전부" 같은 대량 지정이면** 개별 전문을 들이붓지 말고, README의 목록표를 보여준 뒤 **"어느 항목을 볼까요?"**로 범위를 좁힌다.
- 세션 로그(`team/sessions/`)·회의 원본은 다루지 않는다 (→ tm-context로 위임).

## 안 하는 것

- 파일 수정 (읽기 전용)
- 묻지 않은 전문 대량 로드
- 세션 로그 / 팀 현황 로드 (→ tm-context)
- 외부 API 호출(issues / docs / calendar 등 외부 서비스)
- FTS(전문 검색) — 백로그
