"""팀 시작 멘트(greeting)·끝맺음 말(farewell) — SPEC §3.1·§4.4·부록 A.3.

엔진 on: 배너 직후 team.config.json 의 team.greeting 있으면 출력(없으면 미출력).
엔진 off: team.farewell 있으면 그걸, 없으면 "상태 저장됨" 폴백.
config 읽기는 비치명 — 부재·깨짐이면 조용히 무시(on/off 막지 않음).

P1: --root 명시 + --settings 격리(실 ~/.claude 무접촉, conftest 가드).
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run(root: Path, verb: str, *argv):
    cmd = [sys.executable, str(ENGINE), verb, "--root", str(root),
           "--settings", str(root / ".teammode-settings.json"), *argv]
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_config(root: Path, **team_extra):
    """유효 team.config.json 작성(team.name 비-placeholder). team_extra 로 team.* 추가."""
    team = {"name": "tgates", "timezone": "Asia/Seoul", "locale": "ko_KR"}
    team.update(team_extra)
    cfg = {"spec_version": "0.1", "team": team, "services": {}}
    (root / "team.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


# ── on: greeting ──

def test_on_prints_greeting_when_configured(tmp_path):
    _write_config(tmp_path, greeting="우리 팀 화이팅!")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    assert "우리 팀 화이팅!" in r.stdout


def test_on_no_greeting_when_absent(tmp_path):
    _write_config(tmp_path)  # greeting 키 없음
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    # 배너만 출력 — greeting 으로 새 줄 추가 안 됨(현행 유지)
    assert "team mode ON" in r.stdout


def test_on_no_config_is_nonfatal(tmp_path):
    # config 파일 자체가 없어도 on 성공(greeting 미출력, 비치명)
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    assert "team mode ON" in r.stdout


def test_on_broken_config_is_nonfatal(tmp_path):
    (tmp_path / "team.config.json").write_text("{ this is not json",
                                               encoding="utf-8")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    assert "team mode ON" in r.stdout


def test_on_greeting_after_banner(tmp_path):
    # 배너 → greeting 순서(배너 다음에 멘트가 와야 함)
    _write_config(tmp_path, greeting="GREETING_TOKEN")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    assert r.stdout.index("team mode ON") < r.stdout.index("GREETING_TOKEN")


# ── off: farewell ──

def test_off_prints_farewell_when_configured(tmp_path):
    _write_config(tmp_path, farewell="수고하셨습니다 — tgates")
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    assert "수고하셨습니다 — tgates" in r.stdout


def test_off_fallback_when_no_farewell(tmp_path):
    _write_config(tmp_path)  # farewell 키 없음
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    assert "상태 저장됨" in r.stdout


def test_off_fallback_when_no_config(tmp_path):
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    assert "상태 저장됨" in r.stdout


def test_off_broken_config_falls_back(tmp_path):
    (tmp_path / "team.config.json").write_text("not json {{", encoding="utf-8")
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    assert "상태 저장됨" in r.stdout
