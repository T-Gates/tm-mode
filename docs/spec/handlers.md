# handlers/ 규약 (폐기됨)

> **이 문서는 폐기됐다.** L2 재설계(2026-06-25, A안 확정)에서 `role_server` 프록시와
> 수제 `handlers/<role>.py` 핸들러 추상화를 버리고 **MCP 등록기**로 전환했다.
> 진실 소스: `docs/archive/2026-06-25-L2-redesign.md`.

## 무엇이 바뀌었나

L2는 더 이상 역할(issues/chat/docs/calendar)을 도구 중립 함수 계약으로 추상화하지 않는다.

- **폐기**: `handlers/<role>.py` 파일·`issues_create()` 같은 필수 함수 시그니처 계약·`handlers_are_valid()` 검증·`infra/mcp/role_server.py` 프록시·"재사용>흡수>수제" 우선순위 판정.
- **대체**: tm-mode는 팀이 고른 **공식 벤더 MCP를 역할 슬롯에 *연결(등록)*만** 한다. 이슈 생성·일정 추가 같은 **동작은 AI가 `mcp__<alias>__<벤더도구>`를 직접 호출**한다. tm-mode는 동작을 한 겹 래핑하지 않는다.

## 어디를 봐야 하나

| 옛 내용 | 현행 위치 |
|---|---|
| 슬롯에 provider/MCP 연결하는 흐름 | `docs/spec/skills.md §5.4` (tm-connect — 등록기 흐름) |
| MCP alias 등록 (install-mcp) | `docs/spec/internals.md §2.8` |
| 역할 슬롯 선언·provider 팩 스키마 | `docs/spec/internals.md §7` |
| 토큰 금고 | `docs/spec/internals.md §7.6`, `infra/credentials.py` |

> 동작을 감싸는 `tm-issues create` 같은 동작 CLI/명령은 **만들지 않는다** — 그것은 여기서
> 폐기한 추상화의 부활(B안)이다. 동작은 AI가 벤더 MCP 도구를 직접 부른다.
