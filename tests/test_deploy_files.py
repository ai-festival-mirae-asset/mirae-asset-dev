# -*- coding: utf-8 -*-
"""⑧-5 배포 묶음(infra/deploy) 검사 — 서버(리눅스)에서 깨질 파일을 커밋 전에 잡는다.

무엇: ① 파일 존재 ② bash 스크립트·systemd 유닛에 CR(\\r) 없음(CRLF 면 서버에서 실행 실패)
      ③ 유닛이 실제 진입점(server/app.py)·포트 80·자동 재기동을 가리킴 ④ bash 문법 검사(bash 가 있을 때만).
"""
import io
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(ROOT, "infra", "deploy")
FILES = ("install.sh", "healthcheck.sh", "warmup.sh", "mirae-api.service", "README_DEPLOY.md")


def _read(name):
    with io.open(os.path.join(DEPLOY, name), "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def test_deploy_files_exist_and_are_lf():
    for name in FILES:
        assert os.path.exists(os.path.join(DEPLOY, name)), name
    for name in ("install.sh", "healthcheck.sh", "warmup.sh", "mirae-api.service"):
        assert "\r" not in _read(name), f"{name}: CRLF — 리눅스에서 실행이 깨진다(.gitattributes eol=lf 확인)"
    attrs = io.open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8").read()
    assert "*.sh" in attrs and "eol=lf" in attrs


def test_service_unit_points_at_server_entrypoint():
    unit = _read("mirae-api.service")
    assert "ExecStart=/opt/mirae-asset-dev/.venv/bin/python server/app.py" in unit
    assert "--port 80" in unit and "Restart=always" in unit and "WantedBy=multi-user.target" in unit
    assert "EnvironmentFile=-/etc/mirae-api.env" in unit           # 비밀값은 저장소 밖
    assert "TimeoutStartSec=300" in unit                            # 그래프 적재 시간 허용


def test_install_script_covers_data_build_and_health():
    sh = _read("install.sh")
    for needle in ("requirements.txt", "storage/load_duckdb.py", "kg/build_kg.py",
                   "ai-festival-mirae-asset.github.io",                # 옛 어휘 그래프 재생성 판정
                   "/etc/mirae-api.env", "systemctl restart mirae-api", "/health",
                   "mirae-healthcheck", "warmup.sh"):
        assert needle in sh, needle
    assert "CLOVASTUDIO_API_KEY=" in sh and "CLOVASTUDIO_API_KEY=\"" not in sh   # 키 값은 스크립트에 없다


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 없음")
def test_bash_syntax():
    """bash -n (문법만). Windows 의 WSL bash 는 상대 경로+cwd 로 넘겨야 파일을 찾는다."""
    for name in ("install.sh", "healthcheck.sh", "warmup.sh"):
        try:
            proc = subprocess.run(["bash", "-n", name], cwd=DEPLOY, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"bash 실행 불가: {exc}")
        if proc.returncode != 0 and "No such file" in (proc.stderr or ""):
            pytest.skip("이 환경의 bash 가 저장소 경로를 읽지 못함(WSL 미설정)")
        assert proc.returncode == 0, f"{name}: {proc.stderr}"
