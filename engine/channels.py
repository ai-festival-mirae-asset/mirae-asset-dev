# -*- coding: utf-8 -*-
"""
채널 실행기 — RoutePlan 을 4채널(SQL·키워드·그래프·벡터)에 실행 (S2 순서 ③, 8/13).

무엇: ChannelCall 목록 → 채널별 실행 → ChannelOutcome(행·Evidence) 수집.
      실행은 전부 결정적 코드다 — LLM 은 플랜(id+파라미터)까지만 관여한다.
왜  : 환각 SQL·임의 검색 차단(ROADMAP §4.1). 벡터는 단독 한계(실측: "반도체
      집중"→로보틱스 1위)가 있어 테마 lexical anchor 목록과 RRF 로 결합한다.

LIKE 규약: 플랜의 *_raw 파라미터는 여기서 like_param() 이스케이프 후 실행 —
      Stage B(LLM 플랜)가 이스케이프를 놓치는 사고를 구조적으로 방지.
구조 주의: 테스트가 순수 함수(rrf_fuse 등)를 import 한다 — import 부작용 금지.
"""
import os
import csv
import io
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.keyword_channel import keyword_lookup             # noqa: E402
from engine.policy import load_policy                         # noqa: E402
from engine.sql_templates import like_param, like_prefix_param, run_template  # noqa: E402
from pipeline.evidence import (AS_OF_CONSTITUENTS, AS_OF_MASTER,  # noqa: E402
                               Evidence)
from pipeline.themes import detect_theme_terms, expand_anchors, load_themes  # noqa: E402

# 플랜에는 원문(*_raw)이 실리고 실행 직전 이스케이프된 정식 파라미터로 바뀐다
_RAW_LIKE_PARAMS = {"pattern_raw": "pattern", "attr_pattern_raw": "attr_pattern",
                    "region_pattern_raw": "region_pattern",
                    "exclude_region_pattern_raw": "exclude_region_pattern", "name_pattern_raw": "name_pattern"}
# 앞부분 일치(text%) — 그룹 계열사 후보(회사명 접두) 조회 (8/19)
_RAW_PREFIX_PARAMS = {"prefix_raw": "prefix"}


@dataclass
class ChannelOutcome:
    """채널 호출 1건의 결과 — 실패해도 예외 대신 ok=False 로 남긴다(전체 강등 방지)."""
    channel: str
    op: str
    ok: bool = True
    rows: list = field(default_factory=list)
    evidences: list = field(default_factory=list)
    note: str = ""
    error: str = ""


@dataclass
class RuntimeContext:
    """서버 기동 시 1회 준비하는 실행 자원 — 없는 채널은 해당 호출만 불가 처리."""
    con: object = None                # DuckDB 연결 (sql·벡터 lexical anchor)
    index: object = None              # EntityIndex (keyword)
    policy: dict = None
    kg_store: object = None           # kg_store.TripleStore (graph)
    vstore: object = None             # vector_store.VectorStore (vector)
    embedder: object = None           # callable(text) -> vector (vector)
    deadline: object = None           # engine.deadline.Deadline — 요청별 시간 예산(서버가 주입)

    def __post_init__(self):
        if self.policy is None:
            self.policy = load_policy()


def resolve_raw_params(params):
    """*_raw → like_param/like_prefix_param 이스케이프 적용된 정식 파라미터 dict."""
    out = {}
    for k, v in params.items():
        if k in _RAW_LIKE_PARAMS:
            out[_RAW_LIKE_PARAMS[k]] = like_param(v)
        elif k in _RAW_PREFIX_PARAMS:
            out[_RAW_PREFIX_PARAMS[k]] = like_prefix_param(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# RRF 결합 (순수 함수 — 회귀 테스트 대상)
# ---------------------------------------------------------------------------

def rrf_fuse(ranked_lists, k=60):
    """Reciprocal Rank Fusion — [[id,...], ...] → [(id, score)] 내림차순.

    벡터(의미)와 lexical anchor(키워드) 순위를 결합해 "의미는 비슷한데
    anchor 가 없는" 오답(로보틱스 케이스)을 anchor 보유 후보 아래로 내린다.
    """
    scores, first_seen = {}, {}
    for li, lst in enumerate(ranked_lists):
        for rank, item in enumerate(lst):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
            first_seen.setdefault(item, (li, rank))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))


