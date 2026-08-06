import json

import requests

resp = requests.get(
    "http://127.0.0.1:8000/answer",
    params={
        "question_id": "Q-001",
        "question": "미국 증시에 상장된 주식형 ETF 중에서 총보수가 낮고 운용 규모가 큰 상품 3개만 비교해 주세요",
    },
)
print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
