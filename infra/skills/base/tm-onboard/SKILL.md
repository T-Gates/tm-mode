---
name: tm-onboard
description: Use right after a teammode install (`teammode init` / `teammode join`) — when entering Claude Code/Codex in a freshly set-up team repo. Dispatches a verification subagent to confirm the install landed, and meanwhile conveys what teammode does for you. Triggers on "tm-onboard", "팀모드 온보딩", "팀모드 시작", "설치 잘 됐나", "팀모드 셋업 확인", or when the CLI tells the user to open an agent and run tm-onboard.
---

# tm-onboard — 설치 검증 + 팀모드 가치 전달

설치는 **CLI(`teammode init` / `teammode join`)가 이미 끝냈다.** 이 스킬은 사람이 셋업 직후 에이전트로 처음 들어왔을 때 **딱 두 가지만** 한다:

1. **설치가 제대로 됐나 확인** — **검증 서브에이전트에 위임**한다(메인은 기다리지 않는다).
2. **팀모드가 뭘 해주는지(가치)** — 검증이 도는 **그 동안** 메인이 사람에게 전한다.

> ⛔ **설치·질문을 하지 않는다.** 멤버명·org·팀명·역할·에이전트·obsidian 묻기, `install.py` 직접 호출, 레포 생성/clone — **전부 CLI wizard 몫이고 이미 끝났다.** 재현 금지. (아직 설치 안 된 상태로 "셋업해줘" 하면 → "`teammode init`(새 팀) / `teammode join <url>`(합류)을 터미널에서 실행하세요"로 CLI 진입 안내 후 멈춘다.)

## 진입 흐름 (병렬 — 이 순서대로)
1. 진입 즉시 **검증 서브에이전트를 띄운다**(§①). 읽기 전용·결과만 보고. **메인은 그 완료를 기다리지 않는다.**
2. 서브가 도는 동안 **메인은 §② 가치 전달**을 사람에게 진행한다 — 사람을 빈 화면으로 기다리게 두지 않는다.
3. 검증 결과가 도착하면 **종합**한다:
   - 전부 ✅ → "설치도 정상 확인됐어요" 한 줄로 매듭.
   - 빠진 게 있음(❌) → *무엇이* 안 됐는지 사람 말로 짚고 → `teammode join <팀레포 URL>` 을 같은 위치에 다시 실행하라고 안내(install 은 멱등 — 안전하게 덮어 채운다). **손으로 install.py 단계를 재현하지 말 것.**

> 진입 맥락: `teammode init/join` 을 마치면 CLI 가 *"Claude/Codex 열고 'tm-onboard' 입력하면 검증·브리핑이 자동"*이라고 안내한다(cli.py `_done()`). 그 첫 진입이 이 스킬이다. **팀 루트(clone된 레포)에서 실행**된다고 가정한다.

---

## ① 설치 검증 — 검증 서브에이전트로 위임

진입하자마자 아래 프롬프트로 **검증 전용 서브에이전트 1개**를 띄운다(읽기 전용, **수정·설치 절대 금지** — 검증만). 도그푸딩에서 "훅·스킬 심링크가 등록 안 됨"이 핵심 버그였으니 **통과를 가정하지 말고 실제 파일/명령으로 확인**시킨다.

> **[검증 서브에이전트 프롬프트 템플릿]** — `<팀루트>`·`<멤버명>`·`<에이전트>` 를 채워 디스패치:
>
> 팀 루트 `<팀루트 절대경로>` 에서 teammode 설치가 제대로 됐는지 **검증만** 해라. **수정·설치·git 쓰기 절대 금지(읽기 전용).** 아래 각 항목을 실제 명령/파일로 확인하고 ✅/❌ + 안 된 건 사유 한 줄로 표를 만들어 보고하라:
> 1. **코어 엔진**: `python infra/teammode.py context --root <팀루트> --json` 이 **에러 없이** state 를 출력하는가 (설치 직후 `state=off` 가 정상 — 설치 ≠ 활성화). 출력의 팀명·멤버수·세션수도 같이 적어라(가치 브리핑에 쓰임).
> 2. **scaffold**: `memory/team/members.md` 에 `<멤버명>` 이 등재됐는가, `memory/INDEX.md` 가 있는가.
> 3. **팀 config**: `team.config.json` 존재 + `agents` 가 기록됐는가.
> 4. **스킬 심링크**: 에이전트 스킬 디렉토리(claude=`~/.claude/skills`, codex=해당 경로)에 `tm`·`tm-onboard`·`tm-knowledge` 등 teammode 스킬이 심링크/설치돼 있는가.
> 5. **훅 배선**: 에이전트 설정(claude=`~/.claude/settings.json`)에 teammode 훅(session-start 등)이 들어갔는가.
>
> 마지막 줄에 **전체 판정**(전부 정상 / 빠진 항목 목록)을 한 줄로.

