---
name: tm-onboard
description: Use at first contact with teammode — setting up a team repo or joining a team. Triggers on "이 레포 셋업해줘", "팀모드 셋업", "팀모드 시작", "온보딩", "팀모드 합류", "teammode setup", or when handed a teammode repo to set up.
---

# tm-onboard — 팀모드 온보딩

teammode를 처음 켜는 스킬. 팀 생애주기를 따라 자란다:

```
팀 셋업 (도입자 1회)  →  개인 셋업 (각 멤버)  →  서비스 연결 (L2, 나중)
        └─ L1: 세션로그 + 맥락 자동주입 ─┘        └─ Linear·Slack·… ┘
```

## 원칙
- **될 일은 `install.py`(결정적 기계)가, 판단·대화는 이 스킬이.** install.py를 호출하고 결과를 사람 말로 옮긴다 — 단계를 손으로 재현하지 않는다.
- **progressive**: L1 가치(맥락 주입)를 *먼저* 보여주고, 서비스 연결은 사람이 원할 때만.
- **호스트 안전**: `--yes`는 **실 `~/.claude/settings.json`에 훅을 배선(write)** 한다. 격리만 원하면 `--settings <경로>`, 변경 없이 보려면 `--dry-run`. `--yes`/`--settings` 둘 다 없으면 install은 wire를 건너뛰고 끝난다(실호스트 무접촉).

## 0. 도입자/팀원은 install.py가 자동 판정
**경로는 `--member-name`이 아니라 `team.config.json` 유효성으로 install.py가 자동으로 가른다**:
- config 없음/미초기화 → **팀 셋업**(도입자) — config를 새로 쓴다.
- config 유효 → **개인 셋업**(팀원) — config는 읽기만.

`--member-name`은 *분기 스위치가 아니라* 양쪽에서 author 이름을 정하는 인자다. 그래서 셋업 명령은 사실상 하나다 (아래). 미리 사람에게 "도입자/팀원 자동 판정됨"을 알리고 진행한다.
> (install.py는 아직 role을 `--json`으로 안 뱉으므로, 굳이 미리 알아야 하면 `team.config.json` **유효성**(team.name이 placeholder 아니고 spec_version 존재 = config_is_valid)을 직접 확인한다. `--json` 생기면 그걸로 전환.)

## 셋업 (도입자·팀원 공통 명령, role 자동)
```bash
python infra/install.py --root . --member-name <영문이름> --yes
```
- install.py가 함: preflight → 감지 → role 자동 → scaffold(`memory/INDEX.md`·`memory/team/members.md`·`memory/team/sessions/<이름>/`·도입자면 빈 services config) → 훅 sync(**실 settings.json에 write**) → env 주입 → verify(`on` → active 마커).
- `--member-name`: 권장. 생략 시 git user.name 제안. **팀원은 이름 충돌 회피 위해 명시 권장.**
- **이름 충돌**(다른 사람이 같은 이름 등재) → install.py가 **exit 3 + 안내**. 사람이 `--member-name <다른 영문이름>`으로 재실행하게 한다 (추측 정정 금지).
- 실패(exit≠0)면 사유를 사람 말로 옮기고 멈춘다.

## 첫 가치 (셋업 직후) — L1 보여주기
```bash
python infra/teammode.py context --root . --json
```
→ 결과를 사람 말로 요약: "지금 팀 상황: …"
- **단, state=on으로 보이려면 위 셋업이 `--yes`(또는 `--settings`)로 wire+verify까지 완주했어야 한다.** wire를 건너뛴 경우 state=off로 나온다.
- **갓 만든 팀은 세션로그 0** → 요약할 게 없다. "구조는 섰고, 다음 작업부터 자동 기록·주입됩니다"로 내레이션.
- 팀원은 context로 팀 기존 로그가 보인다. (다음 세션부터는 `session-start.py` 훅이 자동 주입.)

## Obsidian 뷰 (opt-in, 키 0)
memory/가 마크다운이라 Obsidian으로 그래프처럼 볼 수 있다. **물어보고** 진행:
- **예** → `python infra/install.py --root . --register-obsidian`. 이 명령이 `.obsidian/`(dataview·graph) 생성 + `obsidian.json`에 **merge 등록**(기존 볼트 보존·멱등)을 한 번에 한다. **Obsidian 미설치면 둘 다 우아하게 skip**(아무것도 안 만듦) — 안 쓰는 사람 0 영향.
- **아니오/미설치** → 수동: "`<repo>/memory`를 Obsidian 'Open folder as vault'로 여세요" (`obsidian://open?path=<memory 절대경로>`).
- 키·토큰 0(로컬 파일). 단 `obsidian.json`은 실 호스트 설정이라 **동의(opt-in) 후에만.**

## 서비스 연결 (L2) — 🔜 나중, 아직 준비 중
서비스(Linear·Slack·Notion·Google Cal)가 붙는 단계. (미래) `providers/<name>.json` 팩을 읽어 서비스별로 정확한 링크·버튼을 안내하고, 토큰 후 리소스 ID를 자동 조회해 1클릭 선택하게 한다.
- 팀 스코프(Slack·Notion): 도입자 1회 → config 커밋 → 팀원은 읽기만.
- 개인 스코프(Linear·Google Cal): 멤버 각자 1회 (Linear=개인 API키 안내, Google=localhost OAuth "허용").
- **지금은 미구현** — 사용자가 요청하면 "서비스 연결은 아직 준비 중(L2)"이라 안내. (`install-mcp`·credentials 금고 생기면 채움.)

## 안 하는 것 / 경계
- 코드 작성·이슈 생성·다른 스킬 자동 호출 안 함 — 온보딩만.
- 자가진단·검증은 `doctor`(별도, 나중) 몫.
- 푸시·PR은 사람 결정.

## Common Mistakes
| 실수 | 올바른 방법 |
|------|------------|
| `--member-name`으로 도입자/팀원을 가른다고 봄 | role은 install.py가 config 유효성으로 자동 판정. member-name은 이름일 뿐 |
| `--yes`를 단순 "동의"로만 안내 | `--yes`=실 `~/.claude/settings.json`에 write. 격리는 `--settings` |
| install.py 역할을 스킬이 손으로 재현 | install.py 호출하고 결과만 옮긴다 |
| 이름 충돌(exit 3)을 임의 해소 | 사람이 `--member-name <다른이름>` 재실행 |
| 서비스 연결을 지금 시도 | L2 미구현 — "준비 중" 안내 |

---
> 동작이 예상과 다르면 `spec/04-install.md`(install.py)·`spec/05-onboard-skill.md`(이 스킬 설계)를 확인.
