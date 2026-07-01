# 탐지 시나리오 (보안 룰) — 2026-06-25~

> Phase 1(수집→파싱→인리치→시각화) 위에 얹는 **탐지층**. 각 시나리오는 *로직 → 데이터 검증 → (나중에) Kibana 룰화* 순서.
> 설계 근거: `event.outcome`(success/failure/probe) 분류 + known-good(인증방식+계정) 기반.

## 시나리오 1 — SSH brute-force (로직 검증 완료, 룰화는 보류)
- **로직**: 한 `source.ip`가 **5분 윈도우 내 `event.outcome=failure` ≥ 20회** (known-good·사설IP 제외).
- **왜 윈도우(peak5m)인가**: 단순 누적(total)이 아니라 *시간당 밀도*가 brute-force의 본질. peak 5분 burst가 저속 스캔과 자동공격을 가름.
- **데이터 검증 (2026-06-25)**: 조건 충족 IP **30개**. 상위:
  | peak5m | total | IP | 국가 | ASN |
  |---|---|---|---|---|
  | 170 | 680 | 209.38.23.x | 호주 | DigitalOcean |
  | 166 | 239 | 113.0.152.x | 중국 | China Unicom |
  | 155 | 188 | 200.71.154.x | 베네수엘라 | Telefonica |
  | 152 | 640 | 167.71.235.x | 인도 | DigitalOcean |
  | 110 | 208 | 220.123.x | 한국 | Korea Telecom |
  | 47 | 94 | 149.118.135.x | 말레이시아 | Oracle Corp |
  - 1위 209.38.23.x = 5분 170회(≈34/분) = 명백한 자동공격.
- **Kibana 룰화(나중)**: ES query 룰 — query `event.outcome:failure NOT known-good`, **group by source.ip**, threshold ≥20 / 5m, check 1m. 액션 ① index(`security-alerts` 큐) ② Slack.
- 검증 쿼리: `terms source.ip` → `date_histogram 5m` → `max_bucket(peak5m)`, peak≥20 필터.

## 시나리오 2 — known-good 아닌 *성공* 로그인 (라이브 Kibana 룰 가동, 2026-06-27)
- **로직**: `event.outcome=success` AND (source.ip ∉ known-good **또는** 인증키 ∉ authorized_keys **또는** auth=password).
- 근거: 성공 179건 전부 publickey+알려진 계정이었고, <HOME_IP_PREV>(본인) 1건이 IP목록 밖이었음(§phase-1b). → **IP보다 인증방식+계정+키지문**이 견고.
- 침해 시 레드알럿.

## 시나리오 3 — 웹 스캐너 (설계됨, 미검증)
- **로직**: caddy 로그에서 `/.env`,`/wp-login.php` 등 민감경로 요청 + 404 폭증 + 의심 User-Agent(`Shodan`,`zgrab` 등).
- 필드: `url.path`, `http.status`, (User-Agent는 caddy.request.headers — 추가 파싱 필요).

## 알림(전달) — 보류
- 알림 큐 = `security-alerts` 인덱스(Index 커넥터) — SIEM의 핵심(알림=문서, 트리아지).
- 실시간 통보 = Slack 웹훅(사용자 보유). 웹훅 URL 확보 시 룰 액션으로 추가(5분 작업).

## 시나리오 2·3 데이터 검증 (2026-06-25)

### 시나리오 2 — known-good 아닌 성공 (검증)
- known-good 밖 외부 IP 성공 = **0건**(<내 IP 대역> 화이트리스트 후). 침해 없음.
- 성공 계정 = ubuntu(133)·<운영계정>(49)만(정상).
- **발견**: `Accepted password` 성공 **43건** — 키 전용인 줄 알았으나 비번 인증 성공 존재. → **권고: PasswordAuthentication no(키 전용 강제)**. 시나리오2가 잡은 실제 위생 이슈.
- 룰화: query `event.outcome:success NOT known-good` 또는 `Accepted password` → 알럿.

