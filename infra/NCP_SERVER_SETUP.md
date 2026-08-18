# NCP 평가용 API 서버 구축 가이드 — 따라 하기 (2026-08-12 · 8/13 공식 규격 반영)

> 무엇: 평가자가 `curl -G "http://{공인IP}/answer"`로 호출할 서버를 네이버 클라우드에 만드는 절차.
> 왜: 평가는 주최 측이 제출된 End-point로 GET 요청을 보내는 방식이라, Public 망에서 접근 가능한 서버가 필수(8/6 설명회 + 공식 세부 규정).
> **[8/13 공식 확정 — `../API 호출 관련 참고.txt`]** HTTP 기본(HTTPS 선택·self-signed 무방) · 표준 포트 80/443 · **도메인 불필요, 공인 IP 제출 가능** · 인증 헤더 없음 · 문항당 타임아웃 300초, 타임아웃·5xx 시 최대 2회 재시도 · **서버 운영 기간 = 9/7~9/20 중 별도 공지 기간(주제별 최대 1주)** · 장애로 인한 재기동은 실격 아님 · End-point 주소는 README.md에 필수 명시.
> 근거: [NCP 공식 가이드 — Server 생성(VPC)](https://guide.ncloud-docs.com/docs/server-create-vpc) + 8/6 설명회 안내(권장 스펙 2vCPU·4GB·20GB, 월 4~7만원 시뮬레이션).

## 언제 만들까 + 비용 감각

- **지금 당장은 필수 아님.** 로컬 개발(전처리·KG·에이전트)은 서버 없이 진행된다. **8/17(S2) 배포 연습 시작, 늦어도 8/23 E2E 하드 게이트 전**에는 만들어야 한다.
- 참고: 공식 규정상 개인 노트북 + 터널링(ngrok 등)로 정적 URL을 제출하는 것도 허용된다. 다만 평가 기간(최대 1주) 무중단 안정성은 클라우드 서버가 압도적으로 유리 — NCP 서버를 기본으로 간다.
- 서버는 **켜져 있는 동안 과금**된다(정지해도 스토리지·공인 IP는 과금). 크레딧 40만원 확보 상태라 월 4~7만원 수준은 여유 있음. 연습용으로 만들었다가 **반납**하면 과금이 멈춘다.
- 요금제: 처음엔 **시간 요금제**(만들고 지우기 부담 없음) → 평가 기간 상시 가동이 확정되는 8월 말에 월 요금제 전환 검토.

## 0. 사전 확인 (완료 상태)

- [x] NCP 계정 + 결제수단 등록 — 완료
- [x] 크레딧 확보(신규 30만 + 마케팅 동의 10만 = 40만) — **마이페이지 > 크레딧에서 유효기간을 꼭 확인**할 것(무료 크레딧은 통상 사용 기한이 있다). AI 페스티벌 크레딧(20만)은 신청 경로 확인 후 추가 등록(규정 PDF·디스코드 대기)
- [ ] 콘솔 진입: [console.ncloud.com](https://console.ncloud.com) → 우측 상단 리전 **한국(KR)**, 플랫폼 **VPC** 확인 (Classic 아님!)

## 1. VPC 생성 (가상 네트워크)

콘솔 → **Services > Networking > VPC > VPC Management** → [VPC 생성]

| 항목 | 값 |
|---|---|
| 이름 | `mirae-vpc` |
| IP 주소 범위 | `10.0.0.0/16` (사설 대역 그대로 사용) |

생성에 1~2분 걸린다. 상태 `운영중`이 되면 다음 단계.

## 2. Subnet 생성 (서버가 놓일 구획)

같은 화면 좌측 **Subnet Management** → [Subnet 생성]

| 항목 | 값 |
|---|---|
| 이름 | `mirae-subnet-public` |
| VPC | `mirae-vpc` |
| IP 주소 범위 | `10.0.1.0/24` |
| 가용 Zone | 아무거나 (예: KR-2) |
| Internet Gateway 전용여부 | **Public** ← 공인 IP를 붙이려면 반드시 Public |
| 용도 | 일반 |

## 3. Server 생성

콘솔 → **Services > Compute > Server** → [서버 생성] (신규 콘솔 화면 기준 6단계)

1. **서버 이미지**: `Ubuntu Server 24.04 LTS` (또는 22.04) — 리눅스 기준. 윈도우가 편하면 Windows Server 선택(접속은 RDP)
2. **서버 설정**:
   - VPC/Subnet: 위에서 만든 것 선택
   - 서버 타입 **Standard**, 스펙은 **2vCPU · 메모리 4GB** 조합 선택(세대에 따라 s2-g3 등 코드명이 다름 — vCPU/메모리 숫자로 고르면 된다)
   - 요금제: **시간 요금제**
   - 서버 이름: `mirae-api-01`
   - 공인 IP: **새로 할당** 선택 가능하면 여기서 함께(안 보이면 4단계에서 별도 신청)
3. **스토리지**: 기본 SSD (10~50GB 아무거나 — 데이터 1GB 미만이라 최소로 충분)
4. **인증키**: [신규 생성] → `mirae-key.pem` **다운로드 후 안전한 곳에 보관** (분실 시 비밀번호 확인 불가 — 재발급 절차 필요)
5. **네트워크 접근(ACG)**: 기본 ACG 선택(규칙은 5장에서 수정)
6. 최종 확인 → 생성 (수 분 소요)

## 4. 공인 IP 할당 (3단계에서 못 했다면)

**Server > Public IP** → [공인 IP 신청] → 적용 서버 `mirae-api-01` 선택.
이 IP가 평가자에게 제출할 엔드포인트 주소의 후보다(HTTPS/도메인 요건은 사무국 재공지 대기 — ROADMAP §8.2).

## 5. ACG(방화벽) 규칙 — 설명회 안내 그대로

**Server > ACG** → `mirae-vpc` 기본 ACG 선택 → [ACG 설정]

| 방향 | 프로토콜 | 접근 소스 | 포트 | 용도 |
|---|---|---|---|---|
| 인바운드 | TCP | **내 IP/32** (myip.com 등에서 확인) | 22 | SSH 접속 (0.0.0.0/0은 보안상 비권장) |
| 인바운드 | TCP | 0.0.0.0/0 | 80 | 평가용 HTTP |
| 인바운드 | TCP | 0.0.0.0/0 | 443 | 평가용 HTTPS |
| 아웃바운드 | TCP | 0.0.0.0/0 | 1-65535 | 외부 데이터 수집·패키지 설치 (기본값 유지) |

윈도우 서버를 골랐다면 22 대신 RDP(3389, 내 IP만).

## 6. 관리자 비밀번호 확인 + 접속

1. 서버 목록에서 `mirae-api-01` 체크 → **[서버 관리 및 설정 변경] > 관리자 비밀번호 확인** → `mirae-key.pem` 업로드 → root 비밀번호 표시됨 (NCP가 만든 비밀번호를 조회하는 방식 — 8/6 설명회 안내 그대로)
2. Windows PowerShell에서 접속:
   ```
   ssh root@<공인IP>
   ```
   비밀번호 입력 → 접속. (첫 접속 후 `passwd`로 비밀번호 변경 권장)

## 7. 서버 초기 세팅 (Ubuntu 기준 — 접속 후 순서대로)

```bash
apt update && apt install -y python3-pip python3-venv git
```

```bash
git clone <우리 repo URL> && cd mirae-asset-dev
python3 -m venv .venv && . .venv/bin/activate
pip install fastapi uvicorn pandas   # requirements.txt 확정 전 임시
```

상시 가동은 systemd 서비스로 (터미널 끊겨도 살아 있고, 서버 재부팅 시 자동 시작):

```ini
# /etc/systemd/system/mirae-api.service
[Unit]
Description=mirae answer API
After=network.target

[Service]
WorkingDirectory=/root/mirae-asset-dev
ExecStart=/root/mirae-asset-dev/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 80
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now mirae-api
```

검증: 내 PC에서 `curl http://<공인IP>/health` 응답 확인 → 이게 되면 "외부에서 접근 가능한 서버" 완성.

## 8. HTTPS/도메인 — **불필요 확정 (8/13)**

공식 세부 규정으로 해결됐다: **HTTP 기본, 포트 80. 도메인·인증서·Global DNS·Certificate Manager 전부 불필요** — 공인 IP를 그대로 End-point로 제출하면 된다(README.md에 명시). HTTPS를 굳이 쓰려면 self-signed 인증서도 무방(포트 443).

선택 사항: **주최 측 발신 IP 대역이 추후 공지**되면, ACG 인바운드 80 포트를 그 대역만 허용하도록 좁힐 수 있다(봇·스캐너 차단 — 권장하되 공지 전에는 0.0.0.0/0 유지).

## 9. 비용·정리 수칙 (실격·과금 방지)

- **정지 ≠ 무과금**: 서버 정지 시에도 스토리지·공인 IP 요금은 나간다. 완전 종료는 **반납**.
- **9/6 이후 '결과물 변경' 금지 = 실격**(커밋·push·재배포). 단 **[8/13 확정] 불가피한 장애로 인한 서버 재기동은 실격이 아니다** — systemd `Restart=always`로 자동 복구를 걸어 두고, 수동 개입은 재기동까지만.
- **서버 운영 기간은 9/7~9/20** — 안내문(8/13)은 "이 중 별도 공지 기간(주제별 최대 1주)", 공식 과제설명 PDF(8/18 입수)는 **"운영 09.07~09.20 상시 활성"**. → **9/7~9/20 두 주 전체를 사람 손 없이 가동하는 것으로 준비**한다(자동 재기동 + `/health` 주기 확인 + 로그·디스크·크레딧 잔량 점검).
- **9/30 크레딧 만료** — 운영 기간 종료 확인 후 서버·공인 IP·스토리지 전부 반납.
- 응답 시간: 공식 권장 문항당 60초 이내(우리 목표 15초). 서버 사양이 낮아 첫 요청이 느리면 기동 직후 예열 호출(예: `/answer?question=공모펀드는 총 몇 개야?`)을 한 번 넣어 둔다.

## 체크리스트

- [ ] VPC + Public Subnet 생성
- [ ] 서버 생성(Ubuntu · 2vCPU/4GB · 시간제) + pem 보관
- [ ] 공인 IP 할당
- [ ] ACG: 22(내 IP), 80/443(전체), 아웃바운드 오픈
- [ ] SSH 접속 + 비밀번호 변경
- [ ] systemd 서비스 등록, 외부에서 `/health` 응답 확인
- [ ] (사무국 공지 후) HTTPS/도메인 결정
- [ ] 공인 IP 확정 즉시 `README.md` §5 와 `API_SPEC.md` §1 의 End-point URL 칸 기입(제출 필수)
- [ ] 2주 무인 운영 점검: `Restart=always` 동작 확인(프로세스 강제 종료 후 자동 복구) · 디스크 여유 · 캐시 파일 크기 · 크레딧 잔량
