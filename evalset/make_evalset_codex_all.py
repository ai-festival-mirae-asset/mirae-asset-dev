"""블라인드 30문항의 기대값을 상품 데이터에 직접 조회해 만든다.

엔진·조회문 목록을 가져오지 않는다. 날짜 조건은 측정일 2026-09-06에 고정한다.
"""
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
FEE = " + ".join(f"coalesce(try_cast({col} as double), 0)" for col in
                 ("sale_co_rwrd_r", "or_co_rwrd_r", "trusc_rwrd_r", "ofwk_trus_rwrd_r"))
ETF = "drv_instrument_type='ETF' AND drv_listing_status='active'"
ACTIVE = "drv_maturity_status='active'"


def names(sql, n=1, top=None, ordered=False):
    result = {"type": "sql_names", "name": "직접 조회한 조건·순위 상품", "sql": sql, "min_hit": n}
    if top:
        result["top"] = top
    if ordered:
        result["ordered"] = True
    return result


def note(*terms):
    return {"type": "note_any", "name": "해석·자료 한계", "terms": list(terms)}


def terms(*words):
    return {"type": "answer_has_all", "name": "요청한 항목·관계", "terms": list(words)}


def one():
    return {"type": "answer_regex", "name": "한 건만 표시", "pattern": r"\A(?!.*\n\s*2\.\s).+"}


def etfs(where="", order="", n=1, top=None, ordered=False):
    return names("SELECT pd_abrv_nm,pd_nm FROM kr_etp WHERE " + ETF +
                 (" AND " + where if where else "") + (" ORDER BY " + order if order else ""), n, top, ordered)


def funds(where="", order="", n=1, top=None, table="fund_master", ordered=False):
    return names(f"SELECT itm_abrv_nm,itm_nm FROM {table} WHERE " + (where or "TRUE") +
                 (" ORDER BY " + order if order else ""), n, top, ordered)


def overseas(where="", order="", n=1, top=None):
    return names("SELECT pd_abrv_nm,pd_nm FROM global_etf WHERE drv_instrument_type='ETF'" +
                 (" AND " + where if where else "") + (" ORDER BY " + order if order else ""), n, top)


