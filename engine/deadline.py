# -*- coding: utf-8 -*-
"""
시간 예산 관리 — 요청 1건이 15초 목표를 넘지 않게 무거운 단계를 자동 강등 (순서 ⑥).

무엇: 요청 시작 시각을 기억했다가, 단계 진입 전에 "남은 시간으로 이 단계를 해도
      되는가"를 판정한다. 초과면 그 단계를 건너뛰고 가벼운 방법으로 대신한다.
      - 의미(벡터) 검색: 기준 초과 시 생략(키워드·SQL 결과로 답변)
      - HCX 문장 생성: 기준 초과 시 생략(규칙 요약으로 답변)
왜  : 응답 속도는 평가 요소(내부 목표 15초). 어느 단계가 늦어져도 전체 응답이
      죽지 않고 "덜 예쁘지만 유효한 답"으로 강등되는 것이 설계 원칙이다.
      강등된 응답은 캐시에 저장하지 않는다(서버가 판단).
"""
import time


class Deadline:
    """요청 1건의 시간 예산. cutoff 값들은 '요청 시작 후 경과 초' 기준.

    8/19 조정: 생성 진입 한계 8초 → 7초. 서버의 HCX 호출 상한이 계획 6초·생성 8초라
    최악(계획 6초 소진 → 생성 8초)에도 14초 안에 끝난다(첫 성적표 최대 15.83초 사례 방지).
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
