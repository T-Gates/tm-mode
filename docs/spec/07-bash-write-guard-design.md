# 07 — Bash best-effort KB 쓰기 가드 설계

> 상태: design (brainstorm 2026-06-18, 은수) / 다음: writing-plans
> 의존: kb-write-guard(§2.10 거버넌스 훅), normalize 심(§2.10 정규 스키마), 엔진 knowledge/log 동사
> spec_version 영향: 정규 입력 스키마에 `shell_exec` action + `command` 필드 추가 = **minor bump**

## 1. 정체

현재 KB 거버넌스(`kb-write-guard`)는 **`Write`/`Edit` 직접편집만** 막는다. Bash 경로(`echo > memory/…`, `tee`, `sed -i` 등)는 코드 주석에 "현 범위 밖"으로 명시돼 있고, 윈도우 도그푸딩에서 `{tool_name:Bash}` 우회가 통과함을 실증했다.

이 설계는 **Bash 경로의 명백한 `memory/` 직접쓰기**를 best-effort로 추가 차단한다.

## 2. 위협 모델 (확정)

**부주의·습관 방지 (best-effort).** 에이전트가 무심코 `echo x > memory/…`·`cat >> memory/…` 같은 명백한 직접쓰기를 하는 것을 막는다.

- **범위 밖(의도적 우회는 막지 않음)**: 변수치환·heredoc·`python -c "open(...)"`·base64 디코드 후 실행·`eval` 등. Bash 는 임의 명령이라 "memory/ 를 쓰는지" 정적 완전판별이 **근본적으로 불가능**하다.
- 이 가드는 **보안 경계가 아니라 실수 방지턱**이다. 코드 주석·SPEC·NOTICE 전부 이 톤을 유지한다(과장 금지).
- 진짜 강제(파일시스템 권한·샌드박스)는 별도 큰 작업으로 접었다 — 위협 모델이 부주의 방지로 확정됨.

## 3. 구조 — 기존 `kb-write-guard` 확장 (별도 훅 신설 아님)

근거: 거버넌스 공통 로직(unlock 플래그·TTL·세션 매칭·`.tgates-active` 가드·팀루트 판별·fail-closed)을 이미 보유. Bash action 을 같은 훅이 처리하면 전부 재사용. 별도 훅이면 이 가드들이 중복돼 표류 위험.

손볼 4곳:

| # | 파일 | 변경 |
|---|------|------|
| 1 | `infra/agents/claude/events.json` | `actions` 에 `"shell_exec": "Bash"` 추가 |
| 2 | `infra/agents/claude/normalize.py` | action 이 `shell_exec` 면 `tool_input.command` 를 정규 스키마 `out["command"]` 로 추출 (현재 `file_path`→`files` 만 처리) |
| 3 | manifest / settings 매처 | `kb-write-guard` 를 PreToolUse Bash(=`shell_exec`)에도 등록 (install_lib 의 hook 매처 확장) |
| 4 | `infra/hooks/kb-write-guard.py` | `action == "shell_exec"` 분기: `command` 정규식 스캔 → memory/ 쓰기패턴이면 deny |

정규 스키마 추가형 (§2.10):
```json
{ "event": "PreToolUse",
  "tool":  { "kind": "builtin", "name": "Bash" },
  "action": "shell_exec",
  "command": "echo x > memory/team/foo.md",
  "agent": "claude", "raw": {...} }
```

## 4. 스캔 로직 (`command` 문자열)

`memory/` 하위(팀루트 기준)를 **쓰기 대상**으로 삼는 명백한 쉘 패턴이면 deny.

| deny (명백한 쓰기) | allow (통과) |
|---|---|
| `>` · `>>` 리다이렉트가 `memory/…` 대상 | 읽기: `cat`/`grep`/`head`/`tail`/`less` memory/ |
| `tee [-a] memory/…` | **동사 호출**: `teammode.py log\|knowledge …` (python 호출, 쓰기패턴 아님 → 자연 통과) |
| `sed -i … memory/…` | unlock 플래그 활성 시 (동사 경유와 일관) |
| `cp`/`mv` 의 **목적지**가 memory/ | `.tgates-active` off (일상작업 — 빌드 안전) |
| `rm`/`rmdir`/`truncate`/`dd of=` 가 memory/ 대상 | |

- deny 시: exit2 + `hookSpecificOutput` JSON + "memory/ 직접쓰기 금지 → `knowledge`/`log` 동사 경유" 안내 + best-effort 한계 한 줄.
- 경로 매칭: 팀루트 기준 `memory/` 상대·절대 양쪽. 읽기 명령(첫 토큰이 cat/grep 등)은 리다이렉트 없으면 통과.

## 5. 거버넌스 공통 재사용 (기존 가드와 동일)

- `.tgates-active` 없으면(teammode off) 통과 — 빌드/일상 안전.
- unlock 플래그(`teammode.py knowledge` 동사가 touch/rm, TTL·세션ID 매칭) 활성 시 통과 → 동사 경유 일관.
- `CLAUDE_SESSION_ID` 없으면 fail-closed(기존 정책 유지).
- codex 어댑터: PreToolUse=null 이라 이 훅 미등록 → 어댑터 sync 가 [warn] 으로 표면화(기존과 동일, 현 릴리스 폴백).

## 6. 한계 (정직 문서화 — 반드시 명시)

다음은 **막지 못한다**(best-effort 범위 밖):
- 변수치환(`f=memory/x; echo > $f`), heredoc, `python -c "open('memory/x','w')"`, `printf … | tee`(파이프 우회), base64/eval, 심볼릭/하드링크 우회.
- 따라서 이 가드는 "동사로만 쓴다"의 **습관 유도**이지 기술적 완전강제가 아니다. SPEC §2.10·NOTICE·코드 주석에 이 한계를 명시한다.

## 7. 테스트 (conformance)

- **deny**: `> memory/…`, `>> memory/…`, `tee memory/…`, `sed -i … memory/…`, `cp src memory/…`, `mv src memory/…`, `rm memory/…` (각각 on + 정규형 + 플래그 없음 → exit2).
- **allow**: `cat memory/…`(읽기), `python infra/teammode.py knowledge write …`(동사), unlock 플래그 활성, `.tgates-active` off.
- **한계 실증(문서 테스트)**: 변수치환·heredoc·`python -c open` 우회는 **통과(deny 안 됨)**를 명시적으로 테스트해 best-effort 한계를 회귀 고정.
- end-to-end: claude raw `{tool_name:Bash,tool_input:{command}}` → normalize → 가드 → deny 까지 체인.

## 8. 미결 / 결정 필요

- **매처 등록 방식(#3)**: settings.json PreToolUse matcher 를 `Write|Edit|Bash` 로 합칠지, 별도 엔트리로 둘지 — install_lib 코드 확인 후 writing-plans 에서 확정.
- **경로 파싱 견고성**: best-effort 라 정규식 수준으로 충분한가, 아니면 최소한의 토크나이즈(shlex)까지 갈지. 과도한 정교함은 위협 모델(부주의)에 비해 over-engineering — 정규식 + 명백 패턴으로 출발 권장.
- SPEC 본문(§2.10) 편입 시점: 구현·검수 통과 후.
