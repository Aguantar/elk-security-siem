# 보안 로그 분석 파이프라인 (ELK SIEM) — 한 장 요약

> 이 문서 하나로 전체 그림이 잡히게. 상세는 `decisions.md`, `phase-*.md`.

## 한 줄
내가 운영하는 공개 서버 2대(홈서버+Oracle ARM)의 **실제 공격 로그(SSH·웹)를 Elastic Stack으로 수집·정규화·시각화·탐지·실시간 알림(Slack)**하고, 공격자 시점 하드닝과 외부 **위협 인텔(AbuseIPDB)** 연동까지 한 **self-managed 보안 로그 분석 파이프라인(SIEM)**. 배포는 Ansible로 자동화.

## 왜 (동기)
데이터 엔지니어링 직무에서 자주 요구되는 Elastic Stack·Ansible을 *직접 해봄*으로 채우되, **보안 도메인**에 맞게 — 토이가 아니라 **실제 공격받는 내 서버 로그**로. (보안 관심은 이미 vibescan(시크릿 스캐너)으로 있었고, 블로그 이상 트래픽이 계기.)

## 아키텍처
```
[홈서버] journald(ssh,caddy) → Filebeat ─WireGuard 사설터널─┐
                                                            ├→ [Oracle] Elasticsearch ← Kibana(SOC 대시보드)
[Oracle] journald(ssh,caddy) → Filebeat → localhost ───────┘     ↑ ingest: grok + GeoIP + ASN + 위협인텔 enrich
                                                                 ↓ 탐지 룰 → security-alerts → 포워더(systemd) → Slack
```
- 이기종 2노드, ES는 여유 있는 Oracle(12GB)에, 포트는 비공개(127.0.0.1+WireGuard).

## 핵심 수치
- **10만+ 보안 이벤트** 단일 인덱스(`seclogs-live`)에 통합 (SSH·웹).
- SSH brute-force **고유 공격 IP 3,146개 / 89개국**.
- **DigitalOcean 한 곳이 SSH 공격의 ~34%**(20,131건) — IP로는 안 보이던 신호.
- 노린 계정: root·admin·ubuntu·oracle. **외부 성공 로그인 0건**(전부 known-good IP) = 방어 정상.
- **위협 인텔 교차검증**: 상위 공격자 200개 중 50%가 글로벌 상습범(누적 140만 건), 27% 미신고.

## 생각의 흐름 (수집→파싱→인리치→집계→인사이트)
```
"Failed password ... from 209.38.23.x"   (텍스트)
 → grok 파싱:  source.ip, user.name
 → 인리치:     +국가 +좌표 +ASN(DigitalOcean)
 → 집계:       IP(롱테일 15%, 약함)  →  ASN(DigitalOcean 34%, 신호)
 → 인사이트:   "공격의 1/3이 한 클라우드 임대서버. 집계 단위를 카디널리티에 맞춰야 신호가 보인다."
```

## 핵심 의사결정 (상세=decisions.md)
- **ES vs ClickHouse**: 로그 검색·보안UI엔 역색인 ES. (분석은 ClickHouse 유지 — 용도 분리)
- **Filebeat vs Logstash**: 12GB 제약 → 가벼운 Filebeat, 변환은 ES ingest pipeline.
- **journald 단일 소스**: Caddy 파일로그가 systemd 샌드박스에 막혀 stdout→journald로 통일.
- **집계 단위 = 카디널리티**: IP 3,146개라 ASN으로 올려 신호 확보(D15).
- **노이즈 필터링**: 내 관리 IP(<HOME_IP> 등) 제외 → 순수 공격만. "정상/공격 구분"이 SOC 기본기.

