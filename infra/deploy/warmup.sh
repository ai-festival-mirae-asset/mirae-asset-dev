#!/usr/bin/env bash
# 예열 호출 — 기동 직후 첫 요청이 느린 것을 막는다(캐시·DuckDB 페이지·HCX 연결 워밍업).
# 쓰는 법: bash warmup.sh http://127.0.0.1:80   (install.sh 가 자동 호출)
BASE="${1:-http://127.0.0.1:80}"
QS=(
  "공모펀드는 총 몇 개야?"
  "순자산총액 기준으로 국내 ETF 상위 5개 알려줘"
  "삼성전자가 포함된 ETF 알려줘"
  "kimi 관련 투자 상품 있어?"
  "반도체 산업에 집중 투자하는 해외 ETF는?"
)
i=0
for q in "${QS[@]}"; do
  i=$((i+1))
  t0=$(date +%s.%N)
  code=$(curl -sS -o /dev/null -w '%{http_code}' -G "$BASE/answer" --data-urlencode "question_id=warm-$i" --data-urlencode "question=$q" --max-time 120 || echo "ERR")
  t1=$(date +%s.%N)
  printf 'warm-%d %s %.1fs %s\n' "$i" "$code" "$(echo "$t1 - $t0" | bc)" "$q"
done
