#!/usr/bin/env bash
# =============================================================================
# 평가용 API 서버 설치·갱신 스크립트 (Ubuntu 24.04, root)
#
# 무엇: 저장소를 /opt/mirae-asset-dev 에 받아(또는 갱신) 가상환경·패키지·데이터 파일·
#       systemd 서비스까지 한 번에 준비한다. 여러 번 실행해도 안전(idempotent).
# 쓰는 법:
#   1) 처음:  curl -fsSL <raw URL>/infra/deploy/install.sh | bash -s -- --branch main
#      스크립트가 git·Python·cron 설치부터 저장소 clone까지 모두 담당한다.
#   2) 갱신:  bash /opt/mirae-asset-dev/infra/deploy/install.sh --branch main   (9/6 23:59 이후 금지!)
#   옵션: --branch <이름>(기본 main) · --repo <URL> · --no-restart(중지 상태로 설치) · --port 80
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

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "--port 는 1~65535 범위의 정수여야 합니다: $PORT"; exit 2
fi

# 1) 시스템 패키지 ---------------------------------------------------------------
log "1/8 시스템 패키지·Python 확인"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl jq bc cron >/dev/null
systemctl enable --now cron >/dev/null

read -r PY_MAJOR PY_MINOR < <(python3 -c 'import sys; print(sys.version_info.major, sys.version_info.minor)')
if [ "$PY_MAJOR" -ne 3 ] || [ "$PY_MINOR" -lt 12 ]; then
  echo "Python 3.12 이상이 필요합니다. 현재: $(python3 --version)"; exit 1
fi
log "   $(python3 --version) · cron $(systemctl is-active cron)"

# 갱신 중 실행 중인 서비스가 DuckDB·가상환경을 잡지 않도록 먼저 중지한다.
systemctl stop mirae-api >/dev/null 2>&1 || true

# 2) 저장소 받기/갱신 -------------------------------------------------------------
log "2/8 저장소 ($REPO_URL, 브랜치 $BRANCH)"
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
log "3/8 파이썬 가상환경·패키지"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
else
  read -r VENV_MAJOR VENV_MINOR < <(.venv/bin/python -c 'import sys; print(sys.version_info.major, sys.version_info.minor)')
  if [ "$VENV_MAJOR" -ne 3 ] || [ "$VENV_MINOR" -lt 12 ]; then
    echo ".venv가 Python 3.12 미만입니다. 서버의 .venv를 지운 뒤 다시 실행하세요."; exit 1
  fi
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 4) 비밀값 파일 -----------------------------------------------------------------
log "4/8 비밀값 파일 $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# 평가용 서버 비밀값 — 값은 사람이 직접 채운다. 저장소에 절대 커밋하지 않는다.
CLOVASTUDIO_API_KEY=
EOF
  chmod 600 "$ENV_FILE"
  log "   견본 생성됨 → nano $ENV_FILE 로 CLOVASTUDIO_API_KEY 값을 채우고 다시 실행하세요"
fi
HAS_CLOVA_KEY=1
if ! grep -q '^CLOVASTUDIO_API_KEY=.\+' "$ENV_FILE"; then
  HAS_CLOVA_KEY=0
  log "   경고: CLOVASTUDIO_API_KEY 가 비어 있음 — 서버는 HCX 없이(규칙 엔진만) 뜬다"
fi

# 5) 데이터 파일 (DuckDB · 그래프) — 정제 CSV·벡터 인덱스는 저장소에 있음 -----------------
log "5/8 데이터 파일"
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
if [ ! -s vector/output/index_global_etf.npz ] || [ ! -s vector/output/index_meta_global_etf.json ]; then
  echo "배포 중단: 벡터 인덱스가 없거나 비어 있습니다. 저장소의 vector/output을 확인하세요."; exit 1
fi

# 6) 배포 전 자동 테스트 ----------------------------------------------------------
log "6/8 전체 자동 테스트 (라이브 LLM 호출 제외)"
.venv/bin/python -m pytest tests/ -q

# 7) systemd 서비스 ---------------------------------------------------------------
log "7/8 systemd·cron 서비스"
sed "s#--port 80#--port $PORT#" infra/deploy/mirae-api.service > /etc/systemd/system/mirae-api.service
systemctl daemon-reload
systemctl enable mirae-api >/dev/null 2>&1 || true
# 상태 점검 cron (5분마다 /health, 실패 시 재기동) + journald 용량 제한
install -m 755 infra/deploy/healthcheck.sh /usr/local/bin/mirae-healthcheck
echo "*/5 * * * * root /usr/local/bin/mirae-healthcheck $PORT >> /var/log/mirae-health.log 2>&1" > /etc/cron.d/mirae-health
chmod 644 /etc/cron.d/mirae-health
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=300M\n' > /etc/systemd/journald.conf.d/mirae.conf
systemctl restart systemd-journald || true
if [ "$RESTART" -eq 1 ]; then
  systemctl restart mirae-api
  log "   재기동 — 그래프 적재 중(약 1분)"
else
  log "설치 완료 — --no-restart 지정으로 서비스는 중지 상태입니다."
  exit 0
fi

# 8) 확인 ------------------------------------------------------------------------
log "8/8 실전 구성 확인"
HEALTH_BODY=""
for i in $(seq 1 36); do
  if HEALTH_BODY="$(curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null)"; then break; fi
  sleep 5
done
if [ -z "$HEALTH_BODY" ]; then
  log "서버가 아직 안 떴습니다 — journalctl -u mirae-api -n 50 으로 확인"
  exit 1
fi
echo "$HEALTH_BODY" | jq .
if ! echo "$HEALTH_BODY" | jq -e '
  .status == "ok" and .db == true and .index_entries > 0 and .graph_triples > 0
' >/dev/null; then
  log "배포 실패: DB·색인·그래프 구성이 완전하지 않습니다."
  exit 1
fi
if [ "$HAS_CLOVA_KEY" -eq 1 ] && ! echo "$HEALTH_BODY" | jq -e '
  .vector == true and .hcx_router == true and .hcx_generator == true
' >/dev/null; then
  log "배포 실패: CLOVA 키가 있지만 벡터·HCX 구성이 준비되지 않았습니다."
  exit 1
fi
log "예열 호출(첫 요청 지연 방지)"
bash infra/deploy/warmup.sh "http://127.0.0.1:$PORT"
log "완료. 외부에서: curl -G http://<공인IP>:$PORT/answer --data-urlencode 'question=공모펀드는 총 몇 개야?'"
