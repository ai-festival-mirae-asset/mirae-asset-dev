# -*- coding: utf-8 -*-
"""
미확정 정책 룩업 — config/policy.json 로드 (S2 순서 ③, 8/13).

무엇: '위험등급 낮음'·'AA 이상'·커버리지 임계·매수가능 판정 같은 열린 해석을
      코드가 아니라 설정 파일에서 읽는다.
왜  : 주최 Q&A 로 해석이 확정되면 파일 교체만으로 반영(무배포) — 9/6 이후
      결과물 변경 금지 규정과 충돌하지 않는 유일한 경로다(S2_PLAN §2).
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
POLICY_PATH_DEFAULT = os.path.join(ROOT, "config", "policy.json")

# 파일이 없거나 키가 빠져도 동작하는 기본값 — config/policy.json 과 동기 유지
DEFAULTS = {
    "rating_at_or_above_includes_minus": False,  # 'AA 이상' = 문자 그대로 rank<=3 (8/14 확정 — AA- 는 'AA급/등급대'일 때만)
    "low_risk_grades": [5, 6],                   # '낮은 위험' 해석
    "high_risk_grades": [1, 2],
    "coverage_partial_threshold_pct": 30.0,
    "buyable_rule": "drv_is_buyable",
    "recent_window_months": 6,
    "default_limit": 10,
    "trap_similar_suggest_limit": 3,
}


def load_policy(path=POLICY_PATH_DEFAULT):
    """기본값 위에 설정 파일을 덮어쓴 dict. '_' 로 시작하는 설명 키는 버린다."""
    policy = dict(DEFAULTS)
    if path and os.path.exists(path):
        with io.open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        policy.update({k: v for k, v in loaded.items() if not k.startswith("_")})
    return policy
