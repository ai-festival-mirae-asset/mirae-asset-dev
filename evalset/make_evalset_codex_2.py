"""블라인드 30문항의 정답을 원천 표에서 직접 계산한다. 엔진은 가져오지 않는다."""
import json
from collections import Counter
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "storage/output/products.duckdb"
con = duckdb.connect(str(DB), read_only=True)
ACTIVE = "drv_instrument_type='ETF' AND drv_listing_status='active'"


def num(col):
    return f"TRY_CAST({col} AS DOUBLE)"


def names(sql, title, top=None, minimum=1, ordered=False):
    rows = con.execute(sql).fetchall()
    if not rows:
        return {"type": "answer_regex", "name": title + "(원천 0건)",
                "basis_sql": sql,
                "pattern": r"(?:결과\s*0건|조건.{0,35}(?:없|0)|해당.{0,25}없|만족.{0,25}없)"}
    check = {"type": "sql_names", "name": title, "sql": sql,
             "min_hit": min(minimum, len(rows)), "ordered": ordered}
    if top:
        check["top"] = top
    return check


def note(title, *terms):
    return {"type": "note_any", "name": title, "terms": list(terms)}


def etf(where, order="pd_itm_no"):
    return f"SELECT pd_abrv_nm,pd_nm FROM kr_etp WHERE {ACTIVE} AND ({where}) ORDER BY {order}"


def fund(where, order="itm_no"):
    return f"SELECT itm_abrv_nm,itm_nm FROM fund_master WHERE {where} ORDER BY {order}"


def bond(where, order="PD_NO"):
    return f"SELECT PD_ABRV_NM,PD_NM FROM kr_bond WHERE drv_maturity_status='active' AND ({where}) ORDER BY {order}"


def global_etf(where, order="pd_itm_no"):
    return f"SELECT pd_abrv_nm,pd_nm FROM global_etf WHERE drv_instrument_type='ETF' AND ({where}) ORDER BY {order}"


def prefix_sql(prefix, columns="e.pd_abrv_nm,e.pd_nm"):
    return (f"SELECT {columns} FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) "
            f"WHERE {num('e.pd_net_tamt')}>0 AND e.pd_itm_no IN "
            f"(SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM ILIKE '{prefix}%') "
            f"ORDER BY {num('e.pd_net_tamt')} DESC,e.pd_itm_no")


