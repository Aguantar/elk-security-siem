# Phase 1b — 파싱 · GeoIP · Kibana 노출 (2026-06-24)

> Phase 1a(로그 적재)에 이어, 자유텍스트 로그를 **구조화 + GeoIP 인리치**하고 단일 인덱스로 정리, Kibana를 공개 도메인으로 노출.

## 한 일

### 1. Ingest pipeline `seclogs-parse` (grok + geoip)
- SSH 메시지에서 `source.ip`·`user.name`·`source.port` grok 추출(여러 패턴, `ignore_failure`).
- `geoip` 프로세서로 `source.ip` → `source.geo`(country/continent/location). GeoLite2 DB는 ES geoip 다운로더가 자동 취득(GeoLite2-City/Country/ASN).

### 2. 데이터스트림 템플릿 `seclogs-tmpl` (priority 500, `seclogs-*`)
- `data_stream:{}`, `index.default_pipeline=seclogs-parse`, `number_of_replicas:0`.
- 매핑: `source.ip`=ip, `source.geo.location`=**geo_point**(지도 필수), `user.name`/`country_name`/`host_name`/`log_source`=keyword.

### 3. 단일 인덱스 `seclogs-live`로 통합 + 과거 reindex
- Filebeat(양쪽) 출력 인덱스를 날짜기반 → **정적 `seclogs-live`**(데이터스트림, ILM 롤오버 가능)로 변경 → **샤드 스프롤(140개+) 해소**.
- 기존 93,783건을 `_reindex`로 pipeline 통과시켜 seclogs-live에 적재(파싱+GeoIP 소급). 36초, 실패 0. 구 데이터스트림 삭제.

### 4. Kibana 공개 노출 (Caddy + basic auth)
- `kibana.example.com` → Oracle Caddy `reverse_proxy 127.0.0.1:5601`.
- Kibana 자체 보안 off라 **Caddy basic_auth로 보호**(bcrypt). Kibana `SERVER_PUBLICBASEURL=https://kibana.example.com`.

## 검증 — 실제 보안 분석 동작 (seclogs-live, SSH)
- **Top 공격국**: 네덜란드 5,858 · 미국 4,143 · 중국 3,007 · 호주 1,999 · 루마니아 1,991 · 인도 1,871
- **Top 공격 IP**: 209.38.23.x(680) · 167.71.235.x(640) · 2.57.121.x(579) · 213.209.159.x(565)
- **Top 노린 계정**: root 6,265 · admin 3,199 · ubuntu 1,806 · user 1,750 · oracle 710
- 총 93,785건, geo_point 매핑 → Kibana 지도 가능.

## 트러블 & 결정
- **비-데이터스트림 템플릿 생성 실패**: 기존 seclogs-* 가 이미 데이터스트림이라 충돌 → 템플릿도 `data_stream:{}` 형식으로 맞춤.
- **샤드 스프롤**: 인덱스명에 이벤트 날짜를 넣어 일자별 140개+ 생성됨 → 정적 단일 인덱스로 전환해 해소(decisions D11).

## 사용자 액션
- **가비아 DNS**: `kibana` A레코드 → `<ORACLE_IP>`(Oracle). 완료(2026-06-24).
- **미니PC Filebeat → seclogs-live 전환**(sudo): `sudo sed -i 's#index: "seclogs-minipc-.*"#index: "seclogs-live"#' /etc/filebeat/filebeat.yml && sudo systemctl restart filebeat`

