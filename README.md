# 보안 로그 분석 파이프라인 (ELK 기반 미니 SIEM)

> 내가 운영하는 공개 서버 2대(홈서버 + Oracle Cloud)에 실제로 들어오는 **공격 로그(SSH·웹)를 수집·정규화·시각화·탐지·실시간 알림**하고, 공격자 시점으로 **호스트를 하드닝**하고, 외부 **위협 인텔**까지 연동한 self-managed 보안 로그 분석 파이프라인.
> *합성 데이터가 아니라, 진짜 내 서버에 들어오는 공격 10만+ 건으로.*

## 한눈에
공개된 서버는 매일 전 세계에서 자동 침입 시도를 받습니다. 그 기록(로그)이 그냥 쌓이기만 하던 것을 → **한곳(Elasticsearch)에 모아 정리하고, '어느 나라·어느 호스팅업체에서 왔나'를 붙여(GeoIP·ASN) 지도·그래프로 보고(Kibana), '이건 공격이다'를 자동 판별(탐지 룰)해 Slack으로 알리고, 외부 위협 인텔로 검증**하는 관제 시스템으로 만들고, 이 전체를 다른 서버에도 재현하도록 코드화(Ansible)했습니다.

## 아키텍처
```
[홈서버]  journald(ssh,caddy) → Filebeat ─WireGuard 사설터널─┐
                                                            ├→ [Oracle] Elasticsearch ← Kibana(SOC 대시보드)
[Oracle]  journald(ssh,caddy) → Filebeat → localhost ───────┘     ↑ ingest: grok + GeoIP + ASN + event.outcome + 위협인텔 enrich
                                                                  ↓ 탐지 룰(Alerting) → security-alerts
                                                                  ↓ 포워더(systemd) → Slack(심각도·MITRE·평판)
```
- 이기종 2노드(ARM/Oracle + x86/홈서버). ES 포트는 비공개(localhost + WireGuard만).

## 핵심 결과 (실데이터)
- **10만+ 보안 이벤트** 단일 인덱스 통합 (SSH·웹). 고유 공격 IP **3,146개 / 89개국**.
- **공격의 ~34%가 DigitalOcean 한 곳**(클라우드 VPS 남용) — 개별 IP로는 안 보이던 신호를 ASN 집계로 발견.
- **외부 성공 로그인 0건.** 그리고 그게 운이 아니라 *구조*임을 규명 — 비밀번호 인증을 꺼둔 서버는 brute-force 성공이 불가능.
- **위협 인텔 교차검증**: 상위 공격자 200개 중 **50%가 글로벌 상습범**(누적 신고 140만 건), **27%는 미신고** → "평판은 보강이지 대체가 아니다".

## v1 → v2 (반복·성숙)
- **v1 (Phase 1~3 + 하드닝)** — 내 서버 로그라는 *내부 신호*만으로 수집·파싱·탐지·대응·알림. 공격자 시점 점검으로 실제 구멍(미니PC 비밀번호 인증)을 차단.
- **v2 (Phase 4)** — *"내가 본 것만 안다"*는 한계를 인식하고, 외부 위협 인텔(AbuseIPDB)을 연동해 신규/상습을 구분하고 판정을 외부와 교차검증.

## 기술 스택
Elasticsearch · Kibana · Filebeat · ES ingest pipeline(grok/GeoIP/ASN) · ES enrich(위협인텔) · Kibana Alerting · AbuseIPDB · Slack · Ansible · WireGuard · Docker · Linux(systemd/journald)

## 어떻게 동작하나
1. **수집** — 양쪽 호스트의 SSH·웹 로그를 journald → Filebeat로 ES 전송
2. **정규화** — grok으로 `source.ip`·`user.name` 추출 (자유 텍스트 → 필드)
3. **인리치** — 국가·좌표·**ASN**·`event.outcome`(success/failure/probe) + **위협 인텔 평판** 부착
4. **시각화** — Kibana SOC 대시보드(침해/추이/타겟 자산 우선의 operational 지표)
5. **탐지** — 룰로 자동 판별(5분 20회↑) → `security-alerts`
6. **알림** — 포워더(systemd)가 Slack 전송. 심각도는 *현업 신호*(성공→critical / 권한계정·집요→high)로 차등, MITRE ATT&CK 태깅, 공격자 평판 표시
7. **대응** — 공격자 시점 점검 → SSH 비밀번호 인증 차단(키 전용)
8. **배포 자동화** — 설정 배포를 Ansible playbook으로 코드화(멱등)