### 시나리오 3 — 웹 스캐너 (검증)
- 프로브 경로(http.status≥400): `/.env`,`/.git/config`,`/info.php`,`/config.json`,`/100.php` = 명백한 스캐닝.
- 스캐너 IP(4xx + distinct path): **45.148.10.x(네덜란드) 633요청/633경로** = 순수 경로열거. 외 23.100.85.x, 172.212.174.x 등.
- **노이즈 교훈**: 본인 옛 IP <HOME_IP_PREV>도 distinct-path 163으로 "스캐너"처럼 잡힘(grafana/metabase 자산 404). → **웹 탐지도 known-good 제외 필수**. (User-Agent `Shodan` 등은 caddy.request.headers 추가 파싱 시 신호 강화 가능.)
- 룰화: query `log_source:caddy AND http.status>=400 NOT known-good` → source.ip별 distinct url.path ≥ N → 알럿.

## 시나리오 1 — 임계값(5분/20회) 근거 (분포 분석, 2026-06-25)
각 외부 IP의 *peak 5분 실패횟수* 분포(샘플 2000 IP):
```
 1-2:  409   3-5: 1222   6-10: 321   [11-19: 14 ←골짜기]   20-49: 22   50-99: 4   100+: 8
중앙값 4 / 상위10% 6 / 최대 170
```
- **핵심 재프레이밍 — 임계값은 "공격 판별선"이 아니라 "알림 경제성 선"**: 이 서버는 단일 사용자 + 키 인증이라 *정상적으로 비번 틀리는 유저가 없음* → 실패하는 IP는 사실상 거의 다 공격자. 그래서 문턱은 "공격이냐 아니냐"가 아니라 **"어디까지 알림으로 받을 가치가 있느냐"**를 정한다.
- 낮추면 탐지 폭증: ≥6회만 369개, 더 낮추면 천 단위 → 알림 과다로 무뎌짐(관제 최대 적). ≥20회는 34개로 *행동 가능*. **20은 자연 골짜기(11-19=14개)에 위치** → 자의적 아님. 97%(≤10회) 저속 정찰과 20+(34개) 공격적 자동봇 사이의 빈 골짜기.
- (다중 사용자 시스템이라면 "정상 유저 오탐 방지"가 추가 근거지만, 단일 사용자 박스에선 *알림 볼륨 관리*가 본질. 위협 모델에 따라 근거가 달라진다는 점이 포인트.)
- **한계 → 보완 완료**: 20/5분은 *"시끄러운 무차별 대입"*만 잡고 저속은 통과 → **시나리오 4(저속·계정 스프레이 룰)로 실제 보완**(2026-07-01, 라이브).
- 기술 단서: `fixed_interval` 5분창은 시계정렬이라 경계 폭주를 과소집계 → Kibana 슬라이딩 룩백이 더 엄격.
- 결론: **임계값을 *분포 보고* 정했고 한계도 명시** = 데이터 기반 의사결정.

## 시나리오 1 — 실제 Kibana 룰 + 알림큐 운영 (2026-06-25)
로직 검증을 넘어 **운영 룰**로 격상:
- **Index 커넥터** → `security-alerts` 인덱스 = **알림 큐**(SIEM 핵심: 알림=문서, 트리아지).
- **ES query 룰**(`.es-query`, Kibana Alerting): query `event.outcome=failure NOT known-good`, **group by source.ip**, **threshold ≥20 / 5분**, 1분마다 평가. 발화 시 액션 → 큐에 alert doc(`source_ip`,`failure_count`,`severity:high`).
- 상태: enabled, threshold [20], status active.

**트러블 & 해결**
- 커넥터 생성 500 → Kibana Alerting은 `xpack.encryptedSavedObjects.encryptionKey` **필수**. compose에 env 추가 + 재생성(템플릿/inventory도 동기화, vault 대상).
- 룰 생성 400 `missing frequency` → 액션에 `frequency:{notify_when:onActiveAlert}` 추가.