### 왜 미니PC Filebeat 출력을 seclogs-live로 바꿨나 (기록)
- 과거 이력은 reindex로 이미 seclogs-live에 통합됨(미니PC 68k + Oracle 25k). Oracle Filebeat의 *신규* 출력도 seclogs-live로 전환했음.
- 그러나 미니PC Filebeat는 **신규 로그를 여전히 옛 패턴(`seclogs-minipc-<날짜>`)으로** 보내도록 남아 있었음 → 방치 시 ① 일자별 인덱스 **샤드 스프롤 재발**, ② 미니PC **실시간 공격이 seclogs-live에서 누락**되어 단일 대시보드가 미니PC 신규 이벤트를 못 봄.
- 그래서 미니PC도 동일 `seclogs-live`로 출력 통일 → 양쪽 호스트의 *과거+실시간*이 한 인덱스에 모여 대시보드 하나로 전체 관제.
- **보안 영향 없음**: 출력 인덱스명만 변경하는 로컬 설정 + filebeat 재시작. 포트/인증/노출 변화 없고, Filebeat→ES는 WireGuard 사설 터널로만 전송. 재시작은 registry 기준 재개라 유실 없음.

## 다음 (Phase 1 마무리 → Phase 2)
- Kibana 데이터뷰 `seclogs-live` 생성 → 대시보드(국가지도/Top IP/계정사전/시간대) + **탐지 시나리오**(brute-force, 스캐너).
- Caddy(JSON) 웹로그도 client_ip 파싱(현재 SSH 위주).
- Phase 2: 전체 배포를 Ansible 플레이북화.

## 분석 인사이트 (2026-06-24)

### 1. 성공 로그인은 전부 known-good (침해 0)
- `Accepted` SSH 로그인의 출처 IP는 **전부 자기/내부망**: `<HOME_IP>`(미니PC 집 IP, `ssh oracle` 세션 109건), docker 172.x, LAN 192.168.x.
- 즉 **공격자가 성공한 로그인 0건** — 3만+ brute-force가 모두 실패. "성공 로그인은 모두 known-good IP" = 방어 정상의 증거.
- **탐지 시나리오 직결**: "known-good 목록에 없는 IP의 *성공* 로그인" = 침해 레드알럿. 현재는 전부 정상.

### 2. 노이즈(자기 트래픽) 필터링
- 대시보드/Top-IP 집계 시 자기·내부 IP를 제외해야 순수 외부 공격이 보임. KQL:
  `NOT source.ip : ("<HOME_IP>" or "<WG_SUBNET>/24" or "172.16.0.0/12" or "192.168.0.0/16" or "127.0.0.0/8")`
- 의미: "관리 IP를 필터링해 노이즈 제거 후 실제 공격만 분석" — SOC 기본기.

### 3. 파싱 커버리지 — 보강 적용 (2026-06-24)
초기 source.ip 보유 32,516/94,531(~34%)였고 두 누락을 보강함:
- **개선 ① Caddy JSON 파싱**: caddy access 로그는 JSON이라 `json` 프로세서로 파싱 후 `set copy_from`으로 `source.ip`(=request.client_ip), `url.path`(uri), `http.method` 추출.
- **개선 ② SSH grok 패턴 추가**: `Connection (reset|closed) by ... <IP>`, `by (invalid|authenticating) user <user> <IP>`, `Received disconnect from <IP>` 등 추가 → 누락 IP 회수.
- 파이프라인 교체(`seclogs-parse` v2) + `_update_by_query?pipeline=seclogs-parse`로 기존 94,554건 재처리(33초, 충돌 0).

**결과 (커버리지)**:
| | 전 | 후 |
|---|---|---|
| SSH source.ip | 32,516 | 58,932 |
| Caddy source.ip | 0 | 1,680 |
| 합계 | 32,516 | **60,612** |

- 남는 빈값은 정상: SSH 세션 라인(`pam_unix session`)·Caddy의 cert/runtime 로그(`http.log.access`가 아닌 TLS/ACME 이벤트)는 IP가 원래 없음. → "수집은 완전히, 파싱은 IP 있는 라인만, 노이즈는 필터" 원칙.
- 신규 데이터는 `index.default_pipeline`으로 자동 동일 파싱.

**보강 v3 (2026-06-24)**: `Connection (reset|closed) by (authenticating|invalid) user <X> <IP>`, `Disconnected from ... user <X> <IP>` 패턴 추가 → 이 라인들의 *계정명* 회수. SSH user.name 보유 **28,875 → 51,502**. (root brute-force가 reset으로 끊긴 케이스가 다수였음.)

