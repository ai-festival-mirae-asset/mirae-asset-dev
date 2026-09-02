# 배포·운영 안내서 — NCP 서버 만들기부터 2주 무인 운영까지

> 무엇: 평가자가 `curl -G "http://<공인IP>/answer"`로 호출할 서버를 네이버 클라우드(NCP)에 만들고(§1), 우리 프로그램을 올리고(§2), 확인하고(§3), **9/7~9/20 두 주 동안 사람 손 없이 굴리는**(§5) 절차. 9/2 문서 통합(구 infra/NCP_SERVER_SETUP.md 흡수).
> 규정 요점: **9/6 23:59 이후 코드·데이터·배포 변경 = 실격** / 장애로 인한 **재기동은 실격 아님** / 운영 09.07~09.20 상시 활성 / HTTP 기본(HTTPS 선택, self-signed 무방) · 표준 포트 80/443 · 도메인 불필요(공인 IP 제출) · 인증 헤더 없음 · 문항당 타임아웃 300초, 타임아웃·5xx 시 최대 2회 재시도 · End-point 주소는 README.md와 API_SPEC.md에 명시.
> 응답 시간: 주최는 기준을 공개하지 않는다(8/26 공지). 내부 목표 15초, 로컬 실측 최대 13.2초.

## 0. 이 폴더의 파일

| 파일 | 역할 |
|---|---|
| `install.sh` | 설치·갱신 한 방 스크립트 — 저장소 받기 → 가상환경·패키지 → 비밀값 파일 견본 → DuckDB·그래프 생성 → systemd 등록 → 상태 점검 cron → 예열. 여러 번 실행해도 안전 |
| `mirae-api.service` | systemd 서비스 정의 — 죽으면 5초 뒤 자동 재기동, 부팅 시 자동 시작, 메모리 상한 3.2GB |
| `healthcheck.sh` | 5분마다 `/health` 확인, 실패 시 서비스 재기동(cron이 부름). 로그 `/var/log/mirae-health.log` |
| `warmup.sh` | 기동 직후 예열 호출 5건(첫 요청 지연 방지) |

## 1. NCP 서버 만들기 (콘솔, 약 20분)

권장 스펙은 설명회 안내 그대로 2vCPU·4GB·20GB(월 4~7만원). 서버는 **켜져 있는 동안 과금**되고 정지해도 스토리지·공인 IP는 과금된다 — 완전 종료는 **반납**. 요금제는 시간 요금제로 시작한다. 크레딧(신규 30만 + 마케팅 동의 10만 = 40만) 유효기간은 마이페이지 > 크레딧에서 확인.

