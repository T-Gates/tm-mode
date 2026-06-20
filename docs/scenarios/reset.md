# 초기화 시나리오

호스트 되돌리기(uninstall)를 수행하는 부록이다.

---

## 부록: 초기화 (install.py --uninstall 직접)

테스트 스크래치 레포 정리나 호스트 되돌리기는 **`python infra/install.py --uninstall --root . --yes`** 직접 실행으로 수행한다. **파괴적이라 사람 확인 먼저.** off(`.teammode-active` 마커 삭제 + sync off) → 어댑터 uninstall(settings.json 훅 제거, 남의 훅 보존) → env 줄 제거(우리 마커 줄만) → obsidian 등록 해제(해당 볼트만). 전부 멱등·비치명. **`memory/`(팀 데이터)는 절대 안 지운다.** `--root` 없으면 exit **2**, `--yes`/`--settings` 둘 다 없으면 실호스트 거부 exit **2**. 스크래치 레포 폴더 자체는 남으므로 통째 정리는 사람이 `rm -rf <repo>`.
