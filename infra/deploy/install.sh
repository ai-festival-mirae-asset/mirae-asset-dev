#!/usr/bin/env bash
# =============================================================================
# 평가용 API 서버 설치·갱신 스크립트 (Ubuntu 22.04/24.04, root)
#
# 무엇: 저장소를 /opt/mirae-asset-dev 에 받아(또는 갱신) 가상환경·패키지·데이터 파일·
#       systemd 서비스까지 한 번에 준비한다. 여러 번 실행해도 안전(idempotent).
# 쓰는 법:
#   1) 처음:  curl -fsSL <raw URL>/infra/deploy/install.sh | bash -s -- --branch main
#      또는:  git clone <repo> /opt/mirae-asset-dev && bash /opt/mirae-asset-dev/infra/deploy/install.sh
#   2) 갱신:  bash /opt/mirae-asset-dev/infra/deploy/install.sh --branch main   (9/6 23:59 이후 금지!)
#   옵션: --branch <이름>(기본 main) · --repo <URL> · --no-restart(서비스 재시작 생략) · --port 80
#
# 비밀값: /etc/mirae-api.env 에 CLOVASTUDIO_API_KEY=... 를 사람이 직접 적는다(권한 600).
#         이 스크립트는 파일이 없으면 빈 견본만 만들고 값을 묻지 않는다(키를 셸 이력에 남기지 않기 위해).
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ai-festival-mirae-asset/mirae-asset-dev.git}"
BRANCH="main"
APP_DIR="/opt/mirae-asset-dev"
ENV_FILE="/etc/mirae-api.env"
PORT="80"
RESTART=1

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    *) echo "모르는 옵션: $1"; exit 2 ;;
  esac
done

log() { echo "[install $(date '+%H:%M:%S')] $*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "root 로 실행해야 합니다 (sudo bash install.sh ...)"; exit 1
fi

# 1) 시스템 패키지 ---------------------------------------------------------------
log "1/7 시스템 패키지"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl jq bc >/dev/null

# 2) 저장소 받기/갱신 -------------------------------------------------------------
log "2/7 저장소 ($REPO_URL, 브랜치 $BRANCH)"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch --all --prune
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi
cd "$APP_DIR"
log "   커밋: $(git rev-parse --short HEAD) — $(git log -1 --format=%s | cut -c1-60)"

# 3) 가상환경 + 패키지 -----------------------------------------------------------
log "3/7 파이썬 가상환경·패키지"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 4) 비밀값 파일 -----------------------------------------------------------------
log "4/7 비밀값 파일 $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# 평가용 서버 비밀값 — 값은 사람이 직접 채운다. 저장소에 절대 커밋하지 않는다.
CLOVASTUDIO_API_KEY=
EOF
  chmod 600 "$ENV_FILE"
  log "   견본 생성됨 → nano $ENV_FILE 로 CLOVASTUDIO_API_KEY 값을 채우고 다시 실행하세요"
fi
if ! grep -q '^CLOVASTUDIO_API_KEY=.\+' "$ENV_FILE"; then
  log "   경고: CLOVASTUDIO_API_KEY 가 비어 있음 — 서버는 HCX 없이(규칙 엔진만) 뜬다"
fi

# 5) 데이터 파일 (DuckDB · 그래프) — 정제 CSV·벡터 인덱스는 저장소에 있음 -----------------
log "5/7 데이터 파일"
if [ ! -f storage/output/products.duckdb ] || [ preprocessing/processed -nt storage/output/products.duckdb ]; then
  .venv/bin/python storage/load_duckdb.py
else
  log "   DuckDB 최신 — 건너뜀"
fi
NEED_KG=0
[ ! -f kg/output/kr_bond.nt ] && NEED_KG=1
if [ "$NEED_KG" -eq 0 ] && head -1 kg/output/kr_etf.nt | grep -q 'ai-festival-mirae-asset.github.io'; then
  NEED_KG=1     # 8/18 이전 어휘(mf:) 그래프 — 재생성 필요
fi
if [ "$NEED_KG" -eq 0 ] && [ kg/build_kg.py -nt kg/output/kr_bond.nt ]; then NEED_KG=1; fi
if [ "$NEED_KG" -eq 1 ]; then .venv/bin/python kg/build_kg.py; else log "   그래프 최신 — 건너뜀"; fi
if [ ! -f vector/output/index_global_etf.npz ] || [ ! -f vector/output/index_meta_global_etf.json ]; then
  log "   경고: 벡터 인덱스 파일(vector/output/index_global_etf.npz·index_meta_global_etf.json)이 없음 — "
  log "         저장소에 커밋돼 있어야 함(재생성은 임베딩 API 요금 발생). 없으면 키워드 검색으로 대체 동작"
fi

# 6) systemd 서비스 ---------------------------------------------------------------
log "6/7 systemd 서비스"
sed "s#--port 80#--port $PORT#" infra/deploy/mirae-api.service > /etc/systemd/system/mirae-api.service
systemctl daemon-reload
systemctl enable mirae-api >/dev/null 2>&1 || true
# 상태 점검 cron (5분마다 /health, 실패 시 재기동) + journald 용량 제한
install -m 755 infra/deploy/healthcheck.sh /usr/local/bin/mirae-healthcheck
echo "*/5 * * * * root /usr/local/bin/mirae-healthcheck >> /var/log/mirae-health.log 2>&1" > /etc/cron.d/mirae-health
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=300M\n' > /etc/systemd/journald.conf.d/mirae.conf
systemctl restart systemd-journald || true
if [ "$RESTART" -eq 1 ]; then
  systemctl restart mirae-api
  log "   재기동 — 그래프 적재 중(약 1분)"
fi

# 7) 확인 ------------------------------------------------------------------------
log "7/7 확인"
for i in $(seq 1 36); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 5
done
if curl -fsS "http://127.0.0.1:$PORT/health" | jq . ; then
  log "예열 호출(첫 요청 지연 방지)"
  bash infra/deploy/warmup.sh "http://127.0.0.1:$PORT" >/dev/null || true
  log "완료. 외부에서: curl -G http://<공인IP>:$PORT/answer --data-urlencode 'question=공모펀드는 총 몇 개야?'"
else
  log "서버가 아직 안 떴습니다 — journalctl -u mirae-api -n 50 으로 확인"
  exit 1
fi