# ---------------------------------------------------------------------------
# 채널별 실행
# ---------------------------------------------------------------------------

def _exec_sql(ctx, call):
    if ctx.con is None:
        return ChannelOutcome("sql", call.op, ok=False, error="DB 미탑재")
    result = run_template(ctx.con, call.op, resolve_raw_params(call.params))
    return ChannelOutcome("sql", call.op, rows=result.rows, evidences=result.evidences)


def _exec_keyword(ctx, call):
    if call.op == "fund_class_dictionary":
        path = os.path.join(ROOT, "external_data", "dictionaries", "fund_class.csv")
        if not os.path.exists(path):
            return ChannelOutcome("keyword", call.op, ok=False, error="펀드 클래스 사전 미탑재")
        wanted = {str(v).upper() for v in call.params.get("classes", [])}
        rows, evidences = [], []
        with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                key = str(row.get("키", "")).strip().upper()
                if not str(row.get("분류", "")).startswith("값사전|펀드클래스") or key not in wanted:
                    continue
                fields = {"class": key, "name": row.get("한글명"),
                          "meaning": row.get("의미"), "source": row.get("출처")}
                rows.append(fields)
                evidences.append(Evidence(source="fund_class.csv", source_id=key,
                                          channel="keyword", as_of=AS_OF_MASTER,
                                          fields=fields))
        return ChannelOutcome("keyword", call.op, rows=rows, evidences=evidences,
                              note="KOFIA 펀드 클래스 코드 사전")
    if ctx.index is None:
        return ChannelOutcome("keyword", call.op, ok=False, error="인덱스 미탑재")
    refs, evidences, exact = keyword_lookup(
        ctx.index, call.params.get("query", ""), limit=call.params.get("limit", 10))
    rows = [{"매칭": r.display, "종류": r.kind, "키": r.key, "직접일치": exact} for r in refs]
    note = "직접 매칭" if exact else "부분 일치(안내용 — 존재 근거 아님)"
    return ChannelOutcome("keyword", call.op, rows=rows, evidences=evidences, note=note)


