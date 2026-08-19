# 배포·운영 안내서 (runbook) — 평가용 서버를 올리고 2주 동안 사람 손 없이 굴리는 법

> 서버(NCP)를 **만드는** 절차는 [../NCP_SERVER_SETUP.md](../NCP_SERVER_SETUP.md) 1~6장. 이 문서는 그다음 —
> 만든 서버에 **우리 프로그램을 올리고(배포)**, **확인하고(리허설)**, **9/7~9/20 무인 운영**하는 법이다.
> 규정 요점: 9/6 23:59 이후 코드·데이터·배포 변경 = 실격 / 장애로 인한 **재기동은 실격 아님** / 운영 09.07~09.20 상시 활성.

## 0. 이 폴더의 파일

| 파일 | 역할 |
|---|---|
| `install.sh` | 설치·갱신 한 방 스크립트 — 저장소 받기 → 가상환경·패키지 → 비밀값 파일 견본 → DuckDB·그래프 생성 → systemd 등록 → 상태 점검 cron → 예열. 여러 번 실행해도 안전 |
| `mirae-api.service` | systemd 서비스 정의 — 죽으면 5초 뒤 자동 재기동, 부팅 시 자동 시작, 메모리 상한 3.2GB |
| `healthcheck.sh` | 5분마다 `/health` 확인, 실패 시 서비스 재기동(cron 이 부름). 로그 `/var/log/mirae-health.log` |
| `warmup.sh` | 기동 직후 예열 호출 5건(첫 요청 지연 방지) |

## 1. 처음 배포 (서버 접속 후 root 로, 약 5분)

```bash
apt-get update && apt-get install -y git
git clone https://github.com/ai-festival-mirae-asset/mirae-asset-dev.git /opt/mirae-asset-dev
bash /opt/mirae-asset-dev/infra/deploy/install.sh --branch main
```

- 처음 실행하면 `/etc/mirae-api.env` 견본이 생긴다 → `nano /etc/mirae-api.env` 로 `CLOVASTUDIO_API_KEY=` 뒤에 키를 붙여 넣고 저장(파일 권한 600 — 저장소 밖, 절대 커밋 금지) → **`install.sh` 를 한 번 더 실행**(서비스 재기동).
- 키 없이도 서버는 뜬다(규칙 엔진만, HCX 꺼짐) — `/health` 의 `hcx_router`/`hcx_generator` 가 `true` 여야 실전 구성이다.
- 어느 브랜치를 올릴지: 최종 제출은 `main`. 리허설은 `--branch papuagigi` 처럼 개인 브랜치도 가능.

## 2. 배포 직후 확인 (내 PC 에서)

```bash
curl http://<공인IP>/health
curl -G "http://<공인IP>/answer" --data-urlencode "question_id=Q-001" --data-urlencode "question=순자산총액 기준으로 국내 ETF 상위 5개 알려줘"
```

`/health` 예: `{"status":"ok","db":true,"index_entries":…,"graph_triples":1128224,"vector":true,"hcx_router":true,"hcx_generator":true,"cache_size":…}` — `graph_triples` 가 0 이면 그래프가 안 올라온 것(설치 로그 확인), `vector:false` 면 인덱스 파일 누락, `hcx_*:false` 면 키 미설정.

**원격 리허설(권장, 크레딧 소모)** — 내 PC 의 채점기를 서버로 겨눈다(모의고사 105문항, 약 10분):
```bash
python evalset/eval_runner.py --mode http --base-url http://<공인IP> --tag remote --baseline evalset/reports/<직전 성적표>.jsonl
```
성적표(`evalset/reports/eval_*_remote.md`)의 **응답 시간(p95·최대)** 이 로컬보다 얼마나 느린지 본다 — 서버 사양(2vCPU/4GB)에서 15초를 넘는 문항이 있으면 라우터/생성 타임아웃(현재 6초/8초)을 더 낮춘다.