## 완료된 단계 (Phase)
- **Phase 1**: 수집(Filebeat/journald)·파싱(grok)·인리치(GeoIP/ASN)·시각화(Kibana SOC 대시보드).
- **Phase 2**: 전체 배포를 Ansible 플레이북으로 자동화(이기종 2노드, 멱등성).
- **Phase 3**: 탐지(Kibana 룰, 5분 20회↑)→`security-alerts`→**Slack 실시간 알림**(systemd 포워더). 심각도는 현업 신호 차등(성공→critical / 권한계정·피크100+→high), MITRE T1110 태깅, 표적 서버 표시. (`phase-3-slack-alerting.md`)
- **호스트 하드닝**: 공격자 시점 점검 → 미니PC가 최대 피격·SSH 비번 문 열림 발견 → 비번 인증 차단(키 전용). 실질 노출면이 웹(개발용 IDE)으로 이동했음을 식별. (`host-hardening.md`)
- **Phase 4 (v2): 외부 위협 인텔 연동** — AbuseIPDB로 우리 공격자 평판 교차검증(상위 200개 중 50% 신고 이력·누적 140만 건, 27% 미신고). ES enrich 벌크 태깅 + 알림시 per-IP 조회 + 일일 갱신 타이머. v1의 "내가 본 것만 안다" 한계를 외부 대조로 극복. (`phase-4-threat-intel.md`)

## 한계 (정직하게) → 다음
- ES/Kibana **xpack 네이티브 인증 활성화 완료** (+ Caddy basic_auth 2겹). HTTP TLS는 추후.
- 탐지룰은 "시끄러운" 임계값형만 — *느린(low & slow)* 공격은 통과. 별도 룰 보완 필요.
- 자동 차단(fail2ban) **적용** — SSH 5분 20회→IP 차단(SIEM 룰과 동일 임계값) + 허용목록 lockout 방지. (차단은 IP/CIDR 기준, 지오는 분석용)
- 위협 인텔은 단일 피드(AbuseIPDB) — Spamhaus·GreyNoise 다피드 교차 시 신뢰도 향상 여지.
- 웹 노출면(개발용 IDE): basic_auth 2차 방어 **적용 완료**(무인증 401 차단, IDE 로그인과 2겹).

## 예상 질문과 답
- *왜 Logstash 안 썼나?* → 리소스 제약 + 변환 불필요(ingest pipeline).
- *인덱스 설계는?* → 단일 데이터스트림 seclogs-live, host_name·log_source로 분리, ILM 롤오버(D11).
- *왜 ASN을 봤나?* → IP는 봇넷이 분산(3,146개, top10 15%)이라 호스팅업체로 묶으니 DigitalOcean 34%.
- *데이터 유실/중복은?* → Filebeat registry로 at-least-once, journald 기준 재개.
- *12GB에서 ES 안 죽게?* → 힙 1g cap, single-node, 무스왑이라 OOM 방지.

## 비전문가용 설명
**엘리베이터(30초)**: "인터넷에 연결된 제 서버에 매일 전 세계에서 자동 침입 시도가 들어옵니다. 그 기록을 한곳에 모아 정리하고, '어느 나라·어느 업체에서 왔나'를 붙여 지도·그래프로 보여주고, '이건 공격이다'를 자동으로 가려내는 **보안 관제 시스템**을 만들었습니다. 그리고 이 시스템을 다른 서버에도 명령 한 줄로 **똑같이 복제**할 수 있게 코드로 자동화했습니다."

**비유**: CCTV 영상이 그냥 쌓이기만 하던 걸 → **관제실 모니터 + 자동 경보 + 세계지도 + 자동 시공 설계도**로 바꾼 것.
- 모으기(수집) → 정리(파싱) → 정보 붙이기(GeoIP/ASN) → 보여주기(대시보드) → 가려내기(탐지룰) → 복제(Ansible)

**실데이터(진짜라는 증거)**: 한 달 침입 시도 3만+, 가장 노린 계정 admin·root, 공격의 1/3이 DigitalOcean 클라우드, 89개국, 한 IP는 5분에 170번 — **실제 뚫린 적은 0번**.
