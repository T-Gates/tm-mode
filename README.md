# teammode

> Turn your team mode on. — AI 코딩 에이전트(Claude Code · Codex)를 위한 **크로스에이전트 팀 협업 툴킷.**

팀의 작업 맥락(세션로그·결정·상태)을 git 레포 하나에 마크다운으로 모으고, 에이전트가 **세션 시작 시 자동으로 그 맥락을 읽어** 들어온다. "소통하지 않아도 팀이 뭘 하는지 안다."

> 상태: **WIP.** L1(팀 메모리·맥락 자동주입·Obsidian 뷰)이 동작·검증 완료. L2(서비스 연동)·호스팅은 진행 예정.

---

## 한 줄 셋업

에이전트(Claude Code/Codex)에게:

```
이 레포 셋업해줘
```

→ 에이전트가 `AGENTS.md`를 읽고 `install.py`를 돌려 팀모드를 켠다. (수동: 아래 ‘직접 셋업’.)

## 무엇이 되나 (L1)

| 기능 | 설명 |
|---|---|
| **팀 메모리** | `memory/`에 세션로그·결정·INDEX를 마크다운으로. git으로 공유. |
| **맥락 자동주입** | 세션 시작 시 훅(`session-start.py`)이 팀원별 최근 세션로그를 에이전트에 주입. |
| **세션로그 기계 기록** | `teammode.py log`가 날짜·frontmatter·06시컷을 자동 처리(에이전트가 파일명 틀릴 일 0). |
| **Obsidian 뷰** *(opt-in, 키 0)* | `memory/`를 Obsidian 볼트로 열면 팀 메모리가 그래프로. 자동 등록도 지원. |

## 팀 생애주기

```
팀 셋업 (도입자 1회)  →  개인 셋업 (각 멤버)  →  서비스 연결 (L2, 예정)
```

## 직접 셋업

```bash
# 도입자(팀 새로 시작) — 이 레포를 템플릿으로 만든 뒤:
python infra/install.py --root . --yes

# 팀원(합류) — 팀 레포 clone 후:
python infra/install.py --root . --member-name <영문이름> --yes

# (선택) Obsidian 볼트 자동 등록 — 온보딩 때 안 했어도 언제든 나중에 실행 가능:
python infra/install.py --root . --register-obsidian
```

`--yes` 없이는 실 에이전트 설정에 쓰지 않는다(안전). 격리 실행은 `--settings <경로>`, 계획만 보려면 `--dry-run`.

## 엔진 동사 (teammode.py)

| 동사 | 역할 |
|---|---|
| `on` / `off` | 팀모드 켜기·끄기 (배너·훅·active 마커) |
| `log` | 세션로그 기록 (날짜·frontmatter 자동) |
| `context` | 전원 최근 세션로그·상태를 JSON으로 수집 (요약은 스킬 몫) |
| `pull` / `commit` / `update` | git 동기화·커밋·upstream 갱신 |

모든 동사는 팀 루트를 `--root`로 명시받는다(환경변수 무신뢰 — 안전).

## 구조

```
infra/
├── teammode.py        # 엔진 (동사)
├── install.py         # 부트스트랩 (셋업)
├── install_lib.py     # 부트스트랩 순수 코어
├── git_ops.py         # git 공통
├── agents/<name>/     # 에이전트별 어댑터 (claude·codex)
├── hooks/             # 공통 훅 (session-start·session-log-remind·auto_pull)
└── skills/            # 스킬 (tm-onboard …)
memory/                # 팀 메모리 (셋업 시 생성)
conformance/           # 호환 검사 + 골든 시나리오
```

스펙: 설계 폴더 `spec/` — 01 팀메모리 · 02 훅·어댑터 · 03 호환 · 04 install · 05 onboard.

## 라이선스

[LICENSE](LICENSE) 참조.
