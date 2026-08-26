# -*- coding: utf-8 -*-
"""계열사 수집기(collect_group_affiliates)의 XML 해석 — 오프라인 순수 함수 시험(8/26).

실제 호출은 사용자 인증키(data.go.kr) 발급 후에만 가능하므로, 여기서는 공공데이터포털
표준 응답 형태를 본뜬 표본 XML 로 해석기를 잠근다(태그 이름이 달라도 열로 살아남는지 포함).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "external_data", "collectors"))

from collect_group_affiliates import parse_items  # noqa: E402

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body>
    <items>
      <item><repnGrpNm>LG</repnGrpNm><cmpnNm>엘지전자(주)</cmpnNm><crno>1101110019945</crno></item>
      <item><repnGrpNm>LG</repnGrpNm><cmpnNm>엘지화학(주)</cmpnNm><crno>1101110043818</crno></item>
      <item><repnGrpNm>에코프로</repnGrpNm><cmpnNm>주식회사 에코프로비엠</cmpnNm><crno>1345110123456</crno></item>
    </items>
    <numOfRows>500</numOfRows><pageNo>1</pageNo><totalCount>3</totalCount>
  </body>
</response>"""


def test_parse_items_standard_response():
    rows, code, total = parse_items(SAMPLE)
    assert code == "00" and total == 3 and len(rows) == 3
    assert rows[0]["repnGrpNm"] == "LG" and rows[0]["cmpnNm"] == "엘지전자(주)"
    assert rows[2]["cmpnNm"].endswith("에코프로비엠")


def test_parse_items_unknown_tags_survive_as_columns():
    xml = SAMPLE.replace("repnGrpNm", "grpNm").replace("cmpnNm", "afilCmpnNm")
    rows, code, total = parse_items(xml)
    assert rows and set(rows[0]) == {"grpNm", "afilCmpnNm", "crno"}


def test_parse_items_error_code_surfaces():
    xml = "<response><header><resultCode>30</resultCode></header><body><items/></body></response>"
    rows, code, total = parse_items(xml)
    assert code == "30" and rows == [] and total is None
