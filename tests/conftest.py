# -*- coding: utf-8 -*-
"""pytest 공통 경로 설정.

무엇: repo 루트와 preprocessing/ 을 sys.path 에 추가한다.
왜: 테스트는 `pipeline.time_policy`(루트 기준 패키지)와 `preprocess`(preprocessing/
    안의 단일 모듈, __init__.py 없음)를 import 해야 한다. `pytest` 를 어느 위치에서
    실행하든(루트 / tests/ 안) 동일하게 동작하도록 conftest 에서 경로를 고정한다.
    preprocess.py 는 import 시점 부작용(폴더 생성·datasets 존재 검사)이 없도록
    리팩터링되어 있어(main() 실행 경로에서만 검사) 원본 데이터 없이 import 가능하다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "preprocessing")):
    if p not in sys.path:
        sys.path.insert(0, p)
