# -*- coding: utf-8 -*-
"""
시간 예산 관리 — 요청 1건이 15초(무감점 경계) 안에 끝나게 무거운 단계를 자동 강등 (순서 ⑥).

무엇: 요청 시작 시각을 기억했다가, 단계 진입 전에 "남은 시간으로 이 단계를 해도
      되는가"를 판정한다. 초과면 그 단계를 건너뛰고 가벼운 방법으로 대신한다.
      - 의미(벡터) 검색: 기준 초과 시 생략(키워드·SQL 결과로 답변)
      - HCX 문장 생성: 기준 초과 시 생략(규칙 요약으로 답변)
왜  : 어느 단계가 늦어져도 전체 응답이 죽지 않고 "덜 예쁘지만 유효한 답"으로
      강등되는 것이 설계 원칙이다. 강등된 응답은 캐시에 저장하지 않는다(서버가 판단).

시간 기준(8/22 확정): 설명회 발화(팀 리더 참석 확인) — **15초 이하 응답은 감점 없음,
초과하면 감점(방식 미공개)**. 문서 규정은 권장 60초·타임아웃 300초+재시도 2회.
→ 내부 목표 15초 유지가 정답(같은 날 오전 '정확도 우선 60초'로 올렸다가 이 확인으로
당일 복원). 규칙 요약 강등은 정답을 유지하므로(8/19 실측 105/105) 15초가 우선이다.
"""
import time


class Deadline:
    """요청 1건의 시간 예산. cutoff 값들은 '요청 시작 후 경과 초' 기준.

    사다리(8/19 검증 — 105/105·최대 9.24초·15초 초과 0건): 서버의 HCX 호출 상한이
    계획 6초·생성 8초라, 생성 진입 한계 7초면 최악(7초 진입 → 생성 8초)에도 15초 안.
    """

    def __init__(self, total=15.0, vector_cutoff=6.0, generation_cutoff=7.0):
        self.t0 = time.monotonic()
        self.total = total
        self.vector_cutoff = vector_cutoff
        self.generation_cutoff = generation_cutoff

    def elapsed(self):
        return time.monotonic() - self.t0

    def over(self, cutoff):
        return self.elapsed() > cutoff