def _exec_graph(ctx, call):
    if ctx.kg_store is None:
        return ChannelOutcome("graph", call.op, ok=False,
                              error="그래프 미탑재(서버 기동 시 프리로드 대상)")
    from kg.kg_store import FP, RDFS_LABEL
    from kg.query_kg import find_company_products, find_holding_etfs
    store, params = ctx.kg_store, call.params
    query, limit = params.get("query", ""), params.get("limit", 10)

    if call.op == "holding_etfs":
        found, coverage = find_holding_etfs(store, query, limit)
        rows, evidences = [], []
        for name, ticker, etfs in found:
            labels = [store.label(s) or s for s in etfs]
            rows.append({"종목": name, "코드": ticker, "편입ETF수": len(etfs), "ETF": labels})
            evidences.append(Evidence(
                source="KRX-PDF(KG)", source_id=ticker, channel="graph",
                as_of=AS_OF_CONSTITUENTS,
                fields={"종목": name, "편입 ETF 수": len(etfs),
                        "대표": " / ".join(labels[:5])},
                note=f"수집분 ETF {coverage:,}종목 기준 — 미수집 ETF 는 판단 불가"))
        return ChannelOutcome("graph", call.op, rows=rows, evidences=evidences)

    if call.op == "company_products":
        found = find_company_products(store, query, limit)
        rows, evidences = [], []
        for name, prop, products in found:
            labels = [store.label(s) or s for s in products]
            rel = "운용" if prop.endswith("managedBy") else "발행"
            rows.append({"회사": name, "관계": rel, "상품수": len(labels), "상품": labels})
            evidences.append(Evidence(
                source="KG", source_id=name, channel="graph", as_of=AS_OF_MASTER,
                fields={"회사": name, "관계": rel, "상품 수": len(labels),
                        "대표": " / ".join(labels[:5])}))
        return ChannelOutcome("graph", call.op, rows=rows, evidences=evidences)

    if call.op in ("product_info", "constituents_of"):
        hits = store.search_products(query, 1)
        if not hits:
            return ChannelOutcome("graph", call.op, rows=[],
                                  note=f"'{query}' 상품을 그래프에서 확인 불가")
        s, label = hits[0]
        if call.op == "product_info":
            fields = {"상품": label}
            mgmt = store.object(s, FP + "managedBy")
            if mgmt:
                fields["운용사"] = store.label(mgmt) or mgmt
            for ko, local in (("위험등급", "riskGrade"), ("총보수(%)", "expenseRatio"),
                              ("상장상태", "listingStatus")):
                v = store.object(s, FP + local)
                if v is not None:
                    if local == "expenseRatio" and str(v).strip() in ("0", "0.0"):
                        v = "0(원천 표기 — 무보수인지 미수집인지 미확정)"   # KODEX 200 도 0 으로 표기(8/19)
                    fields[ko] = v
            idx = store.object(s, FP + "tracksIndex") or store.object(s, FP + "hasBenchmark")
            if idx:
                fields["기초지수"] = store.label(idx) or idx
            ev = Evidence(source=store.object(s, FP + "sourceTable") or "KG",
                          source_id=store.object(s, FP + "productId") or s,
                          channel="graph", as_of=AS_OF_MASTER, fields=fields)
            return ChannelOutcome("graph", call.op, rows=[fields], evidences=[ev])
        consts = store.objects(s, FP + "holdsConstituent")
        labels = [store.label(c) or c for c in consts]
        rows = [{"상품": label, "구성종목수": len(labels), "구성": labels[:limit]}]
        ev = Evidence(source="KRX-PDF(KG)", source_id=label, channel="graph",
                      as_of=AS_OF_CONSTITUENTS,
                      fields={"상품": label, "구성종목 수": len(labels),
                              "대표": " / ".join(labels[:10])})
        return ChannelOutcome("graph", call.op, rows=rows, evidences=[ev])

    return ChannelOutcome("graph", call.op, ok=False, error=f"모르는 그래프 op: {call.op}")


def lexical_anchor_ids(con, anchors, per_term=40):
    """테마 anchor 들로 해외ETF 명칭·전략 서술을 ILIKE 검색 → 상품 id 순위 목록."""
    ids, names = [], {}
    sql = (r"""SELECT pd_itm_no, coalesce(pd_nm, pd_abrv_nm) FROM global_etf
               WHERE pd_nm ILIKE $p ESCAPE '\' OR pd_abrv_nm ILIKE $p ESCAPE '\'
                  OR cu_strtegy ILIKE $p ESCAPE '\'
               ORDER BY pd_itm_no LIMIT """ + str(int(per_term)))
    for term in anchors[:4]:
        for pid, name in con.execute(sql, {"p": like_param(term)}).fetchall():
            if pid not in names:
                ids.append(pid)
                names[pid] = name
    return ids, names