## 3. 갱신 배포 (9/6 23:59 전까지만)

```bash
bash /opt/mirae-asset-dev/infra/deploy/install.sh --branch main
```
저장소 최신 커밋으로 맞추고(`reset --hard origin/main`), 그래프 코드가 바뀌었으면 그래프를 다시 만들고, 서비스를 재기동한다. 데이터 정제 결과·벡터 인덱스는 저장소에 있으므로 서버에서 만들지 않는다.

**9/6 23:59 이후에는 이 스크립트를 실행하지 않는다**(코드·데이터 변경 = 실격). 허용되는 것은 3장 아래의 "재기동"뿐.

## 4. 2주 무인 운영 (9/7~9/20)

자동으로 되는 것:
- 프로세스가 죽으면 systemd 가 5초 뒤 재기동 · 서버가 재부팅돼도 자동 시작
- 5분마다 `/health` 점검 → 무응답이면 재기동 (`/var/log/mirae-health.log` 에 기록)
- 저널 로그는 300MB 로 상한 · 답변 캐시(`storage/output/answer_cache.jsonl`)는 질문당 한 줄이라 작다

사람이 가끔 볼 것(하루 1번이면 충분):
```bash
systemctl status mirae-api --no-pager | head -5      # active (running) 인지
tail -n 3 /var/log/mirae-health.log                   # 최근 점검 결과(OK 만 있으면 정상)
journalctl -u mirae-api --since "1 hour ago" | grep -c "응답 시간" # 최근 1시간 요청 수(0 이어도 정상 — 평가 전엔 요청 없음)
df -h / | tail -1                                     # 디스크 여유
```
- **크레딧 잔량**: NCP 콘솔 > 마이페이지 > 크레딧. 서버(월 4~7만원)+CLOVA 호출(문항당 HCX 2콜 안팎). 9/30 크레딧 만료 — 그전에 평가가 끝난다.
- **재기동이 필요할 때**(응답 없음·오류 반복): `systemctl restart mirae-api` → 1분 뒤 `/health`. 이것은 규정상 허용(장애 재기동). 코드·데이터는 손대지 않는다.
- 주최 발신 IP 대역이 공지되면 ACG 인바운드 80 을 그 대역만 허용해도 된다(선택 — 공지 전엔 0.0.0.0/0 유지).

## 5. 문제가 생겼을 때 (증상 → 확인 → 조치)

| 증상 | 확인 | 조치 |
|---|---|---|
| `/health` 무응답 | `systemctl status mirae-api`, `journalctl -u mirae-api -n 50` | `systemctl restart mirae-api`(허용). 기동 실패가 반복되면 로그의 첫 오류 줄을 본다 — 그래프 파일 오류면 `install.sh` 재실행(9/6 전만) |
| 답변은 오는데 `hcx_router:false` | `/etc/mirae-api.env` 의 키, `journalctl` 의 401/403 | 키를 채우고 `systemctl restart mirae-api`. 키 만료·크레딧 소진이면 CLOVA Studio 콘솔 확인 |
| 응답이 15초 근처 | 성적표 `--mode http` 응답 시간 | 서버 CPU 부족 — 라우터/생성 타임아웃 하향은 코드 변경이라 9/6 전에만. 이후엔 그대로 둔다(60초 권장 안엔 든다) |
| 디스크 90%+ | `du -sh /var/log/journal storage/output` | 저널은 자동 상한. 캐시 파일이 크면 서비스 중지 후 삭제 가능(캐시는 결과물이 아님) |
| 재부팅됨 | `uptime`, `systemctl is-enabled mirae-api` | enable 돼 있으면 자동 복구. `/health` 만 확인 |

## 6. 종료 (평가 종료 공지 후)

`systemctl disable --now mirae-api` → NCP 콘솔에서 서버 **반납**(정지는 과금 계속) → 공인 IP·스토리지 반납 → 9/30 크레딧 만료 전 정리 완료.
