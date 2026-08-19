# -*- coding: utf-8 -*-
"""config/env_loader.py (.env 읽기) 테스트.

무엇: ① .env 한 줄 형식 해석 ② 운영체제 환경변수 우선 규칙 ③ 파일 부재 시 무해함
      ④ 비밀값 가리기.
왜  : 팀원마다 설정 방법이 달라 생기던 "나는 왜 안 되지" 문제를 없애려고 .env 를
      도입했다. 특히 ②는 운영 서버에서 개발용 .env 가 실제 키를 덮어쓰는 사고를
      막는 안전장치라 회귀 방지가 필요하다.
"""
import io
import os

import pytest

from config.env_loader import load_env, mask, parse_env_text


# ---------------------------------------------------------------------------
# 1. 한 줄 형식 해석
# ---------------------------------------------------------------------------

def test_parse_basic_pair():
    assert parse_env_text("A=1\nB=2\n") == {"A": "1", "B": "2"}


def test_parse_skips_comment_and_blank():
    assert parse_env_text("# 주석\n\n  \nA=1\n") == {"A": "1"}


def test_parse_allows_export_prefix():
    """맥·리눅스 사용자가 export 를 붙여 넣어도 받아들인다."""
    assert parse_env_text("export A=1\n") == {"A": "1"}


@pytest.mark.parametrize("line,expected", [
    ('A="따옴표 안"', "따옴표 안"),
    ("A='작은 따옴표'", "작은 따옴표"),
    ("A=따옴표없음", "따옴표없음"),
])
def test_parse_strips_surrounding_quotes(line, expected):
    assert parse_env_text(line)["A"] == expected


def test_parse_keeps_value_with_equals_sign():
    """값 안의 = 는 살려 둔다(쿠키·토큰에 흔하다)."""
    assert parse_env_text("A=k=v=w")["A"] == "k=v=w"


def test_parse_ignores_line_without_equals():
    assert parse_env_text("이건그냥글자\nA=1\n") == {"A": "1"}


def test_parse_windows_path_backslash_kept():
    """윈도우 경로의 역슬래시를 이스케이프로 해석하지 않는다."""
    assert parse_env_text(r"MIRAE_DATASETS=C:\Users\a\datasets")["MIRAE_DATASETS"] == r"C:\Users\a\datasets"


# ---------------------------------------------------------------------------
# 2. 파일 읽기 + 우선순위
# ---------------------------------------------------------------------------

def _write_env(tmp_path, text, bom=False):
    p = tmp_path / ".env"
    encoding = "utf-8-sig" if bom else "utf-8"
    io.open(p, "w", encoding=encoding, newline="\n").write(text)
    return p


def test_load_sets_missing_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY", raising=False)
    _write_env(tmp_path, "TEST_ENV_KEY=from-file\n")
    load_env(path=tmp_path / ".env")
    assert os.environ["TEST_ENV_KEY"] == "from-file"


def test_os_environment_wins_over_file(tmp_path, monkeypatch):
    """핵심 안전장치 — 운영 서버의 실제 환경변수를 .env 가 덮어쓰지 않는다."""
    monkeypatch.setenv("TEST_ENV_KEY", "from-os")
    _write_env(tmp_path, "TEST_ENV_KEY=from-file\n")
    load_env(path=tmp_path / ".env")
    assert os.environ["TEST_ENV_KEY"] == "from-os"


def test_override_true_forces_file_value(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ENV_KEY", "from-os")
    _write_env(tmp_path, "TEST_ENV_KEY=from-file\n")
    load_env(path=tmp_path / ".env", override=True)
    assert os.environ["TEST_ENV_KEY"] == "from-file"


def test_missing_file_returns_none_and_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY", raising=False)
    assert load_env(path=tmp_path / "없는파일.env") is None
    assert "TEST_ENV_KEY" not in os.environ


def test_bom_prefixed_file_is_read(tmp_path, monkeypatch):
    """메모장이 붙이는 보이지 않는 머리글자(BOM) 때문에 첫 줄이 깨지지 않는다."""
    monkeypatch.delenv("TEST_ENV_KEY", raising=False)
    _write_env(tmp_path, "TEST_ENV_KEY=ok\n", bom=True)
    load_env(path=tmp_path / ".env")
    assert os.environ["TEST_ENV_KEY"] == "ok"


# ---------------------------------------------------------------------------
# 3. 비밀값 가리기
# ---------------------------------------------------------------------------

def test_mask_hides_middle_of_long_secret():
    out = mask("nv-abcdefghijklmnop")
    assert "abcdefghij" not in out          # 가운데가 노출되지 않는다
    assert out.startswith("nv-a") and out.endswith("(길이 19)")


def test_mask_short_value_fully_hidden():
    assert mask("1234") == "****"


def test_mask_empty_value():
    assert mask("") == "(없음)"
    assert mask(None) == "(없음)"
