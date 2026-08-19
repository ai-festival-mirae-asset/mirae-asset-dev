# -*- coding: utf-8 -*-
"""
Evidence 계약 — 4채널(SQL·그래프·벡터·키워드) 공통 근거 규격 (S2 순서 ①, 8/13).

무엇: 채널이 반환하는 "근거 1건"의 고정 형태와, retrieved_context 문자열(응답
      5필드는 전부 string — 공식 규격)로의 직렬화.
왜  : ① 근거 표시(출처·기준일)는 필수 규칙(ROADMAP §3) — 채널마다 형식이 다르면
      Answer Validation·직렬화가 채널별 분기 지옥이 된다. ② 기준일이 소스별로
      다르다(마스터 7/11 · 구성종목 7/10) — 근거 단위로 as_of 를 들고 다녀야
      "언제 기준의 사실인가"를 잃지 않는다.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
from dataclasses import dataclass, field

AS_OF_MASTER = "2026-07-11"        # 제공 마스터 4종 스냅샷
AS_OF_CONSTITUENTS = "2026-07-10"  # KRX 구성종목 조회일(직전 거래일)

# 채널 식별자 — Router 플랜·think_trace 로그와 공유하는 어휘
CHANNELS = ("sql", "graph", "vector", "keyword", "validation")


@dataclass(frozen=True)
class Evidence:
    """근거 1건 — 답변의 모든 사실은 이 목록 안에서만 나와야 한다(생성 후 대조 기준).

    source   : 출처 테이블·자산 식별자 (예: 'PREF01N001', 'KRX-PDF', 'cu_strtegy-vec')
    source_id: 행 키 (예: pd_itm_no 'KR7102110004')
    channel  : CHANNELS 중 하나
    as_of    : 이 근거의 기준일 (마스터 7/11 · 구성종목 7/10 등 소스별로 다름)
    fields   : {컬럼/속성: 값} — 답변에 인용할 사실들 (원문 lexical 보존)
    note     : 사람이 읽는 부가 설명 (커버리지 한계 등, 선택)
    """
    source: str
    source_id: str
    channel: str
    as_of: str
    fields: dict = field(default_factory=dict)
    note: str = ""

    def __post_init__(self):
        if self.channel not in CHANNELS:
            raise ValueError(f"알 수 없는 채널: {self.channel!r} (허용: {CHANNELS})")


def to_context_string(evidences):
    """Evidence 목록 → retrieved_context 문자열 (복수 문서는 구분 태그 — 태그는 평가
    대상이 아님이 확정돼 있어 형식 자유, 사람도 읽을 수 있는 표기를 쓴다)."""
    blocks = []
    for i, e in enumerate(evidences, 1):
        facts = " · ".join(f"{k}={v}" for k, v in e.fields.items())
        note = f" | {e.note}" if e.note else ""
        blocks.append(f"[근거{i} | 출처: {e.source} | 키: {e.source_id} | "
                      f"채널: {e.channel} | 기준일: {e.as_of}] {facts}{note}")
    return "\n".join(blocks)