콘솔 [console.ncloud.com](https://console.ncloud.com) → 우측 상단 리전 **한국(KR)**, 플랫폼 **VPC**(Classic 아님).

| 순서 | 어디서 | 값 |
|---|---|---|
| 1. VPC 생성 | Services > Networking > VPC > VPC Management | 이름 `mirae-vpc`, IP 범위 `10.0.0.0/16` |
| 2. Subnet 생성 | 같은 화면 Subnet Management | 이름 `mirae-subnet-public`, VPC `mirae-vpc`, `10.0.1.0/24`, Zone 아무거나, **Internet Gateway 전용여부 = Public**(공인 IP를 붙이려면 필수), 용도 일반 |
| 3. Server 생성 | Services > Compute > Server > 서버 생성 | 이미지 `Ubuntu Server 24.04 LTS`(또는 22.04) · VPC/Subnet 위 것 · 타입 Standard **2vCPU·4GB** · 시간 요금제 · 이름 `mirae-api-01` · 공인 IP "새로 할당"(안 보이면 4) · 스토리지 기본 SSD(데이터 1GB 미만) · **인증키 신규 생성 → `mirae-key.pem` 다운로드·보관**(분실 시 비밀번호 확인 불가) · ACG 기본 선택 |
| 4. 공인 IP | Server > Public IP > 공인 IP 신청 | 적용 서버 `mirae-api-01`. 이 IP가 제출할 End-point 주소 |
| 5. ACG(방화벽) | Server > ACG > `mirae-vpc` 기본 ACG > ACG 설정 | 인바운드 TCP **22 — 내 IP/32**(SSH) · **80 — 0.0.0.0/0**(평가용 HTTP) · 443 — 0.0.0.0/0(선택) · 아웃바운드 TCP 0.0.0.0/0 1-65535(기본 유지) |
| 6. 접속 | 서버 체크 > 서버 관리 및 설정 변경 > 관리자 비밀번호 확인 > `mirae-key.pem` 업로드 | root 비밀번호 표시 → PowerShell에서 `ssh root@<공인IP>` → 첫 접속 후 `passwd`로 변경 권장 |

HTTPS·도메인·인증서는 **불필요 확정**(8/13 공식 규격) — 공인 IP를 그대로 제출한다. 주최 발신 IP 대역이 공지되면 80 포트를 그 대역만 허용하도록 좁혀도 된다(선택 — 공지 전엔 0.0.0.0/0 유지).

## 2. 처음 배포 (서버 접속 후 root로, 약 5분)

```bash
apt-get update && apt-get install -y git
git clone https://github.com/ai-festival-mirae-asset/mirae-asset-dev.git /opt/mirae-asset-dev
bash /opt/mirae-asset-dev/infra/deploy/install.sh --branch main
```

- 처음 실행하면 `/etc/mirae-api.env` 견본이 생긴다 → `nano /etc/mirae-api.env`로 `CLOVASTUDIO_API_KEY=` 뒤에 키를 붙여 넣고 저장(파일 권한 600 — 저장소 밖, 절대 커밋 금지) → **`install.sh`를 한 번 더 실행**(서비스 재기동).
- 스크립트가 하는 일: 저장소 받기/갱신 → 가상환경 + `requirements.txt` → DuckDB·그래프 생성(정제 CSV·벡터 인덱스는 저장소에 있음) → `mirae-api.service` 등록 → 5분마다 `/health` 점검 cron → 예열 호출.
- 키 없이도 서버는 뜬다(규칙 엔진만, HCX 꺼짐) — `/health`의 `hcx_router`/`hcx_generator`가 `true`여야 실전 구성이다.
- 최종 제출은 `main`. 리허설로 다른 브랜치를 올리려면 `--branch <이름>`.

## 3. 배포 직후 확인 (내 PC에서)

```bash
curl http://<공인IP>/health
curl -G "http://<공인IP>/answer" --data-urlencode "question_id=Q-001" --data-urlencode "question=순자산총액 기준으로 국내 ETF 상위 5개 알려줘"
```

`/health` 예: `{"status":"ok","db":true,"index_entries":…,"graph_triples":924327,"vector":true,"hcx_router":true,"hcx_generator":true,"cache_size":…}` — `graph_triples`가 0이면 그래프가 안 올라온 것(설치 로그 확인), `vector:false`면 인덱스 파일 누락, `hcx_*:false`면 키 미설정. 이게 되면 "외부에서 접근 가능한 서버"(M2) 완성.

**원격 리허설(권장, 크레딧 소모)** — 내 PC의 채점기를 서버로 겨눈다(실전 미러 38문항, 약 5분):
```bash
python evalset/eval_runner.py --mode http --base-url http://<공인IP> --evalset evalset/evalset_mirror.jsonl --checks evalset/checks_mirror.jsonl --tag remote
```
성적표(`evalset/reports/eval_*_remote.md`)의 **응답 시간(p95·최대)**이 로컬보다 얼마나 느린지 본다. 서버 사양(2vCPU/4GB)에서 15초를 넘는 문항이 있으면 라우터/생성 타임아웃(현재 6초/8초)을 더 낮춘다 — 코드 변경이라 9/6 전에만.

**공인 IP 확정 즉시** `README.md` §5.5와 `API_SPEC.md` §1의 End-point URL 칸에 기입한다(제출 필수).

## 4. 갱신 배포 (9/6 23:59 전까지만)

```bash
bash /opt/mirae-asset-dev/infra/deploy/install.sh --branch main
```
저장소 최신 커밋으로 맞추고(`reset --hard origin/main`), 그래프 코드가 바뀌었으면 그래프를 다시 만들고, 서비스를 재기동한다. **9/6 23:59 이후에는 이 스크립트를 실행하지 않는다.** 허용되는 것은 §5의 "재기동"뿐.

**답변 규칙을 고친 배포라면 캐시를 반드시 비운다(9/2 실측).** 서버는 정상 답변을 `storage/output/answer_cache.jsonl`에 영구 저장하고 켜질 때 다시 읽는다(거절 답변도 저장). `install.sh`는 이 파일을 건드리지 않으므로, 고치기 전에 나갔던 오답이 캐시에 남아 있으면 같은 질문에 계속 옛 답이 나간다. 캐시는 결과물이 아니라 지워도 규정과 무관하다(§6).
```bash
systemctl stop mirae-api && rm -f /opt/mirae-asset-dev/storage/output/answer_cache.jsonl && systemctl start mirae-api
```
1분 뒤 `curl http://<공인IP>/health`의 `cache_size`가 예열 5건 안팎으로 돌아왔는지 확인한다.

## 5. 2주 무인 운영 (9/7~9/20)

자동으로 되는 것:
- 프로세스가 죽으면 systemd가 5초 뒤 재기동 · 서버가 재부팅돼도 자동 시작
- 5분마다 `/health` 점검 → 무응답이면 재기동(`/var/log/mirae-health.log`에 기록)
- 저널 로그는 300MB 상한 · 답변 캐시(`storage/output/answer_cache.jsonl`)는 질문당 한 줄이라 작다

사람이 가끔 볼 것(하루 1번이면 충분):
```bash
systemctl status mirae-api --no-pager | head -5      # active (running) 인지
tail -n 3 /var/log/mirae-health.log                   # 최근 점검 결과(OK 만 있으면 정상)
journalctl -u mirae-api --since "1 hour ago" | grep -c "응답 시간" # 최근 1시간 요청 수(0 이어도 정상)
df -h / | tail -1                                     # 디스크 여유
```
- **크레딧 잔량**: NCP 콘솔 > 마이페이지 > 크레딧. 서버(월 4~7만원) + CLOVA 호출(문항당 HCX 3콜 안팎). 9/30 크레딧 만료 — 그전에 평가가 끝난다.
- **재기동이 필요할 때**(응답 없음·오류 반복): `systemctl restart mirae-api` → 1분 뒤 `/health`. 규정상 허용(장애 재기동). 코드·데이터는 손대지 않는다.

## 6. 문제가 생겼을 때 (증상 → 확인 → 조치)

| 증상 | 확인 | 조치 |
|---|---|---|
| `/health` 무응답 | `systemctl status mirae-api`, `journalctl -u mirae-api -n 50` | `systemctl restart mirae-api`(허용). 기동 실패가 반복되면 로그의 첫 오류 줄 — 그래프 파일 오류면 `install.sh` 재실행(9/6 전만) |
| 답변은 오는데 `hcx_router:false` | `/etc/mirae-api.env`의 키, `journalctl`의 401/403 | 키를 채우고 `systemctl restart mirae-api`. 키 만료·크레딧 소진이면 CLOVA Studio 콘솔 확인 |
| 응답이 15초 근처 | 성적표 `--mode http` 응답 시간 | 서버 CPU 부족 가능성 — 타임아웃 하향은 코드 변경이라 9/6 전에만 |
| 디스크 90%+ | `du -sh /var/log/journal storage/output` | 저널은 자동 상한. 캐시 파일이 크면 서비스 중지 후 삭제 가능(캐시는 결과물이 아님) |
| 재부팅됨 | `uptime`, `systemctl is-enabled mirae-api` | enable돼 있으면 자동 복구. `/health`만 확인 |

## 7. 종료·정리 (평가 종료 공지 후)

`systemctl disable --now mirae-api` → NCP 콘솔에서 서버 **반납**(정지는 과금 계속) → 공인 IP·스토리지 반납 → **9/30 크레딧 만료 전** 정리 완료.

## 체크리스트

- [ ] VPC + Public Subnet 생성
- [ ] 서버 생성(Ubuntu · 2vCPU/4GB · 시간제) + pem 보관
- [ ] 공인 IP 할당
- [ ] ACG: 22(내 IP), 80/443(전체), 아웃바운드 오픈
- [ ] SSH 접속 + 비밀번호 변경
- [ ] `install.sh` 실행 + `/etc/mirae-api.env` 키 기입 + 재실행, 외부에서 `/health` 응답 확인(`hcx_router:true`)
- [ ] 원격 리허설: 응답 시간 p95·최대 확인
- [ ] 공인 IP를 `README.md` §5.5와 `API_SPEC.md` §1에 기입(제출 필수)
- [ ] 2주 무인 운영 점검: `kill -9` 후 자동 복구 확인 · `/var/log/mirae-health.log` OK · 디스크 여유 · 크레딧 잔량
