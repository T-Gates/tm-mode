# teammode 시나리오

이 폴더는 teammode 기능명세서(행동별 유저 여정)다.

시나리오=목표 흐름이자 명세, 구현을 여기 맞춰 깎아간다(드림스테이트와 현재동작을 하나로 수렴).

| 행동 | 역할 | 파일 | 상태 |
|---|---|---|---|
| 도입자 온보딩 | introducer | [onboard-introducer.md](onboard-introducer.md) | L1 셋업 + L2 서비스 연결 |
| 팀원 온보딩 | member | [onboard-member.md](onboard-member.md) | L1 셋업 + L2 서비스 연결 |
| 초기화 | introducer/member | [reset.md](reset.md) | install.py --uninstall 직접 |

## 원문 서문

# teammode 시나리오 (기능명세서)

> 스킬 이름 표기: 이 문서는 L1 셋업 스킬을 **`tm-join`** 으로 부른다. **현 코드상 스킬 디렉토리·이름은 `tm-onboard`(`infra/skills/base/tm-onboard/`) 이며, `tm-join` 으로 리네임이 확정돼 곧 적용된다.** 명령·출력 문자열·exit code 는 전부 현재 코드(`infra/install.py`, `infra/install_lib.py`, `infra/teammode.py`) 기준이다.

## 이 문서의 역할

이 문서는 teammode 개발의 **중심 기능명세서**다. "유저가 이렇게 행동하면 시스템이 이렇게 반응한다"를 사용자 시점으로 명세해, 구현이 명세대로 동작하는지 **대조하는 기준점**이 된다. `conformance/` 의 골든(기계 검증)이 정확한 출력 문자열·exit code 를 자동으로 잡는다면, 이 문서는 그 **사람용 짝** — 한 사람이 처음부터 끝까지 따라가며 "맞게 흘러가는지" 읽고 판단하는 서사다. 기획·구현·QA 가 같은 그림을 공유하기 위해, 추측·창작 없이 코드와 문서에 실제로 적힌 동작만 적었다.

진입점은 항상 **자연어**다. 사용자는 슬래시 명령을 외우지 않는다 — "이 레포 셋업해줘" 같은 말을 하면 에이전트가 스킬(`tm-join`/`tm-connect`)을 골라 `install.py`(결정적 기계)를 대신 호출하고, 결과를 사람 말로 옮긴다.

### 시나리오 인덱스

| # | 시나리오 | 핵심 | role 판정 |
|---|---|---|---|
| 1 | [도입자](#시나리오-1--도입자) | 팀을 처음 만드는 첫 사람. `team.config.json` 없음 → config 를 새로 쓴다 | `introducer` |
| 2 | [팀원](#시나리오-2--팀원) | 이미 만들어진 팀 레포에 합류. config 유효 → 읽기만, 자기 엔트리만 upsert | `member` |

각 시나리오는 3국면으로 나뉜다:
- **국면 ① 레포 clone** — 레포를 손에 넣고 에이전트에 말 거는 지점
- **국면 ② `tm-join` L1 셋업** — `install.py` 부트스트랩(메모리 + 훅 배선 + verify)
- **국면 ③ `tm-connect` L2 서비스 연결** — 역할 슬롯(issues/chat/docs/calendar)에 서비스 붙이기

각 단계는 **4박자**로 적는다: (a) 사용자가 정확히 뭘 입력/실행 → (b) 에이전트가 뭐라 말하고 내부적으로 무슨 명령을 실행 → (c) 터미널/화면에 실제로 뭐가 출력(실제 출력 문자열·exit code) → (d) 사용자가 보고 다음에 뭘.

> 표기: `[plan]`·`[scaffold]`·`[wire]`·`[env]`·`[verify]`·`[done]` 등 대괄호 태그는 `install.py` 가 stdout 에 실제로 찍는 접두사다. `[error]`·`[warn]` 은 stderr.
