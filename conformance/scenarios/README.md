# 골든 시나리오 (Golden Scenarios)

teammode의 **실행 가능한 명세**. 스펙 02 §11.12·§11.11 + 스펙 03 §3에 따른
5개 선언적 시나리오다. 한 정의를 두 모드가 공유한다:

- `verify` — 이 시나리오를 **우리 툴킷**에 실행 = 독푸딩 검수 (스펙 02 §11.12)
- `conform` — 같은 시나리오를 **임의 구현**에 실행 = conformance kit (스펙 03 §3)

## 시나리오 5종

| 파일 | 의미 | 대응 스펙 |
|---|---|---|
| `01-on-banner.json` | `on` → 팀 배너 출력 | 02 §11.5 (배너), 01 §2.2 |
| `02-context-injection.json` | 세션 시작 시 컨텍스트(메모리) 주입 | 01 §4 (주입 스케일) |
| `03-issue-create.json` | 이슈 생성(서비스 슬롯 동작) | 02 §9 (서비스 추상화) |
| `04-log-accumulate.json` | 세션로그 누적 (하루 1파일, append) | 01 §3 |
| `05-off-persist.json` | `off` → 상태 저장·훅 비활성 | 02 §5 (sync on/off) |

## 스키마 (v0.1)

각 시나리오 파일은 다음 형태의 JSON이다:

```jsonc
{
  "id": "01-on-banner",
  "title": "사람이 읽는 한 줄",
  "spec_refs": ["02 §11.5"],            // 추적용 스펙 참조
  "tier_signal": "deterministic",       // "deterministic" | "advisory" — §11.11 Tier 산출용
  "steps": [
    {
      "name": "단계 한 줄 설명",
      "action": {                       // 엔진에 시키는 1개 동작
        "kind": "command",              //   "command"=엔진 CLI | "fs_write"=파일 작성 | "fs_delete"=파일 삭제(teardown)
        "argv": ["on"],                 //   kind=command일 때 엔진에 넘길 인자
        "path": "...", "content": "..." //   kind=fs_write 는 path+content / fs_delete 는 path (root 하위만, 정리용)
      },
      "expect": [                       // 이 단계가 만족해야 하는 단언 배열 (AND)
        { "kind": "stdout_contains", "value": "배너 텍스트" },
        { "kind": "file_exists", "path": "memory/banner.txt" },
        { "kind": "file_contains", "path": "...", "value": "..." },
        { "kind": "exit_code", "value": 0 }
      ]
    }
  ]
}
```

엔진(구현)은 `argv`를 받아 동작하고 `{exit_code, stdout, stderr}`를 돌려주는
하니스 인터페이스만 만족하면 된다 — 스펙 03 §2 "C2 주의"(파일 배치·언어 비강제)와 정합.
빈 엔진(no-op)에 돌리면 모든 `expect`가 실패(RED) = 인수 테스트로 박힌다.
