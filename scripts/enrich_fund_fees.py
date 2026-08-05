"""공모펀드 보수 외부 보강.

주최 측 제공 공모펀드 데이터에는 보수 정보가 없다(과제 자료에 명시, 검증됨).
KOFIA 전자공시서비스에서 받은 '펀드별 보수비용비교' 파일(표준코드 기준)을
fund_master의 std_itm_no와 조인해서 채운다.

규칙: 이 값은 주최 측 기준 데이터가 아니라 외부 보강이므로, fee_source 컬럼으로
출처를 항상 구분해서 표시한다(과제 규칙: 상충 시 주최 측 데이터 우선 원칙과
'근거 표시' 원칙을 따름). 조인 안 된 행은 그대로 결측 유지, 추측하지 않는다.
"""

from pathlib import Path

import pandas as pd

DATA_CLEAN_DIR = Path(__file__).resolve().parent.parent / "data_clean"
FEE_FILE = Path(__file__).resolve().parent.parent / "external_data" / "펀드별 보수비용비교_20260805.xls"
FEE_SOURCE = "external_kofia_20260805"


def load_fee_table() -> pd.DataFrame:
    fee = pd.read_excel(FEE_FILE, header=1)
    fee["표준코드"] = fee["표준코드"].astype(str).str.strip()
    fee = fee.rename(
        columns={
            "합계\n(A)": "fee_mgmt_pct",
            "TER\n(A+B)": "fee_ter_pct",
        }
    )
    # 같은 표준코드가 여러 행일 가능성 대비: 표준코드 기준 유일성 확인 후 중복 시 첫 값만 사용
    fee = fee.drop_duplicates(subset="표준코드", keep="first")
    return fee[["표준코드", "fee_mgmt_pct", "fee_ter_pct"]]


def enrich() -> pd.DataFrame:
    fund = pd.read_csv(DATA_CLEAN_DIR / "fund_master.csv")
    fund["std_itm_no"] = fund["std_itm_no"].astype(str).str.strip()

    fee = load_fee_table()
    merged = fund.merge(fee, left_on="std_itm_no", right_on="표준코드", how="left").drop(
        columns=["표준코드"]
    )

    merged["fee_available"] = merged["fee_mgmt_pct"].notna()
    merged["fee_source"] = merged["fee_available"].map({True: FEE_SOURCE, False: None})

    return merged


if __name__ == "__main__":
    enriched = enrich()
    out_path = DATA_CLEAN_DIR / "fund_master.csv"
    enriched.to_csv(out_path, index=False)

    matched = enriched["fee_available"].sum()
    print(f"공모펀드 보수 조인: {matched}/{len(enriched)} ({matched / len(enriched):.1%}) -> {out_path}")
