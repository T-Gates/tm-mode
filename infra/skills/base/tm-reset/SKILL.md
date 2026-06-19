---
name: tm-reset
description: Use to undo a teammode install on this host — reverting what install.py added (active marker, hooks, env line, Obsidian registration). Triggers on "팀모드 초기화", "팀모드 제거", "언인스톨", "teammode reset", "teammode uninstall", "테스트 정리", or when cleaning up a teammode test/scratch repo.
---

# tm-reset — 팀모드 초기화/언인스톨

install이 호스트에 더한 것을 **안전하게 되돌리는** 스킬. install.py(결정적 기계)를 호출하고 결과를 사람 말로 옮긴다 — 단계를 손으로 재현하지 않는다.

## 원칙
- **될 일은 `install.py --uninstall`(기계)가, 판단·확인은 이 스킬이.**
- **파괴적이므로 반드시 사람 확인 먼저.** 무엇이 지워지는지 말하고, "진행할까요?" 동의를 받은 뒤에만 실행한다.
- **호스트 안전**: `--yes`는 **실 `~/.claude/settings.json`의 훅을 제거(write)** 한다. 격리만 되돌리려면 `--settings <경로>`. 둘 다 없으면 uninstall은 실호스트를 건드리지 않고 거부한다.
- **팀 데이터는 안 지운다**: `memory/`(세션로그·INDEX·members)는 **절대** 삭제하지 않는다. uninstall은 호스트 흔적만 되돌린다.

## 0. 먼저 확인 (파괴적)
사람에게 무엇이 되돌려지는지 알린다:
- `.teammode-active` 마커 삭제 (팀모드 off)
- `settings.json`에서 teammode 훅 제거 (남의 훅은 보존)
- 셸 프로파일(`.bashrc` 등)에서 teammode가 주입한 줄만 제거 (남의 줄 보존)
- `obsidian.json`에서 이 팀 볼트 등록만 해제 (다른 볼트·미설치 무영향)

→ "팀모드 호스트 설치를 되돌립니다. memory/(팀 데이터)는 그대로 둡니다. 진행할까요?"

## 실행 (동의 후)
```bash
python infra/install.py --uninstall --root . --yes
```
- install.py가 함: off(마커 삭제 + sync off) → 어댑터 uninstall(훅 제거) → env 줄 제거 → obsidian 등록 해제. 전부 멱등·비치명(이미 없으면 무동작, 크래시 0).
- **격리 테스트**라면 `--yes` 대신 `--settings <격리경로>`(+ 필요시 `--profile`·`--obsidian-config`)로 실호스트를 안 건드리고 되돌린다.
- 출력의 "제거됨:" 목록을 사람 말로 옮긴다. 되돌릴 게 없으면 "이미 정리됨"으로 안내.

## 테스트용 스크래치 레포 통째 정리
uninstall은 호스트 흔적만 되돌린다 — 레포 폴더 자체는 남는다. 테스트용 스크래치 레포를 통째로 없애려면 **그 폴더를 직접 삭제**하라고 안내한다(예: `rm -rf <scratch-repo>`). uninstall이 폴더를 지우지 않는다.

## 안 하는 것 / 경계
- `memory/`(팀 데이터) 삭제 안 함.
- 푸시·원격 무관 — 로컬 호스트 되돌리기만. 커밋/PR은 사람 결정.
- 코드 작성·다른 스킬 자동 호출 안 함 — 초기화만.

## Common Mistakes
| 실수 | 올바른 방법 |
|------|------------|
| 확인 없이 바로 실행 | 파괴적이므로 무엇이 지워지는지 말하고 동의부터 |
| `memory/`까지 지운다고 안내 | uninstall은 호스트 흔적만 — memory/는 보존 |
| 스크래치 레포가 uninstall로 사라진다고 봄 | 폴더는 남는다. 통째 정리는 폴더 직접 삭제 안내 |
| `--yes`를 단순 "동의"로만 안내 | `--yes`=실 `~/.claude/settings.json` write. 격리는 `--settings` |
| install.py 역할을 스킬이 손으로 재현 | install.py 호출하고 결과만 옮긴다 |

---
> 동작이 예상과 다르면 `infra/install.py`(`--uninstall`)·`infra/install_lib.py`(env/obsidian 역함수)를 확인.
