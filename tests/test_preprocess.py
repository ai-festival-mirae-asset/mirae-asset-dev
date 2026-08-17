# -*- coding: utf-8 -*-
"""preprocessing/preprocess.py 순수 판정 함수 단위 테스트.

무엇: 전처리 파이프라인의 판정 로직(등급 정규화·날짜 토큰·만기 상태·손상 행
      탐지·상품유형·상장 상태·신선도 지연일)을 데이터 파일 없이 검증한다.
왜: 파이프라인 내장 assertion(행수·키 유일성)은 "전체가 깨졌는지"만 알려준다.
    8/5 교차검증에서 나온 버그(R8: MAT_DT=0 316건이 만기도래로 오포함)는
    판정 함수 경계 케이스의 문제였으므로, 경계값을 함수 단위로 고정해
    회귀를 막는다. preprocess.py 는 import 시점 부작용이 없도록 리팩터링되어
    있어(검사·폴더 생성은 main() 전용) 원본 xlsx 없이 import 가능하다.

실행: pytest tests/test_preprocess.py  (경로 설정은 tests/conftest.py)
"""
import pandas as pd
import pytest

import preprocess
from preprocess import (
    AS_OF,
    CRD_RANK,
    evco,
    iso_lag_days,
    kr_etp_corrupt_mask,
    kr_instrument_type,
    listing_status,
    maturity_status,
    norm_grd,
    yyyymmdd_to_iso,
)


@pytest.fixture(autouse=True)
def isolate_report():
    """log_rule 이 쌓는 모듈 전역 report 를 테스트 간 격리한다.
    (yyyymmdd_to_iso 등 일부 함수는 변환과 동시에 리포트를 남긴다)"""
    saved = list(preprocess.report)
    preprocess.report.clear()
    yield
    preprocess.report[:] = saved


class TestImportSafety:
    """구조 요구사항: import 시점 부작용 없음 (main 실행 경로에서만 검사)."""

    def test_module_importable_without_datasets(self):
        # datasets 부재 시 sys.exit 하던 구버전과 달리, import 만으로는
        # 어떤 검사·종료도 일어나지 않아야 한다 (이 테스트가 곧 증명).
        assert callable(preprocess.main)

    def test_as_of_constants_consistent(self):
        assert AS_OF == "2026-07-11"
        assert preprocess.AS_OF_COMPACT == "20260711"


class TestNormGrd:
    """R9: 신용등급 끝자리 '0'(플랫 표기) 제거."""

    @pytest.mark.parametrize("raw, expected", [
        ("AA0", "AA"), ("AAA0", "AAA"), ("A0", "A"), ("BBB0", "BBB"), ("D0", "D"),
    ])
    def test_flat_zero_suffix_removed(self, raw, expected):
        assert norm_grd(raw) == expected

    @pytest.mark.parametrize("raw", ["AAA", "AA+", "AA-", "BBB", "C"])
    def test_regular_grades_unchanged(self, raw):
        assert norm_grd(raw) == raw

    def test_non_grade_token_unchanged(self):
        # 등급 체계 밖 문자열은 조용히 바꾸지 않는다 (정보 파괴 금지).
        assert norm_grd("A00") == "A00"
        assert norm_grd("등급없음") == "등급없음"

    def test_missing_returns_none(self):
        assert norm_grd(None) is None
        assert norm_grd(float("nan")) is None


class TestCrdRank:
    """R9: 등급 서열 rank — 'AA 이상' = rank<=3 계약."""

    def test_rank_ordering(self):
        assert CRD_RANK["AAA"] == 1
        assert CRD_RANK["AA"] == 3      # 'AA 이상' 필터 경계
        assert CRD_RANK["D"] == 20
        assert len(CRD_RANK) == 20 and len(set(CRD_RANK.values())) == 20


class TestEvco:
    """R10: 평가사 병기 등급 분해 — 스플릿 시 보수적(최저) 채택."""

    def test_split_rating_takes_worst(self):
        # AAA(1) vs AA+(2) → 최저는 AA+.
        assert evco("AAA, AA+") == (2, "AA+", 2)

    def test_flat_notation_normalized_inside(self):
        # 병기 항목에도 R9 정규화가 적용돼야 한다: AA0→AA(3) vs AA-(4).
        assert evco("AA0, AA-") == (2, "AA-", 4)

    def test_single_rating(self):
        assert evco("AAA") == (1, "AAA", 1)

    def test_unknown_tokens_counted_but_unranked(self):
        # 미지 토큰은 개수만 세고 rank 는 None — 조용한 오분류 방지.
        assert evco("무등급") == (1, None, None)

    def test_missing(self):
        assert evco(None) == (None, None, None)


class TestYyyymmddToIso:
    """R5: 숫자/문자 YYYYMMDD → ISO. 0·형식 위반은 NULL."""

    def test_conversion_and_sentinels(self):
        s = pd.Series(["20260330", "20260330.0", "00000000", "0", None, "2026033"])
        out = yyyymmdd_to_iso(s, "테스트", "MAT_DT")
        assert out.tolist() == ["2026-03-30", "2026-03-30", None, None, None, None]

    def test_report_logged(self):
        # 변환·파싱 불가 건수가 리포트에 남아야 한다 (모든 규칙은 기록 원칙).
        yyyymmdd_to_iso(pd.Series(["20260101", "0"]), "테스트", "ISU_DT")
        rules = [r for r in preprocess.report if r["컬럼"] == "ISU_DT"]
        assert {r["영향행수"] for r in rules} == {1}  # 성공 1건 + 실패 1건


