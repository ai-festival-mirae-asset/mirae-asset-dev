#!/usr/bin/env bash
# 상태 점검 — cron 이 5분마다 실행(install.sh 가 /etc/cron.d/mirae-health 등록).
# /health 가 10초 안에 응답하지 않거나 status!=ok 이면 서비스를 재기동한다.
# 규정: 장애로 인한 재기동은 실격이 아니다(8/13 확정) — 코드·데이터 변경은 하지 않는다.
# 로그: /var/log/mirae-health.log (한 줄/회, 정상은 OK 한 줄만)
# install.sh가 cron에 포트를 첫 인자로 기록한다. 수동 실행은 환경변수도 호환한다.
PORT="${1:-${PORT:-80}}"
URL="http://127.0.0.1:${PORT}/health"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

body="$(curl -fsS --max-time 10 "$URL" 2>/dev/null || true)"
if [ -n "$body" ] && echo "$body" | grep -q '"status":"ok"\|"status": "ok"'; then
  # 디스크 여유(루트) — 90% 넘으면 경고만(캐시·저널은 크기 제한이 걸려 있음)
  use="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
  if [ "${use:-0}" -ge 90 ]; then echo "$STAMP WARN disk ${use}% used"; fi
  echo "$STAMP OK $(echo "$body" | tr -d '\n' | cut -c1-160)"
  exit 0
fi

echo "$STAMP FAIL health 응답 없음/비정상 — mirae-api 재기동"
systemctl restart mirae-api
sleep 60
if curl -fsS --max-time 10 "$URL" >/dev/null 2>&1; then
  echo "$STAMP RECOVERED"
else
  echo "$STAMP STILL DOWN — journalctl -u mirae-api -n 100 확인 필요"
  journalctl -u mirae-api -n 30 --no-pager 2>/dev/null | tail -n 30
fi
