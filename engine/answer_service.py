# -*- coding: utf-8 -*-
"""
답변 조립기 — 질문 1건의 전 과정: 라우팅 → 채널 조회 → 검증 → 생성 → 5필드 JSON.

무엇: API 서버(순서 ⑥)가 그대로 호출할 진입점 answer_question(). 흐름:
      ① 라우터(순서 ③)가 조회 계획을 세우고 ② 4채널을 실행하고
      ③ 5중 검문(순서 ④)이 답변 태도(answer/partial/refuse)를 확정하고
      ④ 생성기(순서 ⑤)가 있으면 HCX-005 로 문장을 다듬고(사후 대조 포함)
      ⑤ 공식 규격(5필드 전부 문자열)으로 직렬화한다.
왜  : 거절은 템플릿 문구만(함정 경로에서 AI 발화 0), 생성 실패는 규칙 요약으로
      폴백 — 어떤 경우에도 유효한 5필드 응답이 나온다.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.channels import execute_plan                      # noqa: E402
from engine.generator import echo_equivalent, post_check_answer  # noqa: E402
from engine.router import RATING_RANK, route                  # noqa: E402
from engine.validation import validate_answerability          # noqa: E402
from pipeline.evidence import (AS_OF_CONSTITUENTS, AS_OF_MASTER,  # noqa: E402
                               AS_OF_MASTER_GL, Evidence, to_context_string)

# 행 요약에서 이름으로 쓸 컬럼 우선순위 (테이블별 상이 — 먼저 발견되는 것 사용)
_NAME_COLS = ("pd_abrv_nm", "pd_nm", "PD_NM", "itm_nm", "etf_name", "COMPST_ISU_NM",
              "mgmt_co", "종목", "회사", "상품", "상품명", "매칭")
_SKIP_COLS = {"pd_itm_no", "PD_NO", "itm_no", "etf_isin", "COMPST_ISU_CD", "코드", "키"}


def _fmt_value(v):
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _fmt_row(row, max_fields=4, focus=()):
    """행 1개 → '이름 (필드=값 · …)' — 테이블 무관 요약. 비중·수익률 열(*_pct)은 % 를 붙인다.

    focus 열(질문이 콕 집은 속성 — 8/28 블라인드(claude) B-15: '상위 3개의 분배율 비교'에서
    분배율 열이 표시 상한에 잘려 값이 답변에 안 실리던 것)은 열 순서와 무관하게 앞에 두고,
    표시 상한을 focus 개수만큼 늘려 잘리지 않게 한다.
    """
    name = next((str(row[c]) for c in _NAME_COLS if row.get(c)), None)
    parts = []
    focus_present = [k for k in focus if k in row]
    ordered = focus_present + [k for k in row.keys() if k not in focus_present]
    for k in ordered:
        v = row[k]
        if k in _SKIP_COLS or v is None or (name is not None and str(v) == name):
            continue
        if (str(k) + "_krw") in row:                      # 원 단위 원값 대신 환산 표기(…억원)만 보여 준다
            continue
        if isinstance(v, list):
            v = " / ".join(str(x) for x in v[:5]) + (" 외" if len(v) > 5 else "")
        unit = "%" if str(k).endswith("_pct") and isinstance(v, (int, float)) else ""
        parts.append(f"{k}={_fmt_value(v)}{unit}")
        if len(parts) >= max_fields + len(focus_present):
            break
    body = " · ".join(parts)
    return f"{name} ({body})" if name and body else (name or body or str(row))


def _focus_cols(question):
    """질문 낱말이 콕 집은 속성 열 이름들(_ATTR_NOTES 사전 재사용 — 순수 함수)."""
    cols = []
    for rx, col, _label, _fmt in _ATTR_NOTES:
        if re.search(rx, question or "") and col not in cols:
            cols.append(col)
    return cols


def _sort_rows_by_aum(rows):
    def aum(row):
        v = row.get("pd_net_tamt")
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return -1.0
    return sorted(rows, key=aum, reverse=True)


REFUSE_HEAD = "요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"
_FREE_REFUSAL_RE = re.compile(
    r"확인할 수 없|확인하지 못|확인되지 않|확인(해|하여)\s*드릴 수 없|찾을 수 없|찾지 못|"
    r"제공(하지|할 수|받지) (못|없)|"
    r"답변(을|이)?\s*(드릴 수 없|할 수 없|찾을 수 없|드리기 어렵)|정보(가|는)?\s*(없|포함되어 있지)|"
    r"데이터(를|가)?\s*(제공받지|없)|죄송|알 수 없습니다")   # '확인해 드릴 수 없/드리기 어렵'은 8/28 V3-T-09 표현 변형
_NUMBERED_LINE_RE = re.compile(r"^\s*\d{1,3}[.)]\s*\S", re.M)


def _looks_like_free_refusal(text):
    """정해진 거절문이 아닌 '자기 말 거절'인가 — 앞머리 160자에 거절 표현이 있고 목록이 없을 때.

    채점기(와 주최 채점)는 정해진 거절문으로 시작하는 답만 거절로 본다(8/22 v2 실측 —
    "죄송합니다. 조건에 맞는 항목을 확인하지 못했습니다"는 '답변'으로 읽혀 함정 오답).
    """
    head = (text or "").strip()[:160]
    if not head or head.startswith(REFUSE_HEAD):
        return False
    if len(_NUMBERED_LINE_RE.findall(text or "")) >= 2 or re.search(r"결과\s*\d+\s*건", text or ""):
        return False                                  # 목록이 있는 답은 거절이 아니다
    return bool(_FREE_REFUSAL_RE.search(head))


def _draft_refusal(plan, result, verdict):
    """사유 기반 템플릿 거절문 — HCX 자유 생성 없음(확정 설계).

    사유는 검증(5중 검문)의 판정을 우선 쓰고, 라우터가 남긴 해석 노트를 보탠다.
    유사 이름 안내는 '부분 일치일 뿐 존재 근거가 아님'을 문면에 명시한다.
    """
    reasons = list(dict.fromkeys(verdict.reasons))   # 같은 사유를 두 검문이 낸 경우 한 줄로(8/27 router_rule 게이트)
    for n in plan.notes:
        if n not in reasons and "부분 일치" not in n:
            reasons.append(n)
    if not reasons:
        reasons = ["요청 내용을 보유 데이터에서 확인할 수 없습니다"]
    lines = ["요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다."]
    lines += [f"- 사유: {r}" for r in reasons]

    suggestions = list(verdict.suggestions)
    # 키워드 채널의 부분 일치도 안내에 합류하되, '상품' 종류이면서 질의어가 원문 표기
    # 그대로 이름 안에 보이는 것만 — 'kimi' ⊂ 'Denmark IMI'(공백 제거 우연 겹침)처럼
    # 검문소가 이미 "의미 없는 겹침"으로 판정한 이름을 안내로 되살리지 않는다(8/18 실측).
    kw_queries = [str(c.params.get("query", "")) for c in plan.calls if c.channel == "keyword"]
    for o in result.outcomes:
        if o.channel == "keyword":
            for r in o.rows:
                name = str(r["매칭"])
                is_product = str(r.get("종류", "")).startswith("product")
                visible = any(q and q.lower() in name.lower() for q in kw_queries)
                if (not r.get("직접일치") and is_product and visible
                        and name not in suggestions):
                    suggestions.append(name)
    if suggestions:
        lines.append("- 혹시 다음 상품을 찾으셨나요(명칭 부분 일치 안내이며, "
                     "질의하신 대상의 존재 근거는 아닙니다): " + " / ".join(suggestions[:3]))
    lines.append(f"(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})")
    return "\n".join(lines)


def _draft_rating_compare(plan):
    pairs = plan.hints.get("rating_compare") or []
    if len(pairs) < 2:
        return None
    (t1, r1), (t2, r2) = pairs[0], pairs[1]
    hi, lo = ((t1, r1), (t2, r2)) if r1 < r2 else ((t2, r2), (t1, r1))
    return (f"{hi[0]} 등급이 {lo[0]} 등급보다 높습니다. 신용등급 서열(AAA=1 최상 ~ D=20)에서 "
            f"{hi[0]}는 서열 {hi[1]}, {lo[0]}는 서열 {lo[1]}입니다. "
            f"(근거: 신용등급 서열 사전 — 신용평가 3사 공식 등급체계)")


# 구성종목 조회 템플릿 — 0건이면 "조건 불일치"가 아니라 "구성 공시 없음(빈 응답·미수집)"이 맞는 표현
_CONSTITUENT_OPS = {"etp_pattern_top_constituents", "constituent_top_weights"}


def _draft_answer(plan, result, question=""):
    """규칙 기반 요약 답변 — 생성기가 없거나 실패했을 때의 폴백(항상 동작)."""
    if plan.intent == "unstructured_info":
        lines = ["요청하신 상품의 구조·투자전략·동향을 설명할 비정형 자료는 "
                 "현재 보유 데이터에서 확인할 수 없습니다."]
        # 마스터에 있는 사실(운용속성·위험등급·수익률·순자산·판매상태·벤치마크)은 답한다 (M-10)
        for o in result.outcomes:
            if o.ok and o.channel == "sql" and o.op == "fund_detail" and o.rows:
                lines.append("확인된 상품의 마스터 정보:")
                lines += [f"  - {_fmt_row(r, max_fields=8)}" for r in o.rows[:3]]
        if plan.notes:
            lines.append("")
            lines += [f"※ {n}" for n in plan.notes]
        lines.append(f"(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})")
        return "\n".join(lines)

    lines = []
    for o in result.outcomes:
        if not o.ok:
            continue
        if not o.rows:
            if o.channel == "sql" and o.op in _CONSTITUENT_OPS:
                lines.append(f"[{o.op}] 구성 공시 없음 — 해당 상품의 {AS_OF_CONSTITUENTS} KRX 구성종목 공시가 "
                             "비어 있거나 미수집이라 구성종목을 확인할 수 없습니다")
            elif o.channel == "sql":
                lines.append(f"[{o.op}] 조건 일치 결과 0건")
            continue
        rows = o.rows
        if o.channel == "sql":
            if plan.hints.get("order") == "aum" and rows and "pd_net_tamt" in rows[0]:
                rows = _sort_rows_by_aum(rows)
            head = f"[{o.op}] 결과 {len(rows):,}건"
            display_rows = int(plan.hints.get("display_rows", 5))
            focus = _focus_cols(question)                 # 질문이 콕 집은 속성 열은 잘리지 않게(B-15)
            body = [f"  {i}. {_fmt_row(r, focus=focus)}" for i, r in enumerate(rows[:display_rows], 1)]
            lines.append("\n".join([head] + body))
        elif o.channel == "graph":
            for r in rows[:3]:
                if "편입ETF수" in r:
                    etfs = r.get("ETF") or []
                    lines.append(f"'{r['종목']}'({r['코드']})을(를) 편입한 ETF {r['편입ETF수']:,}종 — "
                                 f"대표: {' / '.join(etfs[:5])}")
                elif "상품수" in r:
                    lines.append(f"{r['회사']}이(가) {r['관계']}하는 상품 {r['상품수']:,}종 — "
                                 f"대표: {' / '.join((r.get('상품') or [])[:5])}")
                else:
                    lines.append(_fmt_row(r))
        elif o.channel == "vector":
            names = [str(r.get("pd_nm")) for r in rows[:5]]
            lines.append(f"[의미·키워드 결합 검색] 상위: " + " / ".join(names) + f" ({o.note})")
        elif o.channel == "keyword":
            if o.op == "fund_class_dictionary":
                lines += [f"{r['class']}형({r['name']}): {r['meaning']}" for r in rows]
                continue
            exact = list(dict.fromkeys(r["매칭"] for r in rows if r.get("직접일치")))
            partial = list(dict.fromkeys(r["매칭"] for r in rows if not r.get("직접일치")))
            if exact:
                lines.append("명칭 직접 일치: " + " / ".join(exact[:5]))
            if partial:
                lines.append("유사 명칭 안내(부분 일치 — 존재 근거 아님): " + " / ".join(partial[:5]))
    if not lines:
        lines.append("조건에 일치하는 결과를 보유 데이터에서 확인하지 못했습니다.")
    if plan.notes:
        lines.append("")
        lines += [f"※ {n}" for n in plan.notes]
    lines.append(f"(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})")
    return "\n".join(lines)


# 분포형 템플릿 — (첫 열 이름, 우리말 라벨). 한 갈래뿐이면 "전부 X — 다른 것 없음"을 명시한다 (L-20/L-30)
_DIST_OPS = {"global_ccy_dist": ("pd_trd_ccy", "거래통화"), "etp_currency_dist": ("drv_curr_cd", "거래통화"),
             "bond_currency_dist": ("CURR_CD", "통화"), "bond_class_dist": ("STD_PD_MCLS_NM", "대분류")}


def dist_sentence(op, rows):
    """분포 결과 → 결론 문장. 순수 함수(테스트 대상)."""
    col, label = _DIST_OPS[op]
    buckets = [(str(r.get(col)), int(r.get("n") or 0)) for r in rows if r.get(col) is not None]
    total = sum(n for _v, n in buckets)
    if not buckets or total == 0:
        return None
    if len(buckets) == 1:
        return f"{label}: 전부 {buckets[0][0]}({total:,}건) — 다른 {label} 없음"
    parts = ", ".join(f"{v} {n:,}건({n / total * 100:.1f}%)" for v, n in buckets[:5])
    more = f" 외 {len(buckets) - 5}종" if len(buckets) > 5 else ""
    return f"{label} 분포: {parts}{more} — 총 {total:,}건, 이 밖의 {label} 없음"


# 건수 템플릿 — 숫자 한 개짜리 결과는 생성기가 "정보 없음"으로 오독하기 쉬워(L-05 실측) 문장으로 승격한다
_COUNT_OPS = {"bond_count", "etp_count", "global_etf_count", "fund_counts", "fund_class_count",
              "fund_missing_bmrk"}
_COUNT_LABELS = {"n": "건수", "products": "상품(마스터) 수", "share_classes": "판매 클래스 수",
                 "on_sale_products": "판매 중 상품(마스터) 수", "on_sale_classes": "판매 중 클래스 수"}
# 상품 1종 상세에서 질문이 콕 집은 항목은 노트로 강제한다(8/22 블라인드 v2 L-04~10 — 생성기가
# 상세 행에서 그 칸을 못 찾거나 빼먹던 실측). (질문 낱말, 열, 라벨, 형식)
_DETAIL_OPS = {"etp_detail", "bond_detail", "fund_detail"}
_ATTR_NOTES = [
    (r"상장|거래\s*가능|언제", "pd_lstg_dt", "상장일(원천 항목명: 상품거래가능일자)", "date"),
    (r"만기", "MAT_DT", "만기일", "date"),
    (r"발행일|발행", "ISU_DT", "발행일", "date"),
    (r"신용\s*등급|등급", "drv_crd_grd_norm", "신용등급(대표)", "text"),
    (r"위험\s*등급|위험", "PD_RISK_NM", "상품위험등급명", "text"),
    (r"표면\s*금리|금리|쿠폰|이자", "SRFC_IRT", "표면금리(%)", "text"),
    (r"위험\s*등급|위험", "drv_risk_grade", "위험등급(1=매우 높음~6=매우 낮음)", "risk"),
    (r"순자산|규모", "fd_nast_suma", "순자산", "krw"),
    (r"순자산|규모", "pd_net_tamt", "순자산총액", "krw"),
    (r"기초\s*지수|추종", "cu_base_index", "기초지수", "text"),
    (r"보수", "cu_charge_rt", "총보수(%)", "text"),
    (r"수익률", "fd_yr1_ern_r", "1년 수익률(%)", "text"),
    (r"수익률", "du_er_1y", "1년 수익률(%)", "text"),
    (r"ETF야|ETN이야|유형|종류", "drv_instrument_type", "상품 유형", "text"),
    # 8/27 재배포본 신설 분배·품질 필드 (구본은 전부 0/결측이라 거절하던 항목)
    (r"배당\s*수익률|분배\s*수익률|배당|분배", "pd_dvid_yield", "분배(배당)수익률(%)", "text"),
    (r"배당금|분배금", "pd_divd_amt_ann", "연간 추정 분배금(원)", "text"),
    (r"지급\s*횟수|몇\s*번|배당|분배", "pd_dvid_pay_cnt", "연간 분배 지급횟수", "text"),
    (r"지급\s*월|지급일|배당|분배", "pd_dvid_pay_months", "분배 지급월", "text"),
    (r"추적\s*오차", "du_chas_errt", "추적오차율(%)", "text"),
    (r"괴리", "du_diff_rt", "괴리율(%)", "text"),
    (r"변동성", "du_vlty_1y", "1년 변동성(%)", "text"),
    (r"분배율", "fd_last_dstb_r", "최근 분배율(%)", "text"),
    (r"자산\s*구성|주식\s*비중|편입\s*비율", "zrin_dmst_stk_cmst_rt", "국내주식 구성비율(%)", "text"),
    # 8/28 r2 — 펀드 보수 분해 4종(재배포본 신설)·거래량·세후수익률
    (r"세후\s*수익률", "AFTER_TAX_YIELD", "세후수익률(%)", "text"),
    (r"보수", "sale_co_rwrd_r", "판매회사 보수(%)", "text"),
    (r"보수", "or_co_rwrd_r", "운용회사 보수(%)", "text"),
    (r"보수", "trusc_rwrd_r", "수탁회사 보수(%)", "text"),
    (r"보수", "ofwk_trus_rwrd_r", "사무관리 보수(%)", "text"),
    (r"거래량", "du_vol_1d", "1일 거래량", "text"),
    (r"퇴직연금|연금", "PD_PEN_TR_YN", "퇴직연금 편입 가능 여부", "text"),
]


def _fmt_attr(row, col, fmt):
    v = row.get(col)
    if v in (None, ""):
        return None
    s = str(v).strip()
    if fmt == "date":
        d = re.sub(r"\D", "", s)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else s
    if fmt == "risk":
        name = row.get("zrin_fd_ivst_risk_grd_nm")
        return f"{s}등급" + (f"({name})" if name else "")
    if fmt == "krw":
        return row.get(col + "_krw") or s
    return s


def attribute_notes(question, op, rows):
    """상세 조회 행 + 질문 낱말 → '만기일: 2026-11-03' 같은 사실 노트(순수 함수)."""
    if op not in _DETAIL_OPS or not rows:
        return []
    row, out = rows[0], []
    name = next((str(row[c]) for c in _NAME_COLS if row.get(c)), None)
    for rx, col, label, fmt in _ATTR_NOTES:
        if col in row and re.search(rx, question):
            val = _fmt_attr(row, col, fmt)
            if val:
                # 상품 이름을 노트에 함께 박는다 — 생성기가 문장을 줄이며 이름을 빼먹어도
                # (8/28 r2 R2-16: 'KODEX 인버스 거래량'에 값만 남긴 실측) 답에 이름이 남는다.
                out.append(f"'{name}' {label}: {val}" if name else f"{label}: {val}")
    return list(dict.fromkeys(out))
# 목록 1위 상품의 속성 명시 (8/26 v3 C-05/C-10) — "…중 순자산 1위 상품의 위험등급/상장일/운용사"
# 처럼 정렬 목록의 최상위 행에서 속성을 되묻는 3단 질문의 마지막 고리. 정렬은 라우터가
# 이미 SQL 로 해 두므로 여기서는 첫 목록의 첫 행 값을 사실 노트로 밝히기만 한다.
_RANK_WORD_RE = re.compile(r"1\s*위|가장|제일|최고|최대|상위")   # 상위: 8/28 r3 R3-16
_TOP_ATTRS = [
    (r"운용사|어느 운용|누가 운용", "mgmt", "운용사(복구값 기준)", "text"),
    (r"위험\s*등급", "drv_risk_grade", "위험등급(1=매우 높음~6=매우 낮음)", "risk"),
    (r"상장일|언제 상장|상장됐", "pd_lstg_dt", "상장일", "date"),
    (r"기초\s*지수|추종", "cu_base_index", "기초지수", "text"),
    (r"보수", "cu_charge_rt", "총보수(%)", "text"),
    (r"배당\s*수익률|분배\s*수익률", "pd_dvid_yield", "분배(배당)수익률(%)", "text"),
    (r"추적\s*오차", "du_chas_errt", "추적오차율(%)", "text"),
    (r"수익률", "du_er_1y", "1년 수익률(%)", "text"),
]
_TOP_OPS = {"constituent_holders", "etp_by_mgmt", "etp_top_aum", "constituent_intersection_top_aum",
            "constituent_prefix_holders_by_aum", "fund_filter", "etp_top_return",
            "etp_by_dividend", "constituent_holders_top_return"}


def top_rank_attribute_notes(question, result):
    """정렬 목록형 결과 + 순위 낱말 + 속성 낱말 → '목록 1위 X의 속성: 값' 노트. 순수 함수."""
    if not _RANK_WORD_RE.search(question):
        return []
    for o in result.outcomes:
        if not (o.ok and o.channel == "sql" and o.op in _TOP_OPS and o.rows):
            continue
        row = o.rows[0]
        name = next((str(row[c]) for c in _NAME_COLS if row.get(c)), None)
        out = []
        for rx, col, label, fmt in _TOP_ATTRS:
            if col in row and re.search(rx, question):
                val = _fmt_attr(row, col, fmt)
                if val:
                    prefix = f"목록 1위 '{name}'" if name else "목록 1위"
                    out.append(f"{prefix} — {label}: {val}")
        return out                                        # 첫 목록만 본다(뒤 호출은 보조 근거)
    return []


# 상품 '구조·전략·운용 동향' 질의(공식 예시 중-1 유형)에 구조화 필드로 요약을 조립한다 (8/27 5차).
# 서술형 공시 자료는 제공 데이터에 없으므로(주최 데이터 실측), 검증 가능한 필드 값으로 답하고
# 한계를 명시하는 것이 트랩 정의("확인할 수 없는 질의에 답변 생성 시 감점")에 맞는 안전한 형태다.
_STRUCTURE_ASK_RE = re.compile(r"구조|전략|운용\s*(동향|방식|스타일)|어떤\s*상품|특징|소개")


def structure_summary_notes(question, op, rows):
    """etp_detail·fund_detail 행 → '구조·전략 요약(구조화 필드 기반)' 노트. 순수 함수."""
    if op not in ("etp_detail", "fund_detail") or not rows or not _STRUCTURE_ASK_RE.search(question):
        return []
    row = rows[0]
    parts = []
    if op == "etp_detail":
        if row.get("drv_instrument_type"):
            parts.append(f"유형 {row['drv_instrument_type']}")
        strat = row.get("cu_strtegy")
        if strat and str(strat).strip() not in ("", "C"):   # 국내 값은 분류(실물복제·합성복제·액티브), 'C'는 의미 미확인 코드
            parts.append(f"복제·운용 방식 {strat}")
        idx = row.get("cu_base_index") or row.get("ref_base_index")
        if idx:
            parts.append(f"기초지수 {idx}")
        if row.get("ref_geo_focus"):
            parts.append(f"투자지역 {row['ref_geo_focus']}")
        if row.get("drv_risk_grade"):
            parts.append(f"위험등급 {row['drv_risk_grade']}(1=매우 높음~6=매우 낮음)")
        fee = row.get("cu_charge_rt")
        if fee not in (None, "") and str(fee) != "0":
            parts.append(f"총보수 {fee}%")
        if row.get("pd_dvid_pay_cnt"):
            months = row.get("pd_dvid_pay_months")
            parts.append(f"분배 연 {row['pd_dvid_pay_cnt']}회" + (f"({months})" if months else ""))
        aum = row.get("pd_net_tamt_krw") or row.get("pd_net_tamt")
        if aum:
            parts.append(f"순자산 {aum}")
    else:
        if row.get("zrin_btyp_nm"):
            parts.append(f"펀드 유형 {row['zrin_btyp_nm']}")
        comp = [(lbl, row.get(c)) for lbl, c in (("국내주식", "zrin_dmst_stk_cmst_rt"),
                                                 ("해외주식", "zrin_ovrs_stk_cmst_rt"),
                                                 ("국내채권", "zrin_dmst_bd_cmst_rt"),
                                                 ("해외채권", "zrin_ovrs_bd_cmst_rt"))
                if row.get(c) not in (None, "", "0")]
        if comp:
            parts.append("자산구성 " + " · ".join(f"{l} {v}%" for l, v in comp))
        if row.get("bmrk_nm"):
            parts.append(f"벤치마크 {row['bmrk_nm']}")
        if row.get("drv_risk_grade"):
            parts.append(f"위험등급 {row['drv_risk_grade']}")
        if row.get("fd_last_dstb_r") not in (None, "", "0"):
            parts.append(f"최근 분배율 {row['fd_last_dstb_r']}%")
        if row.get("share_class_count"):
            parts.append(f"판매 클래스 {row['share_class_count']}개")
    if not parts:
        return []
    return ["구조·전략 요약(구조화 필드 기반): " + ", ".join(parts),
            "투자설명서 등 서술형 공시 자료는 제공 데이터에 없어 위 요약은 마스터 필드 값으로 구성한 것"]


# 구성종목에 선물·옵션이 보이면 파생 위험을 데이터 사실로 명시한다 (H-21·M-20)
_DERIV_SECUGRP = {"FU": "선물", "OP": "옵션"}


def count_sentence(op, rows):
    """건수 결과 → '조건 일치 건수: N건' 문장(열이 여럿이면 라벨별로). 순수 함수."""
    if not rows:
        return None
    parts = []
    for row in rows[:6]:
        label_cols = [k for k, v in row.items() if isinstance(v, str)]
        prefix = " ".join(str(row[k]) for k in label_cols[:2])
        nums = [f"{_COUNT_LABELS.get(k, k)} {int(v):,}건" for k, v in row.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            parts.append((prefix + ": " if prefix else "") + " · ".join(nums))
    if not parts:
        return None
    return "조건 일치 건수 — " + " / ".join(parts)


def data_notes(question, plan, result):
    """조회 결과에서 도출한 사실 노트 목록 — 분포 결론·건수·요청 필드 결측·파생 구성 명시."""
    notes = []
    for o in result.outcomes:
        if not o.ok or o.channel != "sql":
            continue
        if o.op in _DIST_OPS:
            s = dist_sentence(o.op, o.rows)
            if s:
                notes.append(s)
        if o.op in _COUNT_OPS:
            s = count_sentence(o.op, o.rows)
            if s:
                notes.append(s)
        if o.op in _CONSTITUENT_OPS and o.rows:
            kinds = {_DERIV_SECUGRP[r.get("SECUGRP_ID")] for r in o.rows if r.get("SECUGRP_ID") in _DERIV_SECUGRP}
            if kinds:
                notes.append(f"구성종목에 {'·'.join(sorted(kinds))}(파생상품)이 포함됨 — 레버리지·인버스형은 지수 선물로 "
                             "배수를 만들므로 기초지수 변동의 배수로 손익이 움직이는 파생 위험이 있음(위험등급은 상품 행 참조)")
        notes.extend(attribute_notes(question, o.op, o.rows))        # 질문이 집은 항목 값(8/22)
        notes.extend(structure_summary_notes(question, o.op, o.rows))  # 8/27 5차: 구조·전략 요약(구조화 필드)
        if o.op == "etp_detail" and o.rows and re.search(r"지수|추종|벤치마크|따라가", question):
            idx = o.rows[0].get("cu_base_index")
            ref_idx = o.rows[0].get("ref_base_index")
            if idx:
                notes.append(f"기초지수(cu_base_index): {idx}")
            elif ref_idx:
                notes.append(f"기초지수(Refinitiv 참조 ref_base_index): {ref_idx} — 원천 기초지수 컬럼은 결측")
            else:
                notes.append("기초지수 값이 이 상품 행에 없음(cu_base_index·ref_base_index 모두 결측) — "
                             "추종 지수는 제공 데이터로 확인할 수 없음")
    # 8/27 v1 H-30 재발(HCX 표현 흔들림): 두 상품 상세를 나란히 조회한 비교 질문에서 생성기가
    # "두 ETF"라고만 쓰고 상품명을 빼는 변동 실측 — 비교 대상 이름을 노트로 강제해 어느 실행에서도
    # 답변에 두 이름이 남게 한다(_ensure_notes 가 빠지면 붙인다). 모든 두-상품 비교에 유효한 일반 정책.
    if re.search(r"비교|더\s|중에|어느\s*(쪽|게|것)|랑\s|와\s|과\s", question):
        detail_rows = []
        for o in result.outcomes:
            if o.ok and o.channel == "sql" and o.op in _DETAIL_OPS and o.rows:
                nm = next((str(o.rows[0][c]) for c in _NAME_COLS if o.rows[0].get(c)), None)
                if nm and nm not in [n for n, _r in detail_rows]:
                    detail_rows.append((nm, o.rows[0]))
        if len(detail_rows) >= 2:
            notes.append("비교 대상: " + " vs ".join(n for n, _r in detail_rows[:3]))
            # 8/28 r2: 순자산 비교 결론을 규칙이 직접 낸다 — HCX 없이(빠른 판)도, HCX 문장
            # 흔들림에도 "어느 쪽이 더 큰가"의 답이 항상 남는다(값·환산 표기 동반).
            if re.search(r"순자산|규모|AUM", question, re.IGNORECASE) and re.search(r"더|어느", question):
                vals = []
                for nm, row in detail_rows[:2]:
                    try:
                        vals.append((nm, float(str(row.get("pd_net_tamt")).replace(",", "")),
                                     row.get("pd_net_tamt_krw")))
                    except (TypeError, ValueError):
                        vals = []
                        break
                if len(vals) == 2 and vals[0][1] != vals[1][1]:
                    big, small = (vals[0], vals[1]) if vals[0][1] > vals[1][1] else (vals[1], vals[0])
                    notes.append(f"순자산총액은 '{big[0]}'({big[2] or f'{big[1]:,.0f}원'})가 "
                                 f"'{small[0]}'({small[2] or f'{small[1]:,.0f}원'})보다 더 큽니다")
    # 8/28 실측(M-11·L-19 계열): 테마 검색의 생성 답변이 상품 목록을 통째로 생략하는 일이
    # 있다 — 검색 상위 이름을 노트로 강제해 어떤 실행에서도 이름이 답에 남게 한다(일반 정책).
    if plan.intent == "theme_search":
        _tnames = []
        for o in result.outcomes:
            if o.ok and o.rows:
                for row in o.rows:
                    nm = next((str(row[c]) for c in _NAME_COLS if row.get(c)), None)
                    if nm and nm not in _tnames:
                        _tnames.append(nm)
            if len(_tnames) >= 5:
                break
        if _tnames:
            notes.append("검색 상위 상품: " + " / ".join(_tnames[:5]))
    notes.extend(top_rank_attribute_notes(question, result))     # v3 C-05/C-10: 1위 상품의 속성
    # v2 H-08/O-03: 운용사·테마 필터가 걸린 편입 ETF 조회가 0건이면 '없다'를 명시(거절이 아니라 사실 답변)
    mf = plan.hints.get("mgmt_filter")
    nf = plan.hints.get("holder_name_filter")
    if mf or nf:
        holder_outs = [o for o in result.outcomes
                       if o.ok and o.channel == "sql" and o.op == "constituent_holders"]
        if holder_outs and not any(o.rows for o in holder_outs):
            what = f"'{mf['name']}' 운용 상품" if mf else f"상품명에 '{nf}' 표기가 있는 편입 상품"
            notes.append(f"{what}은 확인되지 않습니다(조건 일치 0건)")
    return notes


def _ensure_notes(text, plan):
    """생성 답변에 해석·한계 노트와 기준일이 빠졌으면 강제로 붙인다(채점 필수 요소)."""
    for n in plan.notes:
        if n not in text:
            text += f"\n※ {n}"
    # '기준일'이라는 말과 실제 날짜가 둘 다 있어야 한다 — 생성기가 "데이터 기준일: 현재"(날짜 없음,
    # M-08·M-30)라거나 "2026-08-22"만 덜렁 쓰는(라벨 없음, L-21) 경우 모두 정식 기준일 줄을 붙인다.
    # 기준일 3원화(8/26 재배포본): 국내 마스터 8/22 · 해외 8/23 · 구성종목 수집분 7/10.
    has_label = "기준일" in text
    has_date = AS_OF_MASTER in text or AS_OF_MASTER_GL in text or AS_OF_CONSTITUENTS in text
    if not (has_label and has_date):
        text += (f"\n(데이터 기준일: 국내 마스터 {AS_OF_MASTER} · 해외 {AS_OF_MASTER_GL}"
                 f" · 구성종목 {AS_OF_CONSTITUENTS})")
    return text


def _think_trace(plan, result, verdict, gen_note="", intent_line=""):
    lines = [f"stage={plan.stage} intent={plan.intent} behavior={verdict.behavior}"
             f"(라우터 힌트 {plan.behavior_hint})"]
    if intent_line:                                   # 8/26 공지 준수 — HCX 의도 분석 기록
        lines.append(intent_line)
    if plan.entities:
        ents = "; ".join(f"{n}→{refs[0].kind}:{refs[0].key}" for n, refs in plan.entities[:6])
        lines.append(f"grounded: {ents}")
    if plan.unknown_terms:
        lines.append(f"미등록 토큰: {', '.join(plan.unknown_terms)}")
    for call in plan.calls:
        lines.append(f"call {call.channel}.{call.op} {call.params}")
    for ch, op, err in result.errors:
        lines.append(f"오류 {ch}.{op}: {err}")
    for g in verdict.gates:
        detail = f" — {g.reason}" if g.reason else ""
        lines.append(f"검문[{g.gate}] {g.verdict}{detail}")
    if gen_note:
        lines.append(f"생성: {gen_note}")
    for n in plan.notes:
        lines.append(f"note: {n}")
    return "\n".join(lines)


def serialize_answer(question_id, question, evidences, think_trace, answer):
    """공식 응답 규격 — 5필드 전부 문자열, 빈 값 없이. API 서버(⑥)가 재사용."""
    return {
        "question_id": str(question_id or ""),
        "question": str(question or ""),
        "retrieved_context": to_context_string(evidences) or "(근거 없음)",
        "think_trace": str(think_trace or "(기록 없음)"),
        "answer": str(answer or "답변 생성에 실패했습니다. 다시 시도해 주세요."),
    }


def answer_question(question, ctx, question_id="", today=None,
                    llm_router=None, generator=None, deadline=None,
                    intent_checker=None, finalizer=None):
    """질문 1건 → 5필드(string) dict — E2E 진입점.

    llm_router: 복잡한 질문의 조회 계획을 HCX 로 세우는 콜러블(없으면 규칙+폴백만).
    generator : 최종 문장을 HCX 로 다듬는 콜러블(없으면 규칙 요약) — 실패 시 자동 폴백.
    deadline  : 시간 예산(engine.deadline.Deadline) — 초과 시 생성 단계를 생략(강등).
    intent_checker/finalizer : HCX 필수 구간 준수 콜러블(8/26 공지 — 의도 분석·답변 생성).
      의도 분석은 모든 질의에서 HCX 가 수행해 trace 에 남기고(판정은 규칙·검증 우선),
      비생성 경로 답안은 HCX 가 '그대로' 최종 출력한다(내용 불일치 시 확정 답안 유지).
    검증은 라우터 판정을 신뢰하지 않고 질문 원문에서 독립 재검사한다(이중 방어).
    """
    plan = route(question, ctx.index, policy=ctx.policy, today=today, llm_router=llm_router)
    intent_line = ""
    if intent_checker is not None:
        label = intent_checker(question)
        if label:
            intent_line = (f"HCX 의도 분석: {label} — 파이프라인 판정 intent={plan.intent}"
                           "(판정 상충 시 규칙·검증 우선)")
        else:
            intent_line = "HCX 의도 분석 호출 실패 — 규칙 라우팅 판정으로 진행"
    result = execute_plan(plan, ctx)
    verdict = validate_answerability(question, plan, result, ctx.index, ctx.policy)

    # 커버리지 수치는 생성 모델이 요약 과정에서 빼먹기 쉽다. 실제 조회된
    # 분자·분모를 노트로 승격해 생성 답변과 규칙 답변 모두에 강제로 남긴다.
    for outcome in result.outcomes:
        if outcome.channel == "sql" and outcome.op == "coverage_check":
            for row in outcome.rows:
                coverage_note = (f"{row['field']} 값 보유 {row['non_null']:,}/{row['total']:,}건"
                                 f"({row['coverage_pct']}%) 기준")
                if coverage_note not in plan.notes:
                    plan.notes.append(coverage_note)
    # 조회 결과에서만 알 수 있는 사실도 노트로 승격한다(8/19 ⑧): 분포 답변의 "전부/없음" 결론,
    # 상세 답변에서 물어본 필드(기초지수)가 결측인 사실 — 생성기가 흐리게 쓰거나 빼먹기 쉬운 것들.
    for note in data_notes(question, plan, result):
        if note not in plan.notes:
            plan.notes.append(note)
    # HCX 라우터(Stage B)가 세운 계획이 0건이면 "없다"가 아니라 "이 조건 해석으로는 못 찾음"이다 —
    # 계획의 파라미터가 질문과 어긋났을 수 있어(M-08·H-26 유형) 단정을 막는 노트를 강제한다(8/19).
    if plan.stage in ("llm", "llm_repair") and verdict.behavior != "refuse" \
            and not any(o.ok and o.rows for o in result.outcomes):
        zero_note = ("조회 계획(HCX 라우터)이 세운 조건으로는 일치하는 항목을 찾지 못함 — 질문 조건의 해석이 "
                     "다를 수 있어 '해당 상품이 없다'고 단정하지 않음(조건을 바꿔 다시 물으면 확인 가능)")
        if zero_note not in plan.notes:
            plan.notes.append(zero_note)

    evidences = list(result.evidences) + list(verdict.evidences)
    # 근거 0개 방지망(8/22 H-17 실측): HCX 계획이 0건으로 끝나면 근거 블록 없이 답이 나가
    # 채점 근거 축을 잃는다. 어떤 경로든 근거가 비면 "무엇을 어떤 조회로 찾아봤는지"를
    # validation 근거로 남긴다 — 0건·실패도 확인 과정의 근거다(거절 경로와 같은 원칙).
    if not evidences:
        for outcome in result.outcomes[:5]:
            n_rows = len(getattr(outcome, "rows", None) or [])
            evidences.append(Evidence(
                source="조회 기록", source_id=f"{outcome.channel}.{outcome.op}",
                channel="validation", as_of=AS_OF_MASTER,
                fields={"실행": f"{outcome.channel}.{outcome.op}",
                        "결과": (f"{n_rows}건" if outcome.ok else f"실패({(outcome.error or '')[:80]})")}))
        if not evidences:
            evidences.append(Evidence(source="조회 기록", source_id="실행 없음",
                                      channel="validation", as_of=AS_OF_MASTER,
                                      fields={"실행": "조회 없음", "결과": "검증 판정만으로 답변"}))
    gen_note = ""
    if generator is not None and deadline is not None and deadline.over(deadline.generation_cutoff):
        generator = None
        gen_note = f"시간 예산 초과({deadline.elapsed():.1f}s) — HCX 생성 생략, 규칙 요약으로 강등"

    answer_from_hcx = False                          # 8/27: HCX 필수 구간 준수 추적(최종화 판단용)
    if verdict.behavior == "refuse":
        answer = _draft_refusal(plan, result, verdict)
    elif plan.intent == "rating_compare":            # 사전 근거 답변 — 생성 불필요(결정적)
        # 다른 경로와 같이 해석 노트·기준일을 붙인다(8/18 채점기가 '답변에 기준일 없음'을 잡아냄)
        answer = _ensure_notes(_draft_rating_compare(plan) or _draft_answer(plan, result, question), plan)
        evidences.append(Evidence(source="credit_rating.csv", source_id="서열사전",
                                  channel="keyword", as_of=AS_OF_MASTER,
                                  fields={k: RATING_RANK[k] for k, _r in
                                          (plan.hints.get("rating_compare") or [])[:2]}))
    else:
        if verdict.behavior == "partial":            # 한계 문구를 노트에 합류(생성 전에)
            for r in verdict.reasons:
                if r not in plan.notes:
                    plan.notes.append(r)
        answer = None
        if generator is not None and not plan.hints.get("skip_generation"):
            raw = generator(question, plan, result, verdict)
            if raw:
                checked, removed = post_check_answer(
                    raw, evidences, question, index=ctx.index,
                    extra_allowed=" ".join(plan.notes))
                if checked is not None:
                    answer = _ensure_notes(checked, plan)
                    answer_from_hcx = True
                    fixes = [r for _s, r in removed if r.startswith("표기 정정")]
                    dropped = [(s, r) for s, r in removed if not r.startswith("표기 정정")]
                    parts = ["HCX-005 생성"]
                    if fixes:                            # 이름 오기를 근거 표기로 되돌린 기록(8/19)
                        parts.append(f"이름 {len(fixes)}건 정정({'; '.join(fixes[:2])})")
                    if not dropped:
                        parts.append("사후 대조 통과")
                    else:                                # 무엇을 왜 지웠는지 남긴다(과잉 삭제 진단용, 8/19)
                        why = "; ".join(f"'{s[:28]}…'({r})" for s, r in dropped[:3])
                        parts.append(f"사후 대조로 {len(dropped)}줄 제거: {why}")
                    gen_note = " · ".join(parts)
                else:
                    gen_note = "생성 답변 전체가 근거 대조 실패 — 규칙 요약으로 강등"
            else:
                gen_note = "생성 호출 실패 — 규칙 요약으로 폴백"
        if answer is None:
            answer = _draft_answer(plan, result, question)
        # 거절 문장 통일(8/22 블라인드 v2 T-03·05·13·14 실측): 생성기·규칙 요약이 자기 말로
        # 거절하면("찾을 수 없었습니다"·"죄송합니다"류) 채점상 거절이 아니다. 조회 결과가 있으면
        # 규칙 요약(목록)으로, 0건이면 정해진 거절문으로 바꾼다 — 거절은 한 문장으로만 시작한다.
        # partial(한계 명시 답변)은 '확인할 수 없음' 문구가 정상이므로 건드리지 않는다.
        if verdict.behavior == "answer" and _looks_like_free_refusal(answer):
            has_rows = any(o.ok and o.rows for o in result.outcomes)
            if has_rows:
                draft = _draft_answer(plan, result, question)
                if not _looks_like_free_refusal(draft):
                    answer = draft
                    answer_from_hcx = False
                    gen_note = (gen_note + " · " if gen_note else "") + "생성기가 자유 문장으로 거절 → 조회 결과가 있어 규칙 요약으로 교체"
            else:
                # 8/27 v1 L-06 실측(영구채 소멸): 규칙 라우팅(해석 확실) + 미등록 이름 없음 + SQL 0건은
                # '없다'가 사실 답변이다 — 거절문으로 바꾸면 과잉 거절. 규칙 요약("결과 0건")이
                # 거절 문장이 아니면 그것으로 교체하고, 존재 의심 경로(미등록 토큰·HCX 계획)만
                # 기존대로 정형 거절문으로 통일한다.
                draft = _draft_answer(plan, result, question)
                suspicious = bool(plan.unknown_terms) or plan.stage != "rule"
                if not suspicious and draft.strip() and not _looks_like_free_refusal(draft):
                    answer = draft
                    answer_from_hcx = False
                    gen_note = (gen_note + " · " if gen_note else "") + \
                        "생성기가 자유 문장으로 거절 → 규칙 조회 0건 = '없음' 사실 답변(규칙 요약)으로 교체"
                else:
                    answer = _draft_refusal(plan, result, verdict)
                    answer_from_hcx = False
                    gen_note = (gen_note + " · " if gen_note else "") + "조회 0건 + 자유 문장 거절 → 정해진 거절문으로 통일"

    # 8/26 공지 준수 — '답변 생성' 단계: 비생성 경로(확정 답안·거절문·사전 답변 등)로 만들어진
    # 답도 HCX 가 최종 출력한다. 확정 답안을 '그대로' 출력하게 하고, 내용이 달라지면(공백 무시
    # 비교) 확정 답안을 유지해 결정성·채점 안전성을 지킨다. 시간 예산 초과 시에는 생성기와
    # 같은 기준으로 생략하고 사유를 남긴다.
    if finalizer is not None and answer and not answer_from_hcx:
        if deadline is not None and deadline.over(deadline.generation_cutoff):
            gen_note = (gen_note + " · " if gen_note else "") + \
                f"시간 예산 초과({deadline.elapsed():.1f}s) — HCX 최종화 생략, 확정 답안 그대로 응답"
        else:
            final = finalizer(question, answer)
            if final and echo_equivalent(final, answer):
                answer = final
                gen_note = (gen_note + " · " if gen_note else "") + "최종 문장 출력: HCX-005(확정 답안 그대로)"
            elif final:
                gen_note = (gen_note + " · " if gen_note else "") + "HCX 최종화 출력이 확정 답안과 달라 확정 답안 유지"
            else:
                gen_note = (gen_note + " · " if gen_note else "") + "HCX 최종화 호출 실패 — 확정 답안 유지"

    return serialize_answer(question_id, question, evidences,
                            _think_trace(plan, result, verdict, gen_note, intent_line), answer)