FEE_NOTE = note("보수 자료 한계", "결측", "미확정", "일부", "커버리지", "채워", "0인", "0 값")
APPROX_NOTE = note("법적 관계 아닌 접두 후보", "접두", "근사", "시작하는", "법적")
CHECKS = {
 1: [names(bond("CURR_CD='KRW' AND drv_is_buyable='Y' AND TRY_CAST(drv_crd_grd_rank AS INT)<=3"), "매수가능 AA 이상", minimum=5)],
 2: [names(etf(f"{num('pd_net_tamt')}>0", f"{num('pd_net_tamt')} DESC,pd_itm_no"), "순자산 상위 3", top=3, minimum=3, ordered=True)],
 3: [names(etf(f"{num('cu_charge_rt')}>0", f"{num('cu_charge_rt')},pd_itm_no"), "보수 낮은 후보", top=10), FEE_NOTE],
 4: [names(fund("sale_yn='판매중' AND prvo_pbff_desc='공모'"), "판매중 공모 후보")],
 5: [names(bond("replace(MAT_DT,'-','')>'20260906'", "replace(MAT_DT,'-',''),PD_NO"), "가까운 만기 후보", top=10)],
 6: [names(etf(f"{num('pd_dvid_pay_cnt')}=12"), "연 12회 지급")],
 7: [names(etf(f"{num('du_vlty_1m')}>0", f"{num('du_vlty_1m')},pd_itm_no"), "1개월 변동성 최저", top=1),
     {"type":"answer_regex","name":"상품 행에 1개월 값","pattern":r"1개월\s*변동성\s*[:：]?\s*\d+[.]\d{2}%"}],
 8: [names(fund("itm_nm LIKE '%국민성장%'"), "펀드 식별"), note("서술 자료 한계", "서술", "미수집", "보유하지", "확인할 수 없")],
 9: [names("SELECT DISTINCT e.pd_abrv_nm,e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM ILIKE 'CAMBRICON%' AND e.pd_nm LIKE '%중국%' AND e.pd_nm LIKE '%반도체%'", "중국 반도체 편입"), note("편입 관계", "편입", "담", "들어", "포함")],
 10: [names(etf(f"(coalesce(cu_base_index,ref_base_index) ILIKE '%NASDAQ%100%' OR pd_nm LIKE '%나스닥100%' OR pd_abrv_nm LIKE '%나스닥100%') AND {num('cu_charge_rt')}>0 AND {num('cu_charge_rt')}<=0.5", f"{num('cu_charge_rt')},pd_itm_no"), "지수와 보수 상위 3", top=3, minimum=3, ordered=True), FEE_NOTE],
 11: [names(etf(f"pd_dvid_pay_months ILIKE '%April%' AND {num('pd_divd_amt_ann')}>=500"), "4월과 연간 500원")],
 12: [names(global_etf(f"wu_inv_rgn='Europe' AND wu_inv_ast_type='Bond' AND {num('cu_charge_rt')}>0 AND {num('cu_charge_rt')}<=0.3"), "유럽 채권형 보수")],
 13: [names(fund(f"ovrs_fd_desc='국내' AND zrin_btyp_nm LIKE '%주식형%' AND prvo_pbff_desc='공모' AND {num('fd_yr1_ern_r')}<>0", f"{num('fd_yr1_ern_r')} DESC,itm_no"), "국내 주식형 공모 1년 상위", top=5, minimum=5, ordered=True)],
 14: [names(etf(f"{num('du_vlty_3m')}>0 AND {num('du_vlty_3m')}<=20 AND {num('pd_net_tamt')}>=10000000000"), "3개월 변동성과 순자산"),
      {"type":"answer_regex","name":"요청 외 상품 행 속성 제외","pattern":r"\A(?!.*\n  \d+\. [^\n]*(?:위험등급|종가|1년 변동성))"}],
 15: [names(bond(f"abs({num('SRFC_IRT')}-3)<0.000001 AND PD_PEN_TR_YN='Y'"), "정확 금리와 연금")],
 16: [names("SELECT DISTINCT COMPST_ISU_NM FROM etf_constituent WHERE COMPST_ISU_NM LIKE '%카카오뱅%'", "유사 종목 후보"), note("부분 일치 안내", "부분", "유사", "후보", "혹시", "정확")],
 17: [names(prefix_sql("LG"), "접두 편입 순자산 1위", top=1), names(prefix_sql("LG", "coalesce(m.resolved,e.cu_fund_mgmt_co)"), "1위 운용사", top=1), APPROX_NOTE],
 18: [names(prefix_sql("SK"), "접두 편입 순자산 1위", top=1), names(prefix_sql("SK", "e.drv_risk_grade||'등급'"), "1위 위험등급", top=1), APPROX_NOTE],
 19: [names(etf(f"pd_itm_no IN (SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM='삼성전자') AND {num('du_er_1y')}<>0", f"{num('du_er_1y')} DESC,pd_itm_no"), "편입 ETF 1년 상위 3", top=3, minimum=3, ordered=True),
      names(fund(f"prvo_pbff_desc='공모' AND {num('fd_yr1_ern_r')}<>0", f"{num('fd_yr1_ern_r')} DESC,itm_no"), "공모펀드 참고 상위 3", top=3, minimum=3, ordered=True),
      {"type":"answer_regex","name":"해외 1년 수익률 한계","pattern":r"해외.{0,180}(?:1년|연간).{0,180}(?:없|제외|미제공|미수집)"},
      {"type":"answer_regex","name":"펀드 보유종목 한계","pattern":r"펀드.{0,180}(?:보유|구성|편입).{0,180}(?:없|미수집|한계|불가|확인할 수)"}],
 20: [names(etf(f"(coalesce(cu_base_index,ref_base_index) ILIKE '%KOSPI%200%' OR pd_nm LIKE '%코스피200%' OR pd_abrv_nm LIKE '%코스피200%') AND {num('cu_charge_rt')}>0 AND {num('cu_charge_rt')}<=0.3 AND {num('pd_net_tamt')}>=100000000000"), "지수 보수 규모 교집합"), FEE_NOTE,
      {"type":"answer_regex","name":"이름 보수만 표시","pattern":r"\A(?!.*\n  \d+\. [^\n]*(?:위험등급|순자산총액|기초지수))"}],
 21: [names(etf(f"pd_dvid_pay_months ILIKE '%January%' AND {num('pd_dvid_pay_cnt')}=4 AND {num('pd_divd_amt_ann')}>=200"), "1월 연4회 연200원", minimum=3)],
 22: [names(bond(f"TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 2 AND 4 AND STD_PD_MCLS_NM='회사채' AND replace(MAT_DT,'-','') BETWEEN '20260906' AND '20290906' AND {num('SRFC_IRT')}>3 AND PD_PEN_TR_YN='Y'"), "등급 만기 금리 연금 교집합", minimum=3)],
 23: [names(global_etf(f"wu_inv_rgn='United States of America' AND wu_inv_ast_type='Equity' AND {num('cu_charge_rt')}>0 AND {num('cu_charge_rt')}<=0.1 AND {num('du_last_aum')} BETWEEN 1000000000 AND 10000000000", f"{num('du_last_aum')} DESC,pd_itm_no"), "해외 지역 유형 보수 규모 상위3", top=3, minimum=3, ordered=True)],
 24: [names(etf("pd_nm LIKE '%우주%' OR pd_nm LIKE '%항공%' OR coalesce(cu_base_index,ref_base_index) LIKE '%우주%'"), "현재 테마 후보"), note("과거 관계 이력 한계", "이력", "미수집", "스냅샷")],
}