**End-to-end 발화 검증**: 임계값을 임시 2로 낮춰 → 라이브 공격자(92.118.39.x)에 발화 → `security-alerts` 큐에 alert doc 3건 적재 확인 → **임계값 20 복원 + 데모 doc 삭제**. = 탐지→알림큐 파이프라인 실작동 증명.

시나리오 1·2는 라이브 Kibana 룰로 가동 + 포워더가 Slack 전송(시나리오 2는 "의심 성공 로그인 — 침해 가능성" 별도 카드, MITRE T1078). 시나리오 3(웹 스캐너)은 동일 패턴으로 확장 가능.

## 시나리오 4 — 저속·계정 스프레이 (라이브 룰, 2026-07-01)
- **왜 추가**: fail2ban(5분 5회 차단)과 시나리오1 알림(5분 20회)은 **버스트 기반**이라, 일부러 느리게 두들기는 공격을 통째로 놓친다. 실측(2026-07-01) — `event.outcome=failure` 상위 IP의 **peak5m가 1~3에 불과**한데 한 IP가 **10~30개 계정**을 시도(`45.198.224.x` 30계정·peak1, `45.205.1.x` 20계정·peak1, `2.57.121.x`·`9.223.176.x` 등 20계정). = rate 기반 방어를 회피하는 저속 credential spraying → 당시 **완전 미탐지**.
- **로직**: `source.ip`별 **12시간 창 `event.outcome=failure` ≥ 10회**(known-good·사설 제외). 버스트가 아니라 *장기 누적*을 봄 → 버스트 방어가 못 보는 축.
- **라이브 룰**: `.es-query`, group by source.ip, threshold ≥10 / 12h, 30분 평가. 액션 → `security-alerts`에 `detection: low_and_slow` 태그 기록. 포워더가 **"저속·계정 스프레이 의심"** 카드(MITRE **T1110.003 Password Spraying**)로 분기.
- **설계 통찰(정직)**: inline 차단(fail2ban)을 넣으면 *그보다 높은 임계값의 탐지 알림은 구조적으로 중복*이 된다(차단이 먼저 잘라냄). 그래서 탐지의 가치는 **차단이 못 보는 신호**(저속·다계정·성공 로그인)로 옮겨야 한다 — 이 룰이 그 이동의 실천. (fail2ban·20룰이 조용한 건 "공격이 없어서"가 아니라 "요즘 공격이 저속·정찰형"이기 때문 — 실측으로 확인.)

## SOC 대시보드 + 알림 큐 (2026-06-25)
운영 우선 SOC 대시보드 구성(Kibana saved objects API로 생성):
- **상단 메트릭**: 침해 의심(외부 성공 로그인)=0 · 고유 공격 IP · 탐지된 brute-force=34
- **중앙(centerpiece) — 탐지 알림 큐(datatable)**: IP·5분 최대 실패·국가·ASN·심각도. 209.38.23.x(170회/호주/DigitalOcean) 최상단.
- **공격 추이**(성공 vs 실패 일별), **성공 로그인 출처(접근 감사)**, **타겟 호스트**(minipc 32.5k vs oracle 7.5k), 노린 계정, 출처 ASN(맥락).
- 시간범위 2026-02-01~now 고정(데이터 시작점), 노이즈필터는 공격 패널 query에 개별 적용.

**알림 큐 백필(정직 표기)**: 라이브 룰은 임계값 20이라 평상시 큐가 비어 데모/스샷 임팩트가 없음 → **과거 데이터에 brute-force 룰을 소급 적용(백테스트)**해 실제 탐지 34건을 `security-alerts` 인덱스에 적재. 각 doc에 `detection: historical backtest` 표기. 라이브 룰 발화 시 신규 알림 append. (SIEM의 historical rule run과 동일 — "실시간"이라 과장 금지.)

**왜 SOC스러운가**: vanity(Top국가)가 아니라 *대응 가능한* 지표(탐지 큐·침해여부·추이·타겟자산)를 위로. 확장 방향: 다중 로그소스 상관 + 케이스 관리 + 위협 인텔.
