"""4종 데이터의 지역·자산군 원본값을 표준 코드로 매핑하는 표를 만든다.

원칙(PROJECT_GUIDE.md와 동일):
- 런타임 LLM 추론 없이, 사람이 검토한 고정 매핑만 사용한다.
- 확실하지 않은 값은 mapped로 억지로 끼워맞추지 않고 ambiguous로 남긴다.
- 원본에서 관측되지 않은 값은 매핑표에 넣지 않는다(추측 금지).
"""

from pathlib import Path

import pandas as pd

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"
OUT_PATH = Path(__file__).resolve().parent.parent / "data_clean" / "taxonomy_mapping.csv"

# 표준 지역 코드: 국내ETF·공모펀드가 이미 쓰는 한글 버킷 체계를 기준으로 삼음
REGION_MAP = {
    # (source_table, source_column): {source_value: (standard_code, status, note)}
    ("bond", "PD_CTRY_CD"): {
        "KR": ("KR", "mapped", None),
        "XS": ("OTHER", "ambiguous", "Euroclear/Clearstream 국제 코드, 특정 국가 아님"),
    },
    ("domestic_etf", "wu_inv_rgn"): {
        "국내": ("KR", "mapped", None),
        "미국": ("US", "mapped", None),
        "글로벌": ("GLOBAL", "mapped", None),
        "중국": ("CHINA", "mapped", None),
        "아시아": ("ASIA_OTHER", "mapped", None),
        "일본": ("JAPAN", "mapped", None),
        "인도": ("INDIA", "mapped", None),
        "남미/북미": ("AMERICAS_OTHER", "mapped", None),
        "유럽": ("EUROPE", "mapped", None),
        "베트남": ("VIETNAM", "mapped", None),
        "이머징/브릭스": ("EMERGING_BRIC", "mapped", None),
    },
    ("fund", "fd_ivst_rgn_desc"): {
        "글로벌": ("GLOBAL", "mapped", None),
        "국내": ("KR", "mapped", None),
        "아시아": ("ASIA_OTHER", "mapped", None),
        "남미/북미": ("AMERICAS_OTHER", "mapped", None),
        "이머징/브릭스": ("EMERGING_BRIC", "mapped", None),
        "유럽": ("EUROPE", "mapped", None),
        "중동/아프리카": ("MIDDLE_EAST_AFRICA", "mapped", None),
    },
    ("overseas_etf", "wu_inv_rgn"): {
        "United States of America": ("US", "mapped", None),
        "Global": ("GLOBAL", "mapped", None),
        "Global Ex US": ("GLOBAL", "ambiguous", "미국 제외 글로벌. GLOBAL과 다른 의미라 교차 비교 시 주의"),
        "Global Emerging Markets": ("EMERGING_BRIC", "mapped", None),
        "China": ("CHINA", "mapped", None),
        "Europe": ("EUROPE", "mapped", None),
        "Japan": ("JAPAN", "mapped", None),
        "India": ("INDIA", "mapped", None),
        "Brazil": ("AMERICAS_OTHER", "mapped", None),
        "Asia Pacific ex Japan": ("ASIA_OTHER", "mapped", None),
        "Korea": ("KR", "mapped", None),
        "Asia Pacific": ("ASIA_OTHER", "mapped", None),
        "United Kingdom": ("EUROPE", "mapped", None),
        "Israel": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "Germany": ("EUROPE", "mapped", None),
        "Switzerland": ("EUROPE", "mapped", None),
        "Latin America": ("AMERICAS_OTHER", "mapped", None),
        "Canada": ("AMERICAS_OTHER", "mapped", None),
        "Taiwan": ("ASIA_OTHER", "mapped", None),
        "Mexico": ("AMERICAS_OTHER", "mapped", None),
        "Australia": ("OTHER", "ambiguous", "오세아니아, 기존 7개 버킷에 없음"),
        "Vietnam": ("VIETNAM", "mapped", None),
        "Greater China": ("CHINA", "mapped", None),
        "Saudi Arabia": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "EuroZone": ("EUROPE", "mapped", None),
        "Norway": ("EUROPE", "mapped", None),
        "Indonesia": ("ASIA_OTHER", "mapped", None),
        "Poland": ("EUROPE", "mapped", None),
        "Malaysia": ("ASIA_OTHER", "mapped", None),
        "Italy": ("EUROPE", "mapped", None),
        "Peru": ("AMERICAS_OTHER", "mapped", None),
        "Austria": ("EUROPE", "mapped", None),
        "Qatar": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "Greece": ("EUROPE", "mapped", None),
        "Finland": ("EUROPE", "mapped", None),
        "United Arab Emirates": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "Hong Kong": ("CHINA", "ambiguous", "중화권이나 별도 시장. CHINA로 묶었으나 재검토 필요"),
        "Colombia": ("AMERICAS_OTHER", "mapped", None),
        "Turkey": ("MIDDLE_EAST_AFRICA", "ambiguous", "유럽/중동 경계, 관례상 중동아프리카로 분류"),
        "Africa": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "Ireland": ("EUROPE", "mapped", None),
        "Chile": ("AMERICAS_OTHER", "mapped", None),
        "Argentina": ("AMERICAS_OTHER", "mapped", None),
        "Belgium": ("EUROPE", "mapped", None),
        "Philippines": ("ASIA_OTHER", "mapped", None),
        "BRIC": ("EMERGING_BRIC", "mapped", None),
        "Iceland": ("EUROPE", "mapped", None),
        "North America": ("AMERICAS_OTHER", "ambiguous", "미국 포함 여부 불명확"),
        "Singapore": ("ASIA_OTHER", "mapped", None),
        "Denmark": ("EUROPE", "mapped", None),
        "ASEAN": ("ASIA_OTHER", "mapped", None),
        "Spain": ("EUROPE", "mapped", None),
        "France": ("EUROPE", "mapped", None),
        "Netherlands": ("EUROPE", "mapped", None),
        "Thailand": ("ASIA_OTHER", "mapped", None),
        "Kuwait": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "South Africa": ("MIDDLE_EAST_AFRICA", "mapped", None),
        "New Zealand": ("OTHER", "ambiguous", "오세아니아, 기존 7개 버킷에 없음"),
        "Sweden": ("EUROPE", "mapped", None),
    },
}

