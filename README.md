# mirae-asset-dev

미래에셋증권 AI Festival 금융상품 Agent 개발 저장소다. 공식 과제 요구사항과 4종 상품 데이터 탐색을 마치고, 비파괴 정제·품질검증 파이프라인을 구현했다. 다음 단계는 PostgreSQL 적재와 Text-to-SQL 베이스라인이다.

- 구현·평가·제출 계획: [`PROJECT_GUIDE.md`](./PROJECT_GUIDE.md)
- 현재 상태와 열린 결정: [`memory.md`](./memory.md)
- 데이터 문제·수정 상세: [`reports/DATA_QUALITY_REPORT.md`](./reports/DATA_QUALITY_REPORT.md)
- 공식 과제 소개서: [`manifest/금융상품Agent_과제소개.pdf`](./manifest/금융상품Agent_과제소개.pdf)

정제 산출물 생성과 검증:

```bash
python3 -m pip install -r requirements.txt
python3 -m pipeline.prepare_data --input-dir datasets --output-dir artifacts/data --manual-overrides config/manual_overrides.csv
python3 -m unittest discover -s tests -v
```

생성된 `artifacts/`는 재현 가능하므로 Git에서 제외한다. 채권 잔존일수는 저장값을 현재값으로 간주하지 않고 요청 시점의 서울 날짜로 계산하며, 실제 계산 기준일을 응답에 표시한다. 답변 생성 LLM은 공식 규칙에 따라 HyperCLOVA X만 사용한다.