class TestMaturityStatus:
    """R8(8/5 정정): 만기 4-상태 — 파싱 실패는 unknown 으로 분리."""

    def test_unknown_for_missing(self):
        # 기존 버그: 파싱 불가(=NULL) 행이 만기도래로 오포함 → unknown 분리가 핵심.
        assert maturity_status(None) == "unknown"
        assert maturity_status(float("nan")) == "unknown"

    def test_matured_before_as_of(self):
        assert maturity_status("2026-07-10") == "matured"

    def test_matures_on_snapshot(self):
        assert maturity_status("2026-07-11") == "matures_on_snapshot"

    def test_active_after_as_of(self):
        assert maturity_status("2026-07-12") == "active"

    def test_perpetual_sentinel_is_active(self):
        # 9999-12-31 은 pandas Timestamp 범위 밖 — 문자열 비교라 안전하게 active.
        # (dev-kyung 은 불명 처리 — 우리는 active + drv_is_perpetual 로 분리 표기)
        assert maturity_status("9999-12-31") == "active"

    def test_custom_as_of(self):
        assert maturity_status("2026-07-11", as_of="2026-07-12") == "matured"


class TestKrEtpCorruptMask:
    """R24: 국내ETP 손상 행 탐지 — 행 번호 하드코딩 없이 도메인 규칙으로."""

    def _df(self, itm, nm):
        return pd.DataFrame({"pd_itm_no": itm, "pd_nm": nm})

    def test_detects_short_key_and_dot_name(self):
        # 실측 손상 행: pd_itm_no='KR', pd_nm='.' — 둘 중 하나만으로도 걸린다.
        df = self._df(["KR7069500007", "KR", "KR7069500007"],
                      ["KODEX 200", ".", "."])
        assert kr_etp_corrupt_mask(df).tolist() == [False, True, True]

    def test_normal_rows_pass(self):
        df = self._df(["KR7069500007", "KR70193M0005"], ["KODEX 200", "메리츠 금융채"])
        assert not kr_etp_corrupt_mask(df).any()

    def test_missing_key_is_corrupt(self):
        # 키 결측도 12자리 형식 위반 → 격리 대상 (조용한 통과 금지).
        df = self._df([None], ["이름있음"])
        assert kr_etp_corrupt_mask(df).tolist() == [True]

    def test_lowercase_key_is_corrupt(self):
        # ISIN 은 대문자 체계 — 소문자 혼입은 형식 위반으로 잡는다.
        df = self._df(["kr7069500007"], ["KODEX 200"])
        assert kr_etp_corrupt_mask(df).tolist() == [True]


class TestKrInstrumentType:
    """R25(국내): pd_grp_no → ETF/ETN. 미지 값은 NULL 로 드러낸다."""

    @pytest.mark.parametrize("raw, expected", [
        ("ETF", "ETF"), ("ETN", "ETN"), ("etf", "ETF"), (" ETN ", "ETN"),
    ])
    def test_known_values(self, raw, expected):
        assert kr_instrument_type(raw) == expected

    def test_unknown_value_returns_none(self):
        # 조용히 임의 매핑하지 않는다 — NULL 이면 R25 assertion 이 잡아낸다.
        assert kr_instrument_type("ELW") is None

    def test_missing(self):
        assert kr_instrument_type(None) is None


class TestListingStatus:
    """R26(국내): delisted > suspended > active 우선순위 판정."""

    def test_past_end_date_is_delisted(self):
        assert listing_status("20250116", "0") == "delisted"

    def test_delisted_takes_precedence_over_suspended(self):
        # 종료 상품 다수가 정지 플래그도 1 — 종료 우선이어야 이중 계상이 없다.
        assert listing_status("20250116", "1") == "delisted"

    def test_open_sentinel_with_halt_flag_is_suspended(self):
        # pd_tr_yn 은 1='거래정지' (1 을 거래 가능으로 읽으면 의미 반전).
        assert listing_status("99991231", "1") == "suspended"

    def test_open_sentinel_trading_is_active(self):
        assert listing_status("99991231", "0") == "active"

    def test_missing_end_date_uses_halt_flag(self):
        assert listing_status(None, "1") == "suspended"
        assert listing_status(None, None) == "active"

    def test_zero_sentinel_not_delisted(self):
        assert listing_status("00000000", "0") == "active"

    def test_numeric_contaminated_token_still_parsed(self):
        # 숫자 캐스팅 오염('20250116.0') 방어 — 원본은 text 선언이지만 안전하게.
        assert listing_status("20250116.0", "0") == "delisted"

    def test_as_of_boundary(self):
        # AS_OF 당일 종료는 '이전'이 아니므로 delisted 가 아니다.
        assert listing_status("20260711", "0") == "active"
        assert listing_status("20260710", "0") == "delisted"


class TestIsoLagDays:
    """R30: AS_OF 대비 지연일수 — ISO/compact 두 형식, 결측은 NA."""

    def test_iso_format(self):
        out = iso_lag_days(pd.Series(["2026-06-15", "2026-07-11", None]))
        assert out.tolist()[:2] == [26, 0]
        assert pd.isna(out.iloc[2])

    def test_compact_format(self):
        out = iso_lag_days(pd.Series(["20260611"]), fmt="%Y%m%d")
        assert out.tolist() == [30]

    def test_unparseable_is_na(self):
        out = iso_lag_days(pd.Series(["not-a-date"]))
        assert pd.isna(out.iloc[0])

    def test_whitespace_stripped(self):
        # 고정폭 패딩 잔재 방어 — 공백 낀 토큰도 파싱돼야 한다.
        out = iso_lag_days(pd.Series([" 2026-07-01 "]))
        assert out.tolist() == [10]