# 원천에서 계산한 반대 조건도 확인해 맞는 이름 한 개의 우연한 포함을 줄인다.
CHECKS[13][0] = {"type":"any_of","name":"원천의 두 주식형 분류 중 명시한 기준",
                 "checks":[CHECKS[13][0], names(fund(f"ovrs_fd_desc='국내' AND or_attr_desc='주식형' AND prvo_pbff_desc='공모' AND {num('fd_yr1_ern_r')}<>0", f"{num('fd_yr1_ern_r')} DESC,itm_no"), "운용속성 주식형 상위", top=5, minimum=5, ordered=True)]}
CHECKS[13].append(note("선택한 분류 기준 명시", "유형 분류", "운용속성", "zrin_btyp_nm", "or_attr_desc"))
for i, count in ((6,12),(21,4)):
    others = con.execute("SELECT DISTINCT TRY_CAST(pd_dvid_pay_cnt AS INT) FROM kr_etp WHERE TRY_CAST(pd_dvid_pay_cnt AS INT)>0 AND TRY_CAST(pd_dvid_pay_cnt AS INT)<>?",[count]).fetchall()
    CHECKS[i].append({"type":"answer_has_none","name":"다른 지급횟수 혼입 금지",
                      "terms":[f"연간 분배 지급횟수 {row[0]}회" for row in others]})
private_top = con.execute(fund("prvo_pbff_desc='사모'",f"{num('fd_yr1_ern_r')} DESC NULLS LAST,itm_no")+" LIMIT 10").fetchall()
CHECKS[19].append({"type":"answer_has_none","name":"사모 수익률 상위 혼입 금지","terms":[name for row in private_top for name in row if name]})
all_sale = con.execute("SELECT count(*) FROM fund_master WHERE sale_yn='판매중'").fetchone()[0]
CHECKS[4].append({"type":"answer_has_none","name":"공모·사모 합산 건수 오답 금지","terms":[f"결과 {all_sale:,}건",f"결과 {all_sale}건"]})


def main():
    questions = [line.split(" | ") for line in (HERE / "blind_codex_2.txt").read_text(encoding="utf-8").splitlines()]
    assert Counter(q[0] for q in questions) == {"하":8,"중":8,"상":8,"답변불가":6}
    # 이름 숫자 조합의 비존재 근거를 원천 네 표에서 독립 확인한다.
    for table, col in (("kr_bond","PD_NM"),("kr_etp","pd_nm"),("global_etf","pd_nm"),("fund_master","itm_nm")):
        assert con.execute(f"SELECT count(*) FROM {table} WHERE {col} LIKE '%별빛AI로봇2031%'").fetchone()[0] == 0
    items, checks = [], []
    partial = {3,8,10,16,17,18,19,20,24}
    for i, (level, question, gold) in enumerate(questions,1):
        check_list = CHECKS.get(i,[{"type":"evidence_min","name":"거절 근거","n":1}])
        item = {"id":f"C2-{i:02}","level":"트랩" if i>24 else level,
                "category":"표현·조건 결합" if i<=24 else "답변 불가",
                "question":question,"channels":[],"behavior":"refuse" if i>24 else "partial" if i in partial else "answer",
                "gold":gold,"basis":"기대값은 엔진 없이 DuckDB SQL 직접 실행. 잔존만기 측정일: 2026-09-06."}
        evidence = []
        audit_checks = list(check_list)
        while audit_checks:
            check = audit_checks.pop(0)
            audit_checks.extend(check.get("checks",[]))
            sql = check.get("sql",check.get("basis_sql"))
            if sql:
                rows = con.execute(sql).fetchall()
                evidence.append({"검사":check["name"],"행수":len(rows),"처음3행":rows[:3]})
        item["basis"] += json.dumps(evidence,ensure_ascii=False)
        items.append(item)
        checks.append({"id":item["id"],"checks":check_list})
    for name, rows in (("evalset_codex_2.jsonl",items),("checks_codex_2.jsonl",checks)):
        (HERE/name).write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in rows),encoding="utf-8")
    print(f"원천 SQL로 {len(items)}문항 정답 계산 완료")


if __name__ == "__main__":
    main()