## 탐지 시나리오 (→ `docs/detection-scenarios.md`)
1. **SSH brute-force** — 5분 20회 임계값을 *분포 분석*으로 도출. *단독 알림은 fail2ban(5/5분) 차단 도입 후 **비활성화** — 버스트는 fail2ban이 전담, 임계값 분석은 탐지선 설계 사례로 유지*
2. **known-good 아닌 성공 로그인** — IP가 아니라 *인증방식+계정+키지문* 기준 (동적 IP는 신뢰 못함)
3. **웹 스캐너** — 한 IP가 여러 경로 + `/.env`·`/.git/config` 등 민감경로 + 404 폭증
4. **저속·계정 스프레이** — 한 IP가 12h 내 여러 계정을 저속(≥10회)으로 시도 → *버스트 방어(fail2ban·brute-force룰)가 못 보는 축*을 탐지 (MITRE T1110.003)

## 주요 의사결정 (→ `docs/decisions.md`, D1~D28)
왜 ES(검색)·왜 Filebeat(리소스)·왜 journald(샌드박스)·왜 ASN(카디널리티)·왜 임계값 20(분포)·왜 Slack을 포워더로(라이선스)·왜 심각도를 신호로·왜 위협인텔 2계층… "왜 X 대신 Y"를 전부 기록.

## 한계 & 다음 (정직하게)
- ES/Kibana **xpack 인증 활성화 완료** (+ Caddy basic_auth 2겹). HTTP TLS는 추후 과제
- **탐지 vs 예방 정리**: brute-force 20/5분 알림은 fail2ban(5/5분)과 같은 *버스트* 영역 → 차단이 20 전에 선점하니 단독 알림은 중복 → **비활성화**. 탐지의 무게는 **차단이 못 보는 축**(성공 로그인=침해, 저속·계정 스프레이=시나리오 4)으로 이동. (inline 차단을 넣으면 higher-threshold 탐지 알림은 구조적으로 중복이 된다는 실증.)
- 자동 차단(fail2ban) **적용 완료** — SSH 5분 5회→자동 IP 차단(알림 임계값 20보다 공격적 — "알림받을 가치"와 "차단할 가치"를 의도적으로 분리), 허용목록으로 lockout 방지. (웹 2차 인증 basic_auth도 적용 완료)
- 위협 인텔은 단일 피드 → 다피드 교차로 신뢰도 향상 여지

## 문서
- `docs/summary.md` — 한 장 요약
- `docs/decisions.md` — 기술 선택 근거(D1~D28)
- `docs/phase-0~2-*.md` — 수집·파싱·시각화·Ansible 구축 기록(트러블 포함)
- `docs/phase-3-slack-alerting.md` — 탐지→Slack 알림·심각도 설계
- `docs/phase-4-threat-intel.md` — 외부 위협 인텔(AbuseIPDB) 연동 (v2)
- `docs/host-hardening.md` — 공격자 시점 점검·하드닝
- `docs/detection-scenarios.md` — 탐지 룰·임계값 근거
- `scripts/` — SOC 포워더·위협인텔 갱신 Python (시크릿은 환경변수, 코드 미포함)

## 실행 (Ansible)
```bash
cd ansible
cp inventory.example.ini inventory.ini   # 자기 환경에 맞게 채우기 (비밀은 ansible-vault)
ansible-playbook deploy-elk.yml --check   # dry-run
ansible-playbook deploy-elk.yml           # 적용 (멱등: 재실행 시 changed=0)
```

## 스크린샷
> Kibana·Slack·서버 캡처. **자기 IP·도메인·계정·호스트명은 가림 처리.** (이미지: `docs/img/`)

**핵심 (실데이터)**
- `soc-dashboard.png` — Kibana SOC 관제 대시보드 (침해·추이·타겟 자산·지도)
- `attack-map.png` — 공격 출발지 세계 지도 (국가별 공격량)
- `top-asn.png` — 공격 출처 ASN Top (DigitalOcean ≈ 34%)
- `slack-bruteforce.png` — Slack brute-force 탐지 카드 (심각도·MITRE)

**탐지 · 대응 · 하드닝**
- `slack-suspicious-success.png` — known-good 아닌 성공 로그인 알림(침해 가능성)
- `fail2ban-ban.png` — fail2ban 자동 차단 (iptables)
- `es-auth.png` — ES/Kibana xpack 인증 적용

**v2 · 자동화**
- `slack-threat-intel.png` — 위협 인텔(AbuseIPDB) 붙은 탐지 카드
- `reputation-dist.png` — 상위 공격자 평판 분포 (50% 상습범·27% 미신고)
- `ansible-recap.png` — Ansible 멱등 배포 (PLAY RECAP, changed=0)
