# 무엇 / 왜

<!-- 이 PR이 무엇을 하고, 왜 필요한지 한두 문장으로. 관련 이슈가 있으면 링크 (예: Fixes #123) -->

## 변경사항

<!-- 주요 변경을 항목별로. 파일/모듈 단위로 짧게 -->

-

## 테스트

<!-- 어떻게 검증했는지. `python -m pytest -q`(인스턴스 검증)와
`python -m pytest -q maintainer_tests`(업스트림 메인테이너 계약) 실행 결과를 모두 붙여주세요.
CI는 Python 3.9와 3.12에서 돕니다. 문법/annotation/test helper/CI/git fixture를 건드렸다면
Python 3.9 호환성, 환경 격리(git config, NO_COLOR/TERM, HOME/XDG), fake remote의 main branch
고정을 같이 확인하세요. -->

```
$ python -m pytest -q

$ python -m pytest -q maintainer_tests

```

## 체크리스트

- [ ] stdlib-only 유지 (Python 3.9+ 표준 라이브러리 외 의존성 없음)
- [ ] 두 테스트 스위트 모두 통과 (`python -m pytest -q`, `python -m pytest -q maintainer_tests`)
- [ ] Python 3.9 호환성 확인 (문법/annotation/stdlib API/test helper 변경 시 필수)
- [ ] 테스트 fixture가 로컬 전역 설정에 의존하지 않음 (`git config`, `NO_COLOR`/`TERM`, `HOME`/XDG, 기본 branch 이름 등)
- [ ] 공개 위생 가드 통과 또는 영향 없음 (`tests/test_no_identity_leaks.py`)
- [ ] `origin/main` 최신 상태에서 merge 가능, 충돌 해결 후 두 테스트 스위트 재확인