def _exec_vector(ctx, call):
    """벡터 채널 — 쿼리 임베딩 top-k 와 lexical anchor 목록의 RRF 결합.

    임베더가 없으면(오프라인) lexical 만으로 동작하고 그 사실을 note 에 남긴다.
    시간 예산(deadline)을 넘긴 요청은 의미 검색을 생략(강등)한다 — 15초 방어.
    """
    if ctx.deadline is not None and ctx.deadline.over(ctx.deadline.vector_cutoff):
        return ChannelOutcome("vector", call.op, ok=False,
                              error="시간 예산 초과로 의미 검색 생략(강등)")
    query, k = call.params.get("query", ""), call.params.get("k", 8)
    themes = load_themes()
    anchors = expand_anchors(detect_theme_terms(query, themes), themes)

    vec_ids, vec_names, vec_scores = [], {}, {}
    if ctx.vstore is not None and ctx.embedder is not None:
        vec, _tokens = ctx.embedder(query)
        for _sha, score, products in ctx.vstore.search(vec, k):
            for p in products:
                pid = p.get("pd_itm_no")
                if pid and pid not in vec_names:
                    vec_ids.append(pid)
                    vec_names[pid] = p.get("pd_nm", pid)
                    vec_scores[pid] = score

    lex_ids, lex_names = ([], {})
    if ctx.con is not None and anchors:
        lex_ids, lex_names = lexical_anchor_ids(ctx.con, anchors)

    if not vec_ids and not lex_ids:
        return ChannelOutcome("vector", call.op, ok=False,
                              error="벡터/anchor 모두 불가(임베더·DB 미탑재 또는 anchor 없음)")

    fused = rrf_fuse([vec_ids, lex_ids]) if (vec_ids and lex_ids) else \
        [(i, 0.0) for i in (vec_ids or lex_ids)]
    # 테마 anchor가 실제로 검색된 경우에는 의미 유사도만 높은 상품을 결과에서
    # 제외한다. 기간 표현 같은 주변 단어가 비관련 ETF를 끌어올리는 것을 막고,
    # 벡터는 anchor 보유 후보의 재정렬에만 사용한다.
    if anchors and lex_ids:
        fused = [(pid, score) for pid, score in fused if pid in lex_names]
    mode = ("RRF(벡터+lexical)" if vec_ids and lex_ids else
            "벡터 단독" if vec_ids else "lexical anchor 단독(임베더 미탑재)")

    rows, evidences = [], []
    for pid, _score in fused[:k]:
        name = vec_names.get(pid) or lex_names.get(pid, pid)
        in_vec, in_lex = pid in vec_names, pid in lex_names
        rows.append({"pd_itm_no": pid, "pd_nm": name,
                     "vector": in_vec, "anchor": in_lex})
        basis = "+".join([b for b, on in (("의미검색", in_vec), ("명칭/서술 일치", in_lex)) if on])
        note = f"{mode} · 근거: {basis}"
        if in_vec and not in_lex and anchors:
            note += " · 주의: 테마 anchor 미일치(의미 유사만)"
        fields = {"상품명": name}
        if pid in vec_scores:
            fields["유사도"] = round(vec_scores[pid], 3)
        if anchors:
            fields["anchor"] = ", ".join(anchors[:3])
        evidences.append(Evidence(source="PREF02N001·cu_strtegy", source_id=str(pid),
                                  channel="vector", as_of=AS_OF_MASTER,
                                  fields=fields, note=note))
    return ChannelOutcome("vector", call.op, rows=rows, evidences=evidences, note=mode)


_EXECUTORS = {"sql": _exec_sql, "keyword": _exec_keyword,
              "graph": _exec_graph, "vector": _exec_vector}


@dataclass
class ExecutionResult:
    outcomes: list = field(default_factory=list)

    @property
    def evidences(self):
        return [e for o in self.outcomes for e in o.evidences]

    @property
    def errors(self):
        return [(o.channel, o.op, o.error) for o in self.outcomes if not o.ok]


def execute_plan(plan, ctx):
    """플랜의 채널 호출을 순차 실행 — 개별 실패는 outcome 으로 격리(전체 불사).

    순차인 이유: 평가 호출은 순차 확정 + 채널 수 2~4개 · ms 단위 쿼리라 병렬화
    이득이 없고, DuckDB 커넥션 공유가 단순해진다(데드라인 강등은 ⑥에서).
    """
    result = ExecutionResult()
    for call in plan.calls:
        executor = _EXECUTORS.get(call.channel)
        if executor is None:
            result.outcomes.append(ChannelOutcome(call.channel, call.op, ok=False,
                                                  error=f"모르는 채널: {call.channel}"))
            continue
        try:
            result.outcomes.append(executor(ctx, call))
        except Exception as exc:                          # 채널 실패 격리
            result.outcomes.append(ChannelOutcome(call.channel, call.op, ok=False,
                                                  error=f"{type(exc).__name__}: {exc}"))
    return result
