# teammode 자율 빌드 로그

> dev-cycle (구현 → 적대적 검수 → 반영 루프). 그린필드 기준선 = 0 tests.
> 각 슬라이스: TDD(분석→RED→구현→GREEN) 구현 서브에이전트 → 별도 적대적 검수 서브에이전트 → "수정할 내역 없음"까지 루프.

## 환경 결정

- **언어/테스트**: Python 3.13 + pytest 9. `pyproject.toml`에 `[tool.pytest.ini_options]` (testpaths=tests). 근거: 스펙 §11.5 "Python-first", 훅이 이미 Python 의존이라 추가 의존성 0.
- **pytest 격리**: 시스템에 pytest 부재 → 레포 루트 `.venv/`에 설치(.gitignore 등재됨). 테스트 실행은 `.venv/bin/python -m pytest`.
- **실환경 오염 금지**: 모든 어댑터 테스트는 tmp_path 픽스처로 settings.json/config.toml을 가짜 경로에 둔다. `~/.claude/settings.json` 등 실파일 무접촉.

---

## 슬라이스 1 — 검수 도구 우선 (골든 시나리오 + 러너)

(진행 중)
