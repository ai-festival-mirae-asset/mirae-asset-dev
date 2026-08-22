# -*- coding: utf-8 -*-
"""
통합 엔티티 인덱스 — 상품·구성종목·운용사·지수·별칭을 한 사전으로 (S2 순서 ②).

무엇: DuckDB 에서 전 개체 명칭을 읽어 {정규화 명칭 → [EntityRef]} 인덱스를 만든다.
왜  : Router 의 grounding(질의 속 개체 → 데이터 키)과 Answer Validation 의
      존재 검증(트랩 방어 — "직접 매칭만, 간접 연상 금지")이 **같은 인덱스**를
      쓰도록 — 두 계층이 서로 다른 사전을 보면 "라우팅은 됐는데 검증이 거부"
      같은 자기모순이 생긴다.

검색 의미론 2종 (트랩 방어 정책 — evalset/SEED_QUESTIONS.md):
  exact(query)  : 정규화 완전 일치만 — 존재 검증용. 부분 일치는 존재로 인정 안 함.
  search(query) : 부분 일치 포함 — 검색·"유사 상품 안내"용 (안내까지만, 답변 아님).

정규화: 공백 제거 + casefold (kg_store.norm_name 과 동일 규칙 — 채널 간 일관).
운용사: 오염 복구값(mgmt_resolved) 기준. 구성종목: 이름 변형 전부 + 한글 별칭 병합.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import os
import re
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))            # pipeline/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.constituent_aliases import load_aliases  # noqa: E402
from pipeline.query_aliases import product_alias_variants  # noqa: E402

DB_PATH_DEFAULT = os.path.join(ROOT, "storage", "output", "products.duckdb")

# 개체 종류 — Router 플랜·검증 사유 코드와 공유하는 어휘
KINDS = ("product_kr_etp", "product_global_etf", "product_bond", "product_fund",
         "constituent", "company", "index")


def norm_name(text):
    """명칭 정규화 — 공백 제거 + casefold (kg_store 와 동일 규칙)."""
    return re.sub(r"\s+", "", str(text)).casefold()


def _norm_with_map(text):
    """norm_name 과 같은 정규화 문자열 + 각 정규화 글자의 원문 위치(경계 검사용)."""
    out, omap = [], []
    for i, ch in enumerate(str(text)):
        if ch.isspace():
            continue
        f = ch.casefold()
        out.append(f)
        omap.extend([i] * len(f))
    return "".join(out), omap


# 이름 바로 뒤에 한글이 이어질 때 '이름이 끝났다'고 볼 수 있는 낱말들 — 조사·질문 낱말·
# 금융 문맥 명사. 이 밖의 한글이 이어지면 다른 이름의 일부로 본다("애플파이", "KB스타골드").
_FOLLOW_OK = (
    "이", "가", "을", "를", "은", "는", "의", "도", "로", "과", "와", "에", "만", "랑", "처럼", "부터",
    "까지", "에서", "한테", "보다", "께", "나", "든", "및", "등", "이라", "이란", "이면", "이랑",
    "주식", "종목", "회사", "기업", "상품", "펀드", "채권", "지수", "관련", "테마", "편입", "담은",
    "담고", "담는", "담긴", "포함", "보유", "운용", "투자", "수익", "순자산", "가격", "주가", "정보",
    "상세", "구성", "비중", "만기", "신용", "위험", "등급", "상장", "보수", "배당", "발행", "잔존",
    "표면", "거래", "여부", "개수", "목록", "알려", "찾아", "보여", "비교", "정리", "추천", "있어",
    "있나", "있는", "뭐", "어디", "언제", "몇", "얼마", "어때", "인지", "이야", "이고", "인가",
    "하고", "중", "그리고", "또는", "대비", "대신", "말고", "빼고", "제외", "기준", "현재", "지금",
    "최근", "총", "평균", "상위", "하위", "가장", "제일", "순", "선주", "우선주", "보통주", "같은",
    "쪽", "것", "거", "인", "두", "외", "혹은", "말", "하나만", "통합", "합성",
)


def _boundary_ok(text, omap, start, end):
    """정규화 문자열 [start, end) 매칭이 원문에서 '이름 하나'로 끝나는가."""
    s, e = omap[start], omap[end - 1]
    if s > 0 and text[s - 1].isascii() and text[s - 1].isalnum():
        return False                                  # 'OKBSTAR' 속 'KBSTAR' — 다른 토큰의 일부
    if e + 1 >= len(text):
        return True
    nxt = text[e + 1]
    if nxt.isascii():
        return not nxt.isalnum()                      # 영문·숫자가 이어지면 다른 이름("코스피300")
    if not ("가" <= nxt <= "힣"):
        return True                                   # 기호·공백 등 — 경계
    run = re.match(r"[가-힣]+", text[e + 1:]).group(0)
    return any(run.startswith(w) for w in _FOLLOW_OK)


@dataclass(frozen=True)
class EntityRef:
    """개체 참조 1건 — kind(종류)·key(데이터 키)·display(대표 표기)·source(출처 테이블)."""
    kind: str
    key: str          # 상품: pd_itm_no/PD_NO/itm_no · 구성종목: 코드/ISIN · 회사/지수: 명칭
    display: str
    source: str       # 근거 표시용 (PREF01N001, KRX-PDF 등)


class EntityIndex:
    """{정규화 명칭 → [EntityRef]} — exact/search 2종 조회."""

    def __init__(self):
        self._by_name = {}          # norm → [EntityRef] (입력 순서 보존, 중복 제거)
        self.entries = 0

    def add(self, name, ref):
        if not name or not str(name).strip():
            return
        bucket = self._by_name.setdefault(norm_name(name), [])
        if ref not in bucket:
            bucket.append(ref)
            self.entries += 1

    # -- 조회 ---------------------------------------------------------------

    def exact(self, query):
        """정규화 완전 일치 — 존재 검증용. 없으면 빈 목록."""
        return list(self._by_name.get(norm_name(query), []))

    def search(self, query, limit=10, kinds=None):
        """부분 일치 검색 — 안내·후보 제시용. (name, EntityRef) 목록."""
        q = norm_name(query)
        if not q:
            return []
        out = []
        for name, refs in self._by_name.items():
            if q in name:
                for ref in refs:
                    if kinds is None or ref.kind in kinds:
                        out.append((name, ref))
                        if len(out) >= limit:
                            return out
        return out

    def scan(self, text, min_len=2):
        """질문 텍스트 안에 통째로 등장하는 등록 명칭 전수 탐색 (Router grounding 용).

        의미론은 exact 와 같다(등록 명칭이 정규화 텍스트에 완전한 형태로 포함) —
        부분 일치가 아니므로 존재 근거로 쓸 수 있다. 겹치는 매칭은 긴 이름이
        이긴다("삼성전자우선주" 안의 '삼성전자우' > '삼성전자' > '삼성').
        반환: [(name, [EntityRef])] — 원문 등장 위치 순.

        이름 경계(8/22 블라인드 v2 실측): 공백을 지운 문자열에서 찾기 때문에
        "TIGER 코스피300" 안의 'tiger코스피', "애플파이" 안의 '애플', 펀드명
        "KB스타골드…" 안의 별칭 'kb스타'가 정확 일치로 잡혔다(함정 오답·엉뚱한 상품).
        원문에서 이름 바로 뒤에 영문·숫자가 이어지면 다른 이름이고, 한글이 이어지면
        조사·질문 낱말(을/를/주식/담은/우선주…)일 때만 경계로 인정한다.
        """
        q, omap = _norm_with_map(text)
        if not q:
            return []
        occs = []
        for name, refs in self._by_name.items():
            if len(name) < min_len:
                continue
            pos = q.find(name)
            while pos >= 0:
                if _boundary_ok(text, omap, pos, pos + len(name)):
                    occs.append((pos, len(name), name, refs))
                    break
                pos = q.find(name, pos + 1)        # 경계가 안 맞으면 다음 등장 위치를 본다
        occs.sort(key=lambda t: (-t[1], t[0]))      # 긴 이름 우선 채택
        taken, spans = [], []
        for pos, ln, name, refs in occs:
            if any(not (pos + ln <= s or pos >= e) for s, e in spans):
                continue                             # 이미 채택된 더 긴 매칭과 겹침
            spans.append((pos, pos + ln))
            taken.append((pos, name, refs))
        taken.sort()
        return [(name, list(refs)) for _pos, name, refs in taken]


def token_matches(index, token, limit=3):
    """토큰이 이름 안에 '의미 있게' 등장하는 검색 결과 — 우연 겹침 제거판.

    공백 제거 정규화 검색은 'kimi' ⊂ 'Denmark IMI'(→denmarkimi) 같은 우연
    겹침을 만든다. 원문 표기(공백 보존)에 토큰이 연속으로 등장하거나 이름
    전체가 토큰과 일치할 때만 인정한다. 결과는 안내용 — 존재 근거 아님.
    """
    t = norm_name(token)
    if not t:
        return []
    out = []
    for name, ref in index.search(t, limit=max(limit * 5, 15)):
        if name == t or t in ref.display.casefold():
            out.append((name, ref))
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------------------
# 인덱스 빌드 (DuckDB → EntityIndex)
# ---------------------------------------------------------------------------

_PRODUCT_SOURCES = [
    # (kind, 테이블, 키 컬럼, 이름 컬럼들, 출처 표기)
    ("product_kr_etp",     "kr_etp",     "pd_itm_no", ("pd_nm", "pd_abrv_nm"), "PREF01N001"),
    ("product_global_etf", "global_etf", "pd_itm_no", ("pd_nm", "pd_abrv_nm"), "PREF02N001"),
    ("product_bond",       "kr_bond",    "PD_NO",     ("PD_NM", "PD_ABRV_NM"), "PRBD01N001"),
    ("product_fund",       "fund_master", "itm_no",   ("itm_nm", "itm_abrv_nm"), "PRFD01N001"),
]


def build_entity_index(con):
    """DuckDB 연결 → EntityIndex. 서버 기동 시 1회 빌드해 재사용한다."""
    idx = EntityIndex()

    # ① 상품 4종 — 정식명·약칭 모두 등록. 국내 ETP는 검증된 브랜드의
    #    한글/영문·구 브랜드 표기도 같은 실제 상품 키를 가리키도록 색인한다.
    for kind, table, key_col, name_cols, source in _PRODUCT_SOURCES:
        cols = ", ".join((key_col,) + name_cols)
        for row in con.execute(f"SELECT {cols} FROM {table}").fetchall():
            key, names = row[0], row[1:]
            display = next((n for n in names if n), None)
            for n in names:
                if n:
                    idx.add(n, EntityRef(kind, key, display or n, source))
                    if kind == "product_kr_etp":
                        for alias_name in product_alias_variants(n):
                            idx.add(alias_name, EntityRef(kind, key, display or n, source))

    # ② 구성종목 — 이름 변형 전부(운용사별 표기 상이), 키는 코드/ISIN.
    #    현금성 센티널(CASH·KRD/KRZ/KRY 코드, 현금·예금 명칭)은 종목이 아니다 —
    #    kg/build_kg.py 의 차단 규칙과 동일 정책(변경 시 양쪽 동기).
    for name, code in con.execute(r"""
            SELECT DISTINCT COMPST_ISU_NM, COMPST_ISU_CD FROM etf_constituent
            WHERE COMPST_ISU_NM IS NOT NULL AND COMPST_ISU_CD IS NOT NULL
              AND NOT starts_with(COMPST_ISU_CD, 'CASH')
              AND NOT regexp_matches(COMPST_ISU_CD, '^KR[DZY]')
              AND NOT regexp_matches(COMPST_ISU_NM, '현금|예금|설정현금액')""").fetchall():
        idx.add(name, EntityRef("constituent", code, name, "KRX-PDF"))

    # ③ 구성종목 한글 별칭 — "캠브리콘" → ISIN (복수 상장은 복수 참조)
    for alias_norm, pairs in load_aliases().items():
        for isin, canonical in pairs:
            idx.add(alias_norm, EntityRef("constituent", isin, canonical or alias_norm,
                                          "constituent_aliases"))

    # ④ 운용사 — 오염 복구값 기준(mgmt_resolved) + 해외ETF 운용사 원시값
    for (name,) in con.execute("""
            SELECT DISTINCT resolved FROM mgmt_resolved
            WHERE resolved IS NOT NULL""").fetchall():
        idx.add(name, EntityRef("company", name, name, "PREF01N001(복구)"))
    global_cos = [name for (name,) in con.execute("""
            SELECT DISTINCT cu_fund_mgmt_co FROM global_etf
            WHERE cu_fund_mgmt_co IS NOT NULL""").fetchall()]
    for name in global_cos:
        idx.add(name, EntityRef("company", name, name, "PREF02N001"))
    # ④-2 운용사 별칭(8/22 v2 실측 — "신한자산운용"은 그래프에만 별칭이 있고 라우터엔 없었다):
    #     정식 운용사명(별칭 사전 국내ETF브랜드)·해외 운용사 한글명 → 같은 company 키
    try:
        from kg.build_kg import company_alias_map
        domestic_raws = [name for (name,) in con.execute(
            "SELECT DISTINCT resolved FROM mgmt_resolved WHERE resolved IS NOT NULL").fetchall()]
        for raw, alts in company_alias_map(domestic_raws, "domestic").items():
            for alt in alts:
                idx.add(alt, EntityRef("company", raw, raw, "alias_dictionary"))
        for raw, alts in company_alias_map(global_cos, "foreign").items():
            for alt in alts:
                idx.add(alt, EntityRef("company", raw, raw, "alias_dictionary"))
    except Exception:
        pass                                          # 사전이 없어도 색인은 성립(원시 표기만)

    # ⑤ 지수·벤치마크 — 원시 표기(정규화 사전 승격은 후속)
    for table, col, source in (("kr_etp", "cu_base_index", "PREF01N001"),
                               ("global_etf", "cu_base_index", "PREF02N001"),
                               ("fund_master", "bmrk_nm", "PRFD01N001")):
        for (name,) in con.execute(
                f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
            idx.add(name, EntityRef("index", name, name, source))

    return idx
