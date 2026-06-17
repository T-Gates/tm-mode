---
name: tm
description: Use when the user wants to enable or disable team mode. Triggers on "팀 모드 켜", "tm on", "tm off", "팀 모드 꺼", "teammode on", "teammode off".
---

# tm — 팀 모드 토글

## Overview

팀 모드를 켜거나 끄는 L1 코어 스킬. ON 시 레포 최신화 + 엔진 배선 + 팀 맥락 주입, OFF 시
세션 로그 저장 + 커밋(push 는 사람 게이트).

## When to Use

- "팀 모드 켜", "tm on", "teammode on"
- "팀 모드 꺼", "tm off", "teammode off"

## 안 하는 것

- 코드 작성, 이슈 생성, 서비스 연결, 다른 스킬 자동 호출 — 이 스킬은 토글만 한다.
- 이슈 트래커·캘린더·채팅(L2 서비스) 조회 — L1 코어 범위 밖, L2 서비스가 연결되면 다른 스킬이 처리.
- push — commit 까지만. push 는 사람이 직접.

## 환경

- 팀 레포 위치는 `--root` 인자로 명시한다. 환경변수(TEAMMODE_HOME 등)는 읽지 않는다.
- 현재 사용자 이름은 `git config user.name`을 *제안값*으로 보여주되 **반드시 사용자 확인** 후 쓴다.
- `--install` 플래그로 실호스트(`~/.claude/settings.json`)에 훅을 배선한다.

## ON 절차

1. **레포 최신화**: `python infra/teammode.py pull --root .`
   - 실패해도 비치명 — 오프라인이거나 이미 최신이면 계속 진행.

2. **팀모드 켜기**: `python infra/teammode.py on --root . --install`
   - 엔진이 배너 출력, greeting 출력(team.config.json에 있으면), adapter sync(mode=on),
     `.acme-active` 마커 생성, upstream fetch + NOTICE 비교 알림까지 한다.
   - NOTICE 알림: upstream `NOTICE.md`가 로컬과 다르면 `[공지] teammode 최신 업데이트: …
     — 받으려면 \`teammode update\`` 를 출력. 같으면 조용히 생략(매번 도배 방지).
   - 스킬은 이 동사를 호출하고 출력을 그대로 사용자에게 보여준다.

3. **맥락 주입**: `python infra/teammode.py context --root . --json`
   - JSON 결과를 파싱해 "지금 팀 상황: …" 형식으로 요약한다.
   - 멤버별 최근 summary + 날짜를 표시한다.
   - state=on: 정상. state=off: 배선 문제 — "훅 배선을 확인하세요(`tm-onboard`)"로 안내.
   - **세션로그 0개**: "구조는 섰고, 다음 작업부터 자동 기록·주입됩니다"로 안내.

4. (세션로그가 없는 갓 셋업된 팀) → "다음 작업부터 자동 기록·주입됩니다" 안내.

## OFF 절차

0. **⚠️ 확인**: "팀 모드를 끌까요?" 사용자에게 한번 더 확인받은 후 진행. 확인 없이 끄지 않는다.

1. **세션로그 기록**: 이번 세션에서 한 작업을 묻는다(또는 사용자가 미리 말했으면 그대로).
   ```bash
   python infra/teammode.py log --root . --author <이름> --text "<내용>"
   ```
   - `<이름>`: 사용자 확인 후 확정한 영문 이름.
   - `<내용>`: 세션 작업 내역 요약 (아래 "세션 로그 형식" 참고).
   - 이름 확인이 안 됐으면 먼저 묻는다(`git config user.name`을 제안값으로).

2. **커밋**: 세션로그(memory/ 디렉터리만)를 팀 레포에 커밋한다.
   ```bash
   python infra/teammode.py commit --root . --paths "memory/" --message "session: <이름> <날짜>"
   ```
   - `--paths memory/` 로 스테이징 범위를 세션로그 디렉터리에 한정한다 — 작업 중인 코드(infra/ 등) **전체 워킹트리를 휩쓸지 않는다**.
   - **push 는 하지 않는다** — commit 까지만. push 는 사람이 직접 결정.

3. **팀모드 끄기**: `python infra/teammode.py off --root . --install`
   - 엔진이 adapter sync(mode=off), `.acme-active` 마커 삭제, farewell 출력을 한다.
   - 스킬은 이 동사를 호출하고 출력을 사용자에게 보여준다.

## 세션 로그 형식 (--text 에 들어갈 내용)

```
## 작업 내역
- "무엇을 했다"로 끝내지 말 것. **왜 그렇게 했는지(근거), 검토했다 접은 대안,
  누가 무엇을 결정했는지, 핵심 디테일**까지 한 흐름으로 녹인다. 나중에 읽어도
  그때 맥락이 살아있게. (개인 내용 제외, 팀 작업만)

## 막힌 점 / 시도
- 걸린 문제, 시도한 것, 해결/미해결 (없으면 생략)

## 다음 할 일
- 이어서 할 작업, 미결정 항목
```

## 엔진 동사 호출 요약

| 절차 | 명령 | 비고 |
|------|------|------|
| ON — 최신화 | `teammode.py pull --root .` | 실패=비치명 |
| ON — 배선·배너 | `teammode.py on --root . --install` | 배너·greeting·sync·마커 |
| ON — 맥락 | `teammode.py context --root . --json` | 스킬이 파싱해 요약 |
| OFF — 세션로그 | `teammode.py log --root . --author <이름> --text <내용>` | 하루 1파일 append |
| OFF — 커밋 | `teammode.py commit --root . --paths "memory/" --message <메시지>` | memory/ 만 stage · push 절대 금지 |
| OFF — 훅 해제 | `teammode.py off --root . --install` | sync=off·마커 삭제 |

## Common Mistakes

| 실수 | 올바른 방법 |
|------|------------|
| OFF 할 때 세션 로그 없이 종료 | 반드시 세션 로그를 먼저 기록 |
| `--push` 플래그 사용 | commit 까지만 — push 는 사람 직접 |
| 이름을 git/계정/이메일에서 추론해 임의 확정 | git user.name 은 *제안값* — 사용자 확인 후 확정 |
| ON 할 때 pull 생략 | 항상 최신화 먼저(실패해도 계속) |
| `TEAMMODE_HOME` 환경변수로 레포 경로 추측 | `--root .` 명시 필수(엔진 정책 A) |
| 이슈 트래커·캘린더·채팅 등 L2 서비스 조회 | tm 은 L1 토글만. L2 는 연결 후 다른 스킬이 처리 |
| 멤버 표시 장식 출력 | 멤버 표시 장식(이모지 등)은 tm 범위 밖 — members.md 이름·team.config.json members.role 이 멤버 기준이며, 시각 장식은 후속 인프라(session-start 훅)가 담당 |
| context 결과를 그대로 dump | 스킬이 파싱해 "지금 팀 상황: …"으로 사람 말로 요약 |

---

> 동작이 예상과 다르면 `docs/spec/` 또는 `infra/teammode.py` 주석을 확인하세요.