- 메인은 이 서브의 final 결과만 받아 §진입흐름 3 으로 종합한다. **서브 자기보고를 의심**해야 할 만큼 중요하면 빠진 항목만 메인이 직접 재확인.

---

## ② 팀모드 가치 전달 (검증이 도는 동안)

검증 서브를 띄운 **직후 곧바로**, **`infra/skills/base/tm-onboard/value.md` 를 읽고** 거기 담긴 가치를 사람·맥락(새 팀 창립 / 기존 팀 합류, 직군)에 맞게 **사람 말로 전한다.** 그대로 낭독하지 말고 — value.md 의 톤 가이드를 따라 요점을 네 말로, 짧게.

> 💡 가치 "내용"의 **단일 소스는 `value.md`** 다. 이 본문에 가치 문구를 중복하지 않는다 — 팀/창업자가 value.md 만 고치면 전달 메시지가 바뀐다.

그 다음, **검증 서브가 돌려준 현황**(팀명·멤버수·세션수)으로 "지금 팀 상황: …"을 덧붙인다.
- **갓 만든 팀은 비어 있다**(세션로그 0, KB 0) → 정상. value.md 톤대로 "구조는 섰고, **지금부터** 쌓입니다" — 빈 상태를 실패처럼 말하지 말 것.
- **마무리, 다음 한 걸음**: "작업을 시작할 땐 `tm on` 하세요 — 최신화하고 팀 맥락과 함께 엽니다." 셋업 직후의 "이제 뭐하지?"를 막는 단 하나의 안내. 여기서 끝낸다.

---

## 안 하는 것 / 경계
- **설치·질문·레포 생성·install.py 호출 안 함** — 전부 CLI(`teammode init/join`)가 끝냈다.
- **검증을 메인이 동기로 붙잡고 하지 않는다** — 서브에이전트에 위임하고, 메인은 가치 전달로 병렬 진행(사람을 기다리게 두지 않음).
- **메뉴 나열 안 함** — 서비스 연결(L2)·Obsidian 등록·배너/personality 커스텀은 *여기서 다루지 않는다*. 필요해지는 순간 각 스킬(`tm-connect`·`tm-customize`)이 트리거로 자연히 드러난다(progressive).
- 활성화(`tm on`)는 **권유까지만** — 실제 켜기·웰컴/배너는 `tm` 스킬 몫.
- 코드 작성·이슈 생성·푸시 안 함.

## Common Mistakes
| 실수 | 올바른 방법 |
|------|------------|
| 멤버명·org·팀명·역할을 다시 묻는다 | 묻지 않는다 — CLI wizard 가 이미 받았다 |
| 검증을 메인이 직접 동기로 하느라 사람을 기다리게 함 | **검증 서브에이전트 디스패치 + 그 동안 가치 전달**(병렬) |
| `install.py` 를 직접 호출해 설치를 재현 | 검증만. 안 됐으면 `teammode join <url>` 재실행 안내(멱등) |
| 검증을 건너뛰고 "설치됐겠지" 가정 | 서브에게 실제 파일/명령으로 확인시킨다 — 특히 훅·스킬 심링크 |
| L2·Obsidian·personality 를 메뉴로 늘어놓는다 | 다루지 않는다. 각 스킬이 그때 드러난다 |
| 빈 팀(세션로그 0)을 실패로 말한다 | 정상 — "지금부터 쌓인다"로 내레이션 |
| 설치 안 된 사람에게 스킬이 설치를 시작 | "`teammode init`/`join` 을 터미널에서" 로 CLI 진입 안내 후 멈춤 |

---
> 동작 명세는 `docs/spec/`(install.py·onboard 스킬), 진입 계약은 `src/teammode/cli.py` 의 `_done()`(이 스킬을 가리킨다)을 확인.
