# teammode 스펙 03 — 호환 선언 절차 (Conformance)

| | |
|---|---|
| spec_version | **0.1** |
| 상태 | 정식 초판 (2026-06-12) |
| 관련 스펙 | [01 팀 메모리 표준](01-team-memory.md), [02 훅·어댑터 표준](02-hook-manifest.md) |

---

## 1. 목적

teammode는 reference 구현(Claude Code 기반)과 별개로, 누구든 같은 표준을 따르는 **독립 구현**을 만들 수 있도록 설계됐다(예: 타 에이전트 네이티브 구현, 다른 언어의 엔진). 이 문서는 독립 구현이 **"teammode 호환(teammode compatible)"** 을 선언하기 위한 조건과 절차를 정의한다.

호환의 약속은 하나다: **호환 구현끼리는 같은 팀 레포를 공유할 수 있다.** 한 팀 안에서 멤버 A가 reference 구현을, 멤버 B가 독립 구현을 써도 팀 메모리가 깨지지 않는다.

### 1.1 용어

| 용어 | 정의 |
|---|---|
| **reference 구현** | teammode 본진 레포의 구현. Tier 1 = Claude Code 기준 |
| **독립 구현** | 본진 코드와 별개로 작성된, 본 스펙 준수를 목표로 하는 구현 |
| **conformance kit** | 호환을 기계 검사하는 픽스처 + 체크 스크립트 묶음 (§3) |
| **Implementations 목록** | 본진 README의 호환 구현 등재 표 (§4) |

## 2. 호환 조건

다음 세 가지를 모두 만족해야 "teammode 호환"을 선언할 수 있다 (필수).

### C1. 팀 메모리 표준 준수 — [스펙 01](01-team-memory.md)

- 세션로그 포맷: 파일 위치·하루 1파일·06시 컷·frontmatter(author/date/summary) (01 §3)
- memory/ 코어 디렉토리 구조와 INDEX.md 갱신 규칙 (01 §2)
- 주입 스케일 규칙: ~4인 전문 / 5인+ summary (01 §4) — v0.1에서는 kit 자동 검사가 없으므로 **자가 점검표 항목**이며, 골든 시나리오의 "컨텍스트 조회" 단계에서 주입 방식을 확인한다
- 검증 방법: 구현이 **생산하는** 파일이 포맷을 준수하는가 + 구현이 표준 팀 레포를 **읽고** 정상 동작하는가, 양방향 모두.

### C2. 훅·어댑터 표준의 의미 보존 — [스펙 02](02-hook-manifest.md)

- 정규 이벤트 4종의 의미 보존 — 특히 `PreToolUse`의 차단 시맨틱 (02 §3.1)
- 정규 입력 스키마(canonical input)로 공통 스크립트 호출 (02 §6)
- 폴백 정책 — **무음 스킵 금지** (02 §7)
- manifest·스킬의 정규형 규약: 에이전트 고유 표기 금지, MCP 시맨틱 참조 (02 §3, §8)
- 주의: 독립 구현이 `agents/` 디렉토리 구조나 Python을 그대로 쓸 필요는 없다. 보존해야 하는 것은 **선언 포맷(manifest.json, events.json)과 의미**이지 구현 언어·파일 배치가 아니다.

### C3. conformance kit 통과 (§3)

kit의 모든 필수 검사를 통과하고, 결과 로그를 등재 신청에 첨부한다.

### 2.1 범위 한정

- 호환 선언은 **특정 spec_version에 대한** 선언이다 ("teammode 호환 (spec 0.1)"). 버전 없는 호환 선언은 무효다.
- 부분 구현(예: 메모리만 구현, 훅 없음)은 호환 선언 불가. 단 Implementations 목록에 "partial" 표기로 등재는 가능하다 (§4). partial 등재의 검사 범위: 신청 시 구현 범위를 명시하고, 그 범위에 대응하는 검사 부분집합(§3 표의 "대응 스펙" 열 기준 — 예: memory-only는 K1~K2·K8 + 골든 시나리오 중 컨텍스트 조회·세션로그 작성)을 통과해야 한다. 부분집합의 적정성은 maintainer가 리뷰에서 승인한다.

## 3. conformance kit (구상)

> 상태: v0.1 시점에서 kit은 **구상 단계**다. reference 구현의 lint 스킬(acme-lint)이 수행하는 구조 검사를 일반화·독립 실행화하는 방향으로 만든다. kit이 공개되기 전까지 호환 선언은 §4의 수동 리뷰로 갈음한다.

구성:

```
conformance/
├── fixtures/
│   ├── memory/                  # 표준 팀 레포 픽스처 (정상 세션로그·INDEX·members)
│   ├── memory-invalid/          # 의도적 위반 픽스처 (kit 스크립트 셀프테스트용)
│   ├── manifest/                # 정규형 manifest 예시 + 위반 예시
│   └── events/                  # 에이전트 원어 훅 입력 JSON ↔ 기대 정규 출력 (golden)
├── checks/
│   ├── check-memory.py          # C1: 세션로그·디렉토리 구조 검사 (lint 일반화)
│   ├── check-manifest.py        # C2: manifest 정규형·events.json 완전성 검사
│   └── check-normalize.py       # C2: 원어→정규 변환 golden test
└── run.py                       # 전체 실행 + 결과 리포트
```

검사 항목 (필수 통과):

