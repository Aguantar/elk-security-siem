# Phase 3 — 탐지 알림(Slack) 자동화

> 목표: brute-force 탐지를 실시간으로 Slack에 통보. "탐지만 하고 대응이 없으면 반쪽"을 보완하는 단계.

## 1. 제약 발견 — Kibana 무료 라이선스는 Slack을 못 쏜다

- Kibana 커넥터 타입을 조회(`GET /api/actions/connector_types`)한 결과, **basic(무료) 라이선스에서 enabled인 건 `.index`와 `.server-log` 둘뿐.**
- `.slack`, `.webhook`, `.email`, `.teams`, `.pagerduty` 등 **알림 커넥터는 전부 Gold+ 유료.** → Kibana 자체로는 Slack 푸시 불가.
- 선택지: (A) 경량 포워더 자체 제작 / (B) 유료 트라이얼(30일 후 만료) / (C) Slack 포기.
- **(A) 채택.** "라이선스 제약을 포워더로 우회" = 파이프라인 이해도를 보여주는 포인트.

## 2. 기존 자산 조사 — 크립토 파이프라인은 어떻게 알림을 쏘나

- 같은 서버의 CDC 크립토 파이프라인이 이미 Slack 알림을 운영 중이라 메커니즘을 조사.
- 결과: **Airflow가 쏜다.** `cdc-realtime-pipeline/airflow/plugins/callbacks/slack_callbacks.py`
  - webhook URL은 코드에 박지 않고 **Airflow Variable `slack_webhook_url`** 에서 읽음.
  - Slack **Block Kit** 포맷으로 POST. `daily_pipeline` DAG(매일 01:00 KST)이 일일 리포트(이상탐지 건수 포함)를 #general에 전송.
  - 이상탐지 자체는 Flink가 실시간으로 `ClickHouse.anomaly_alerts`에 적재 → Airflow가 하루 1번 묶어서 통보하는 구조.
- 재사용 판단: **Airflow(미니PC)에 SOC DAG를 얹는 대신, 오라클에 독립 포워더**를 둔다.
  - 근거: ELK/SOC 스택이 전부 오라클에 있어 ES를 로컬로 읽는 게 깔끔. Airflow 경유는 사설 터널(WireGuard) 건너야 하고 크립토 프로젝트와 결합됨.
  - 단, **Block Kit 메시지 포맷은 차용**해 알림 톤을 통일. 채널·webhook은 SOC 전용으로 분리(크립토 알림에 보안 알림이 묻히지 않게).

## 3. 아키텍처 — 탐지와 전달의 분리

```
[탐지]  Kibana 룰 (.es-query, 1분 간격)
          IP별 5분 내 실패 >=20  → security-alerts 인덱스에 doc 기록
                                    (.index 커넥터, doc: @timestamp/rule/source_ip/failure_count/severity)
[전달]  SOC 포워더 (오라클, systemd 타이머 2분)
          security-alerts의 '새 doc'만 읽음 → 등급화 + 인리치 → Slack Webhook(SOC 채널)
```

- **단일 탐지 소스**: 임계값 판정은 Kibana 룰이 전담. 포워더는 '전달 + 등급화'만 → 로직 중복 없음.
- security-alerts 인덱스는 이제 **대시보드 알림 큐 + Slack 소스** 두 소비자가 공유.

## 4. 포워더 구현 (`/opt/soc-forwarder/forward.py`)

- **상태파일** `/var/lib/soc-forwarder/state.json`: `last_seen`(마지막 처리시각) + `ip_cooldown`(IP별 마지막 발송시각).
  - 최초 실행은 기준선만 잡고 백로그(과거 백필 34건 등) 미전송.
  - 과거 백필은 `detection:"historical backtest"`로 식별해 쿼리에서 제외.