### 4. 집계 단위 = 카디널리티에 맞춤 (ASN 인리치)
- SSH 고유 공격 IP **3,146개**, Top10 집중도 **15.3%**, 고유 국가 89 → 개별 IP "Top" 차트는 롱테일이라 저신호.
- 대응: **ASN(호스팅업체) 인리치 추가**(geoip GeoLite2-ASN → `source.as.organization_name`).
- 결과: **DigitalOcean 20,131건(~34%)**, Unmanaged Ltd 3,475, Techoff 3,036 ... → 한 단계 올리니 신호 명확.
- 권장 패널: 고유IP수(규모) · 국가/ASN(어디서) · 시간추세 · 노린계정 · 성공vs실패. Top IP는 "차단 후보 명단"으로 용도 한정.

### 5. event.outcome 분류 + 정합성 체크 (2026-06-25)
SSH 이벤트를 `event.outcome`(success/failure/probe)로 분류(script processor). 분포: **failure 39,870 / probe 35,178 / success 179**.
- 트러블: caddy 런타임/인증서 로그(비-access)의 문자열 `status`가 `http.status`(long)에 들어가 파싱 실패→update_by_query 중단. 수정: http.status는 `caddy.request != null`(access 로그)일 때만 추출. 재처리 98,567건 전량 성공.

**정합성 감사 (필드 빈값 = 의미적 정상인지 검증):**
- SSH: has_ip 72%/geo 72%/asn 72%/user 63%/outcome 90% — geo·asn은 ip를 거의 그대로 따라옴(차이=사설IP). outcome 미분류 10%=세션/pam 라인(정상).
- Caddy: has_ip=url=32%(=access 로그 비율), user 0%(웹엔 SSH계정 없음, 정상), status 29%.
- ip有/geo無 = 사설IP(192.168.x·172.x)가 대부분(정상). 공인 2개(185.196.x)는 GeoLite2 미수록(커버리지 갭, 무해).
- caddy url有/status無 = `reverse_proxy aborting` 로그(request는 있고 status 없음, 정상).

**발견 — 탐지 시나리오 실증:** `event.outcome=success` 179건의 출처 IP가 전부 known-good(<HOME_IP>·docker·LAN)인데 **`<HOME_IP_PREV>`(3건)** 1개가 목록 밖. 조사 결과 `Accepted publickey for <운영계정>`(본인 계정+본인 키, minipc, 2026-02-03, 국내 가정용 회선) = **본인이 다른 네트워크에서 접속**. 침해 아님 → known-good에 추가 대상.
- 의미: "known-good 아닌 IP의 *성공* 로그인" 룰이 실제로 1건을 잡아냄 → 조사 → 본인 확인 → 화이트리스트. (실제 침해였으면 레드알럿이 되는 바로 그 시나리오.)

### 6. known-good 갱신 + 동적 IP 교훈 (2026-06-25)
- `<HOME_IP_PREV>`(본인 2026-02-03 접속, 당시 집 IP) known-good 추가. 노이즈필터 KQL:
  `NOT source.ip : ("<HOME_IP>" or "<HOME_IP_PREV>" or "<WG_SUBNET>/24" or "172.16.0.0/12" or "192.168.0.0/16" or "127.0.0.0/8")`
- **교훈 — 주거 IP는 동적**: 내 집 공인 IP가 2026-02(<HOME_IP_PREV>)→현재(<HOME_IP>)로 ISP 재할당됨. **정적 IP 화이트리스트는 취약**(유지보수 필요).
- **더 견고한 known-good = 인증방식+계정**: success 179건 전부 `publickey`+알려진 계정(<운영계정>/ubuntu). → "password 성공" 또는 "모르는 계정 성공"을 알럿하는 게 IP 목록보다 안 깨지는 탐지 기준. (이게 다음 탐지룰 설계의 근거.)
