# -*- coding: utf-8 -*-
"""
.env 파일 읽기 — 팀원별 비밀값(API 키 등)을 각자 파일 하나로 관리한다.

무엇을: 저장소 최상위의 `.env` 파일을 읽어서 프로그램의 환경변수로 올린다.
왜   : 예전에는 팀원마다 운영체제에 환경변수를 직접 등록해야 했다(윈도우는 setx,
       맥은 .zshrc 수정). 방법이 서로 다르고, 등록해도 터미널을 껐다 켜야 하고,
       무엇이 등록됐는지 확인하기도 어려웠다. `.env` 파일 하나를 복사해 값만
       채우는 방식이 팀 전체에 훨씬 간단하다.

설치 필요 없음: 표준 라이브러리만 쓴다(python-dotenv 같은 외부 패키지 불필요).
       팀원이 `pip install`을 다시 하지 않아도 동작한다.

우선순위: 운영체제 환경변수 > .env 파일.
       이미 등록된 값이 있으면 .env 값은 무시한다. 이유는 두 가지다.
         1) 기존에 setx 로 등록해 둔 팀원의 환경이 깨지지 않는다.
         2) 서버(NCP) 배포에서는 실제 환경변수로 주입하는데, 서버에 남아 있던
            개발용 .env 가 운영 값을 덮어쓰는 사고를 막는다.

보안: `.env` 는 .gitignore 로 커밋에서 제외돼 있다. 값이 든 파일을 저장소에
       올리지 않는다. 커밋하는 것은 값이 비어 있는 견본 `.env.example` 뿐이다.

쓰는 법(코드에서):
    from config.env_loader import load_env
    load_env()
    key = os.environ.get("CLOVASTUDIO_API_KEY")

쓰는 법(확인용):
    python config/env_loader.py      # 어떤 값이 잡혔는지 가려서 보여준다
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent        # config/
ROOT = HERE.parent                            # 저장소 최상위
DEFAULT_ENV_PATH = ROOT / ".env"

# 이 파일에서 관리하는 설정값 목록 — 확인 출력과 견본 파일의 기준이 된다.
KNOWN_KEYS = (
    ("CLOVASTUDIO_API_KEY", True,  "CLOVA Studio API 키 (필수, 비밀값)"),
    ("KRX_COOKIE",          True,  "KRX 로그인 세션 쿠키 (구성종목 수집 때만, 비밀값)"),
    ("MIRAE_DATASETS",      False, "원본 엑셀 8개가 든 폴더 경로 (기본값: 저장소의 datasets/)"),
    ("RUN_LIVE_LLM",        False, "1 이면 실제 CLOVA 호출까지 포함해 테스트 (기본: 끔)"),
)

_loaded_from = None      # 한 번만 읽도록 기억해 둔다


def parse_env_text(text):
    """`.env` 내용(문자열) → {이름: 값} 사전. 형식 오류는 조용히 건너뛴다."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):          # 빈 줄·주석
            continue
        if line.startswith("export "):                # 맥/리눅스 습관 허용
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        # 따옴표로 감싼 값 허용: KEY="abc" / KEY='abc'
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name:
            result[name] = value
    return result


def load_env(path=None, override=False):
    """
    `.env` 를 읽어 os.environ 에 올린다. 파일이 없으면 아무 일도 하지 않는다.

    path     : 다른 위치의 파일을 쓰고 싶을 때만 지정. 기본은 저장소 최상위 `.env`.
    override : True 면 운영체제 환경변수까지 덮어쓴다(기본 False — 위 '우선순위' 참고).

    반환값: 실제로 읽은 파일 경로(Path). 파일이 없으면 None.
    """
    global _loaded_from
    env_path = Path(path) if path else DEFAULT_ENV_PATH

    if _loaded_from is not None and path is None and not override:
        return _loaded_from                      # 이미 읽었으면 다시 읽지 않는다

    if not env_path.is_file():
        return None

    # BOM(엑셀·메모장이 붙이는 보이지 않는 머리글자) 제거를 위해 utf-8-sig 로 읽는다
    text = env_path.read_text(encoding="utf-8-sig")

    for name, value in parse_env_text(text).items():
        if override or name not in os.environ:
            os.environ[name] = value

    if path is None:
        _loaded_from = env_path
    return env_path


def mask(value):
    """비밀값을 화면에 찍을 때 앞뒤만 남기고 가린다."""
    if not value:
        return "(없음)"
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} (길이 {len(value)})"


def _report():
    """python config/env_loader.py 로 실행했을 때의 확인 출력."""
    used = load_env()
    print(f".env 파일: {used if used else '(없음 — 운영체제 환경변수만 사용)'}")
    print("-" * 58)
    missing_required = []
    for name, secret, desc in KNOWN_KEYS:
        value = os.environ.get(name)
        shown = mask(value) if secret else (value or "(없음)")
        mark = "O" if value else "-"
        print(f" [{mark}] {name:<22} {shown}")
        print(f"     {desc}")
        if name == "CLOVASTUDIO_API_KEY" and not value:
            missing_required.append(name)
    print("-" * 58)
    if missing_required:
        print("CLOVASTUDIO_API_KEY 가 없습니다.")
        print("→ .env.example 을 복사해 .env 로 만들고 키 값을 채우세요.")
    else:
        print("CLOVA 호출 준비 완료.")


if __name__ == "__main__":
    _report()