| # | 검사 | 대응 스펙 |
|---|---|---|
| K1 | 구현이 생산한 세션로그가 포맷 준수 (필수 3필드 존재 — 추가 필드는 허용, 파일명=date, 하루 1파일) | 01 §3 |
| K2 | 06시 컷 경계값 (05:59 → 전날 / 06:00 → 당일) | 01 §3.2 |
| K3 | events.json 완전성 (모든 정규 이벤트·행위 키 존재) | 02 §4 |
| K4 | manifest 정규형 (에이전트 고유 표기 grep — `mcp__`, 매처 문자열 등) | 02 §3 |
| K5 | normalize golden test (원어 픽스처 → 정규 스키마 일치) | 02 §6 |
| K6 | 폴백 동작 (미지원 이벤트 선언 시 `[warn]` 출력, 무음 스킵 부재) | 02 §7 |
| K7 | 스킬 본문 정규형 (`mcp__` 직표기·제품명 직표기 부재) | 02 §8, §9.3 |
| K8 | 코어 디렉토리 구조 존재 + 신규 폴더의 INDEX.md 등재 | 01 §2 |

- `fixtures/memory-invalid/`(위반 픽스처: summary 누락, 분할 파일 등)는 **kit 검사 스크립트 자체의 셀프테스트**용이다 — 피검 구현에 위반 탐지(lint 동등) 기능을 요구하는 항목이 아니다.
- 추가로 **골든 시나리오 5종**(켜기 → 컨텍스트 조회 → 이슈 생성 → 세션로그 작성 → 끄기)을 실환경에서 통과해야 한다. v0.1에서는 수동 체크리스트로 운영하고, 자동화는 kit 후속 버전 과제다.
- kit 후속 과제 (명문화): ① 피검 구현 호출 하니스 인터페이스 — K1의 "생산된 로그"를 트리거·수집하는 방법, K5에서 Python `normalize.py`가 아닌 동등 계층을 호출하는 방법 (§2 C2 주의에 따라 파일 배치 강제 불가) ② `fixtures/memory/`(표준 레포 픽스처)를 소비하는 읽기 방향 검사 추가 ③ 주입 스케일 규칙(01 §4)의 자동 검사.

## 4. Implementations 등재 절차

1. **신청** — 본진 레포에 이슈 생성 (템플릿: `implementation` 라벨). 포함 사항:
   - 구현 이름·레포 링크·라이선스
   - 대상 에이전트/플랫폼, 따르는 spec_version
   - conformance kit 결과 로그 (kit 공개 전: C1·C2 자가 점검표 + 골든 시나리오 5종 수행 기록)
2. **리뷰** — maintainer가 검증한다. kit 결과 확인 + 샘플 팀 레포 상호운용 스팟 체크(독립 구현이 만든 세션로그를 reference가 읽기, 그 역방향). 사이드 프로젝트 운영 케이던스상 응답까지 수 주가 걸릴 수 있다.
3. **등재** — 통과 시 README Implementations 표에 추가:

   | 구현 | 에이전트/플랫폼 | spec_version | 상태 | 검증일 |
   |---|---|---|---|---|
   | (예) hermes-teammode | Hermes | 0.1 | compatible | 2026-07-01 |
   | (예) … | … | 0.1 | partial (memory only) | … |

4. **유지·상태 전이** — 스펙 minor bump 시 등재 구현에 이슈로 통지한다.
   - `compatible → stale`: 통지된 minor에 대해 **그 다음 minor가 발행되는 시점까지** 재검증이 제출되지 않으면 stale. (minor는 비정기 발행이므로, 통지 시 maintainer가 절대 기한을 병기할 수 있고 병기 시 그 기한이 우선한다.) **partial 등재에도 동일 규칙을 적용**한다 (미재검증 시 stale).
   - `stale → compatible`, `partial → compatible`: **기존 등재 이슈에** 제출 시점의 **현행 spec_version** 기준 kit 결과 로그(또는 자가 점검표)를 코멘트로 제출 → maintainer 확인 후 표 갱신. 신규 신청 불요.
   - 등재 철회는 언제든 본인 신청으로 가능하다.

## 5. 배지

등재된 구현은 README에 배지를 달 수 있다:

```markdown
![teammode compatible](https://img.shields.io/badge/teammode-compatible%20(spec%200.1)-blue)
```

- 배지에는 spec_version을 **반드시 포함**한다 (§2.1). 상태 배지도 동일하다.
- `partial`·`stale` 상태에서는 compatible 배지 사용 불가. 상태 표기 배지는 가능:

  ```markdown
  ![teammode partial](https://img.shields.io/badge/teammode-partial%20(spec%200.1)-yellow)
  ![teammode stale](https://img.shields.io/badge/teammode-stale%20(spec%200.1)-lightgrey)
  ```

- 배지는 명예 기반 운영이다 — 허위 사용이 확인되면 Implementations에서 제거하고 공지한다.

## 6. 버저닝 연동

- 본 문서 자체도 spec_version 0.1의 일부다. kit 검사 항목(K1~K8)의 추가·변경은 minor bump를 따른다.
- 0.x 기간의 호환 선언은 "해당 minor에 대한 선언"이며, 1.0 동결([01 §5](01-team-memory.md)) 후에는 1.x 전체에 대한 선언으로 완화한다.
- 독립 구현이 2개 이상 등재되는 시점이 1.0 동결 + RFC-lite 절차 도입의 트리거다.

---

## 부록 A. 설계 근거

- **kit = lint의 일반화**: reference 구현은 이미 구조 검사 스킬(acme-lint)로 자기 레포를 검사한다. 같은 검사를 픽스처 기반·구현 중립으로 추출하면 conformance kit이 된다 — 새 발명이 아니라 기존 자산의 추출.
- **상호운용이 정의의 중심**: "스펙 문장을 다 지켰는가"보다 "같은 팀 레포를 깨지 않고 공유하는가"가 호환의 실질이다. 그래서 리뷰(§4-2)에 양방향 스팟 체크를 둔다.
- **명예 기반 배지**: 0.x 규모에서 법적·기술적 강제는 과잉이다. 등재·제거 권한을 maintainer가 갖는 것으로 충분하다.