- **중복 억제**: 같은 IP는 **쿨다운 60분** 내 재전송 안 함(진행 중 공격이 매분 도배되는 것 방지). 한 배치 내 같은 IP는 failure_count 최댓값으로 1건 집약.
- **인리치**: 발송 시 `seclogs-live`에서 해당 IP의 국가·ASN·성공여부·노린 계정·**표적 서버(host.name)**를 조회(대시보드와 동일 출처). 룰이 호스트를 안 가리므로(2노드 공용 인덱스) 같은 알림이 미니PC/오라클 어느 쪽 공격이든 동작하고, 카드의 "표적 서버"로 구분.
- **메시지**: 단일 세로 레이아웃 Block Kit(2열 fields는 가독성 나빠 폐기). 기본 이모지 미사용. 하단에 **MITRE ATT&CK T1110.001** 태그.
- **자기설명적 라벨**(직관성 개선): 숫자가 무엇 기준인지 카드 안에서 바로 읽히게 작성.
  - `공격 IP (국가) / 표적 서버(host.name + 친근한 이름: 오라클 클라우드·미니PC 홈서버)`
  - `공격 방식: 5분간 N회 로그인 실패 (탐지 기준 20회 초과)` — 횟수의 의미·탐지 사유 명시
  - `심각도: HIGH — <사유 한 줄>` (성공→"침해 의심" / 권한계정→"root/admin 표적" / 100회+→"집요한 대량 시도")
  - `악성 평판: 악성 확신도 0~100, 전 세계 N건 신고 (상습범/신고 이력/미신고)` — AbuseIPDB 점수가 '악성 확신도'임을 명시 (위협 인텔은 `phase-4-threat-intel.md`)
- **시크릿**: webhook URL은 `/etc/soc-forwarder.env`(권한 600, root)에만. 스크립트·git에 미포함. systemd `EnvironmentFile`로 주입.
- **systemd**: `soc-slack-forwarder.service`(oneshot) + `.timer`(OnUnitActiveSec=2분).

## 5. 심각도(severity) — 현업 신호 기반 차등

볼륨(횟수)은 보조 지표일 뿐, 등급은 실제 위험 신호로 매긴다.

| 조건 | 등급 | 근거 |
|---|---|---|
| 같은 IP에서 **성공 로그인** 흔적 | **critical** | 실패하다 성공 = 침해 의심 (1순위 신호) |
| **권한/실계정**(root·admin·administrator·ubuntu·oracle) 표적 또는 피크 100회+ | **high** | 권한 탈취 시 영향 큼 / 집요·지속 공격 |
| 그 외 (invalid user 위주, 20~99회) | **medium** | 표준 봇 |

- 참고 프레임워크: **Elastic Security**(severity low/med/high/critical + risk_score 0~100), **MITRE ATT&CK T1110**(Brute Force), **CVSS** 밴드 네이밍.
- 현업의 궁극은 탐지가 아니라 **원천 차단**(MFA·키전용·fail2ban). 실제로 본 인프라도 미니PC password 인증 OFF·오라클 키전용으로 brute-force 성공 자체를 구조적으로 차단함(→ 심각도 천장이 medium에 머무는 이유).

## 6. 검증

- 종단 테스트: 상태 기준선을 과거로 시드 → 실제 탐지(`193.163.187.x`)를 Slack에 릴레이 성공.
- 등급 차등 확인: 해당 IP는 `admin·ftpuser·oracle` 표적 → **HIGH**로 정확히 산정(성공 0건이라 critical 아님, 피크 47<100이나 권한계정 표적으로 high).
- 쿨다운 확인: 즉시 재실행 시 `sent=0`(중복 미발송).
- 타이머 가동 확인: `systemctl list-timers` 2분 주기 정상.

## 7. 배운 점 / 한계

- **필드명 함정**: `user.name`은 ECS상 `keyword`로 매핑되어 집계는 `user.name`으로 해야 함. `user.name.keyword`로 조회하면 빈 결과 → "데이터 없음"으로 오판하기 쉬움. (대시보드 "노려진 계정" 패널도 동일 필드 점검 필요 — 후속 과제)
- 포워더는 Kibana 룰이 살아 있어야 동작(룰=탐지, 포워더=전달). 룰 중단 시 알림도 멈춤.
- 알림 채널은 SOC 전용 1개. 다채널 라우팅(심각도별 분리 등)은 미구현.