ASSET_CLASS_MAP = {
    ("domestic_etf", "wu_inv_ast_type"): {
        "주식": ("EQUITY", "mapped", None),
        "채권": ("BOND", "mapped", None),
        "원자재": ("COMMODITY", "mapped", None),
        "혼합자산": ("MIXED", "mapped", None),
        "단기자금": ("MONEY_MARKET", "mapped", None),
        "통화": ("CURRENCY", "mapped", None),
        "부동산": ("REAL_ESTATE", "mapped", None),
        "기타": ("OTHER", "ambiguous", "원본에 세부 구분 없음"),
    },
    ("overseas_etf", "wu_inv_ast_type"): {
        "Equity": ("EQUITY", "mapped", None),
        "Alternatives": ("ALTERNATIVES", "ambiguous", "원자재/부동산/기타 중 무엇인지 원본에 세부 구분 없음"),
        "Bond": ("BOND", "mapped", None),
        "Mixed Assets": ("MIXED", "mapped", None),
        "Commodity": ("COMMODITY", "mapped", None),
        "Money Market": ("MONEY_MARKET", "mapped", None),
    },
    ("fund", "or_attr_desc"): {
        "주식형": ("EQUITY", "mapped", None),
        "재간접": ("FUND_OF_FUNDS", "ambiguous", "재투자 대상 펀드의 실제 자산군 불명"),
        "채권혼합": ("MIXED", "mapped", "채권 비중 높은 혼합형"),
        "채권형": ("BOND", "mapped", None),
        "06": ("OTHER", "unmapped", "정의되지 않은 코드. PROJECT_GUIDE도 파생형 후보로만 언급, 확정 아님"),
        "주식혼합": ("MIXED", "mapped", "주식 비중 높은 혼합형"),
        "MMF": ("MONEY_MARKET", "mapped", None),
        "혼합자산": ("MIXED", "mapped", None),
        "특별자산": ("ALTERNATIVES", "mapped", None),
        "임대형": ("REAL_ESTATE", "mapped", None),
        "대출형": ("ALTERNATIVES", "ambiguous", "사모대출형 성격, 대체투자로 분류"),
    },
}

# 국내채권은 컬럼 전체가 채권이라 파생 매핑이 필요 없음(별도 상수 처리)
BOND_ASSET_CLASS_CONSTANT = "BOND"


def _observed_values(table: str, column: str) -> pd.Series:
    files = {
        "bond": "PRBD01N001_국내채권마스터_20260711_datarows.xlsx",
        "domestic_etf": "PREF01N001_국내ETF마스터_20260711_datarows.xlsx",
        "overseas_etf": "PREF02N001_해외ETF마스터_20260711_datarows.xlsx",
        "fund": "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx",
    }
    df = pd.read_excel(DATASETS_DIR / files[table], dtype=str, usecols=[column])
    return df[column].value_counts(dropna=True)


def build() -> pd.DataFrame:
    rows = []
    for dimension, mapping in (("region", REGION_MAP), ("asset_class", ASSET_CLASS_MAP)):
        for (table, column), value_map in mapping.items():
            observed = _observed_values(table, column)

            missing = set(observed.index) - set(value_map.keys())
            if missing:
                raise ValueError(
                    f"[{table}.{column}] 매핑표에 없는 관측값 발견: {missing} — 매핑을 추가해야 함"
                )

            for value, (std_code, status, note) in value_map.items():
                rows.append(
                    {
                        "source_table": table,
                        "source_column": column,
                        "source_value": value,
                        "standard_dimension": dimension,
                        "standard_code": std_code,
                        "mapping_status": status,
                        "observed_count": int(observed.get(value, 0)),
                        "note": note,
                    }
                )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = build()
    OUT_PATH.parent.mkdir(exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"매핑 {len(df)}행 -> {OUT_PATH}")
    print(df["mapping_status"].value_counts())