def main():
    con = duckdb.connect(str(ROOT / "storage/output/products.duckdb"), read_only=True)
    specs = {
        1: [names(f"SELECT PD_ABRV_NM,PD_NM FROM kr_bond WHERE {ACTIVE} AND try_cast(SRFC_IRT as double)=3", 5)],
        2: [etfs("try_cast(du_clpr as double)>0 AND try_cast(du_clpr as double)<=10000", n=5)],
        3: [etfs("try_cast(pd_net_tamt as double)>0", "try_cast(pd_net_tamt as double) DESC,pd_itm_no", top=1), one()],
        4: [overseas("try_cast(cu_charge_rt as double)>0", "try_cast(cu_charge_rt as double),try_cast(du_last_aum as double) DESC,pd_itm_no", top=1), one()],
        5: [funds(f"sale_yn='판매중' AND prvo_pbff_desc='공모' AND ({FEE})>0", f"round(({FEE}),4),itm_no", n=5, top=5, ordered=True), note("보수 분해", "4종", "합으로", "합)")],
        6: [etfs("try_cast(du_vlty_1m as double)<>0", "try_cast(du_vlty_1m as double),pd_itm_no", n=5, top=5, ordered=True), terms("1개월", "변동성")],
        7: [etfs("try_cast(du_vlty_3m as double)<>0 AND try_cast(du_vlty_3m as double)<10", n=5), terms("3개월", "변동성")],
        8: [etfs("try_cast(du_vlty_6m as double)>=20", n=5), terms("6개월", "변동성")],
        9: [funds("ovrs_fd_desc='국내' AND or_attr_desc='주식형' AND try_cast(fd_nast_suma as double)>0", "try_cast(fd_nast_suma as double) DESC,itm_no", n=5, top=5), note("투자지역", "지역", "결측")],
        10: [names("SELECT PD_ABRV_NM,PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND try_cast(SRFC_IRT as double)>=4 AND replace(MAT_DT,'-','') BETWEEN '20260906' AND '20290906'", 5)],
        11: [overseas("wu_inv_rgn='United States of America' AND wu_inv_ast_type='Equity' AND try_cast(cu_charge_rt as double)>0 AND try_cast(cu_charge_rt as double)<=0.2", n=5)],
        12: [etfs("replace(pd_lstg_dt,'-','')>='20200101' AND try_cast(pd_net_tamt as double)>=1000000000000", n=3), terms("순자산"), {"type":"answer_regex","name":"이름·순자산만 표시","pattern":r"\A(?!.*\n\s*\d+\.\s[^\n]*(?:총보수|위험등급|상장일|종가)\s*[=:：]).+"}],
        13: [funds(f"prvo_pbff_desc='공모' AND drv_risk_grade='5' AND ({FEE})>0 AND ({FEE})<=0.5", n=1), terms("총보수"), note("4종", "보수 분해", "합으로", "합)"), {"type":"answer_regex","name":"이름·총보수만 표시","pattern":r"\A(?!.*\n\s*\d+\.\s[^\n]*(?:순자산|위험등급|판매상태|판매보수)\s*[=:：]).+"}],
        14: [names("SELECT DISTINCT COMPST_ISU_NM FROM etf_constituent WHERE COMPST_ISU_NM ILIKE '%ACCENTURE%'"), note("부분 일치", "유사 명칭", "후보"), terms("존재 근거 아님")],
        15: [funds("han_clas_fee_type='수수료미징구' AND han_clas_sales_channel='온라인'", table="fund_class"), one(), note("결측", "값이 없는", "판정에서 제외")],
        16: [names(f"SELECT PD_ABRV_NM,PD_NM FROM kr_bond WHERE {ACTIVE} AND drv_is_buyable='Y' AND PD_PEN_TR_YN='Y'", 2)],
        17: [etfs("pd_itm_no IN (SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM ILIKE 'MICROSOFT%') AND try_cast(du_er_1y as double)<>0", "try_cast(du_er_1y as double) DESC,pd_itm_no", n=5, top=5), terms("해외", "펀드"), note("보유 종목", "보유종목", "미수집", "불가")],
        18: [etfs("pd_itm_no IN (SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM ILIKE 'APPLE%') AND try_cast(cu_charge_rt as double)>0 AND try_cast(cu_charge_rt as double)<=0.5", "try_cast(pd_net_tamt as double) DESC,pd_itm_no", top=1), terms("위험등급"), note("보수", "결측", "값 보유")],
        19: [etfs("pd_itm_no IN (SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM LIKE '현대차%')", "try_cast(pd_net_tamt as double) DESC,pd_itm_no", top=1), terms("운용"), note("접두", "근사", "시작", "법적", "미수집")],
        20: [etfs("try_cast(du_vlty_3m as double)<>0 AND try_cast(du_vlty_3m as double)<15 AND try_cast(pd_net_tamt as double)>=100000000000", "try_cast(du_vlty_3m as double),pd_itm_no", n=3, top=3, ordered=True)],
        21: [etfs("try_cast(du_er_1m as double)>5 AND replace(pd_lstg_dt,'-','')>='20220101'", "try_cast(du_er_1m as double) DESC,pd_itm_no", n=3, top=3, ordered=True), terms("종가")],
        22: [names("SELECT PD_ABRV_NM,PD_NM FROM kr_bond WHERE drv_is_buyable='Y' AND STD_PD_MCLS_NM='회사채' AND try_cast(drv_crd_grd_rank as integer) BETWEEN 2 AND 4 AND replace(MAT_DT,'-','') BETWEEN '20260906' AND '20280906' AND try_cast(SRFC_IRT as double)>0 ORDER BY try_cast(SRFC_IRT as double) DESC,MAT_DT,PD_NO", 3, 3, True), terms("듀레이션")],
        23: [overseas("wu_inv_rgn='Europe' AND wu_inv_ast_type='Bond' AND try_cast(cu_charge_rt as double)>0 AND try_cast(cu_charge_rt as double)<=0.3 AND try_cast(du_last_aum as double)>=100000000", n=1)],
        24: [funds(f"han_clas_sales_channel='온라인' AND han_clas_fee_type='수수료미징구' AND drv_risk_grade='4' AND ({FEE})>0", f"round(({FEE}),4),itm_no", n=3, top=3, table="fund_class", ordered=True), note("4종", "보수 분해", "합으로", "합)")],
    }
    for i, months in ((6, 1), (7, 3), (8, 6)):
        specs[i].append({"type":"answer_regex", "name":"요청 기간 값을 상품 행에 표시", "pattern":rf"\n\s*1\.\s[^\n]*{months}개월 변동성"})
        specs[i].append({"type":"answer_regex", "name":"다섯 건을 넘겨 표시하지 않음", "pattern":r"\A(?!.*\n\s*6\.\s).+"})
    partial = {5, 9, 13, 14, 15, 17, 18, 19, 24}
    sources = {1:"PRBD01N001", 2:"PREF01N001", 3:"PREF01N001", 4:"PREF02N001", 5:"PRFD01N001", 6:"PREF01N001", 7:"PREF01N001", 8:"PREF01N001", 9:"PRFD01N001", 10:"PRBD01N001", 11:"PREF02N001", 12:"PREF01N001", 13:"PRFD01N001", 15:"PRFD01N001", 16:"PRBD01N001", 20:"PREF01N001", 21:"PREF01N001", 22:"PRBD01N001", 23:"PREF02N001", 24:"PRFD01N001"}
    items, checks = [], []
    lines = (ROOT / "evalset/blind_codex_all.txt").read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, 1):
        level, question, gold = (x.strip() for x in line.split("|", 2))
        cs = specs.get(i, [{"type": "evidence_min", "name": "거절 근거", "n": 1}])
        basis = ["블라인드 출제 후 DuckDB 직접 조회. 날짜 조건은 2026-09-06 측정 기준."]
        for ck in list(cs):
            if "sql" in ck:
                rows = con.execute(ck["sql"]).fetchall()
                if not rows:
                    cs.remove(ck)
                    cs[:] = [c for c in cs if c.get("type") != "answer_has_all"]
                    cs.append({"type":"answer_regex", "name":"독립 조회 0건·목록 없음", "pattern":r"\A(?!.*(?:대표:|\n\s*\d+\.\s(?!field\b))).*(?:결과\s*0건|조건[^\n]*(?:없|0건)|확인할 수 없)"})
                    if i != 23:
                        partial.add(i)
                else:
                    assert len(rows) >= ck.get("min_hit", 1), (i, len(rows))
                basis.append(f"조건 일치 {len(rows)}행; 첫 3행: " + json.dumps(rows[:3], ensure_ascii=False))
                print(f"CA-{i:02}: {len(rows)}행; {rows[:3]}")
        if i in sources:
            cs.append({"type":"evidence_source_any", "name":"원천 출처", "sources":[sources[i]]})
        item = {"id": f"CA-{i:02}", "level": "트랩" if i > 24 else level,
                "category": "블라인드/" + ("답변불가" if i > 24 else "전체 상품군"),
                "question": question, "channels": ["validation"] if i > 24 else ["sql"],
                "behavior": "refuse" if i > 24 else ("partial" if i in partial else "answer"),
                "gold": gold, "basis": " ".join(basis)}
        items.append(item)
        checks.append({"id":item["id"], "checks":cs})
    # 만든 이름은 주최 상품 네 표 모두에서 직접 확인한다.
    for table, cols in (("kr_bond",("PD_NM","PD_ABRV_NM")),("kr_etp",("pd_nm","pd_abrv_nm")),
                        ("global_etf",("pd_nm","pd_abrv_nm")),("fund_master",("itm_nm","itm_abrv_nm"))):
        assert con.execute(f"SELECT count(*) FROM {table} WHERE {cols[0]} LIKE '%달빛고래우주여행%' OR {cols[1]} LIKE '%달빛고래우주여행%'").fetchone()[0] == 0
    for name, rows in (("evalset", items), ("checks", checks)):
        (ROOT / f"evalset/{name}_codex_all.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in rows),encoding="utf-8")


if __name__ == "__main__":
    main()
