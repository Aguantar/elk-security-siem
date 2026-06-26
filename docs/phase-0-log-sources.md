# Phase 0 — 로그 소스 확보 (2026-06-24)

> **프로젝트**: 실서버 공격 로그 기반 보안 분석 파이프라인 (ELK SIEM).
> 설계/배경 문서: `~/elk_ansible_handoff.md`
> **이 문서 목적**: ELK에 넣을 "진짜 보안 로그"가 실재하는지 확인하고, 수집 가능하게 만든 기록.

## 목표
ES에 적재할 로그 소스가 실제로 공격 데이터를 담고 있는지 확인 → 부족하면 계측 활성화.

## 발견 — 데이터 충분 (실측)

| 소스 | 미니PC | Oracle(공인IP) | 상태 |
|---|---|---|---|
| SSH 인증 실패/침입시도 (journald) | **19,797건** | **13,924건** | 메인 소스 |
| iptables 거부 패킷 | — | 425건(79KB) | 보조 |
| Caddy HTTP 접속로그 | 기본 미기록 | 기본 미기록 | → 이번에 활성화 |

**SSH brute-force 합계 ~33,700건.** 보안을 신경 썼어도 공격 시도 자체는 계속 로깅됨(방어 성공 = 실패 로그가 데이터셋이 됨).

### 이미 보이는 공격 패턴 (war-story / 분석 시드)
- 최다 시도 계정명: `admin`(3,244), `user`(1,900), `debian`(359) — 전형적 사전 공격.
- 구체적 공격자 예: `193.32.162.x` 가 `deploy` 계정 반복 시도.
- → IP·계정·시각이 다 있어 GeoIP 부착 시 국가/시간대/Top-IP 탐지룰 도출 가능.

## 수행 — Caddy 접속로그 활성화

Caddy 기본은 per-request 로그를 안 남김(관리/TLS 이벤트만). 각 사이트에 `import accesslog` 스니펫 추가:
```
(accesslog) {
	log {
		output stdout
		format json
	}
}
```

### 핵심 트러블 & 결정 (재현 방지용 기록)
- 처음엔 **파일 출력**(`output file /var/log/caddy/access.log`)으로 시도 → **`permission denied`로 Caddy 기동 실패**.
- 원인: caddy.service에 **`ProtectSystem=full` 등 systemd 샌드박스**가 걸려 있어, caddy 소유 디렉토리(`/var/log/caddy`, `/var/lib/caddy`)조차 신규 파일 쓰기가 막힘. systemd 드롭인(`ReadWritePaths`)으로도 즉시 안 풀림.
- **결정: 파일과 싸우지 않고 `output stdout` → journald로 전송.** journald는 Filebeat journald input으로 그대로 읽을 수 있어 파이프라인엔 문제없음.
- 적용 시 **validate 후에만 reload**(원자적, 실패 시 무중단) 원칙 적용. Oracle은 restart 과정에서 한 번 다운됐다가(파일출력 실패) stdout 방식으로 복구 — 라이브 변경 시 reload 우선, restart 지양 교훈.

### 현재 상태
- **Oracle**: 적용 완료. access 로그가 journald(logger `http.log.access.log0`)로 적재 확인(remote_ip·method·host·uri 등 전체 필드). `/.env` 류 프로브도 캡처됨.
- **미니PC**: 적용 완료(2026-06-24). diff상 기존 사이트 설정 무변경(로그만 추가), validate 통과 후 reload(무중단). 적용 후 6개 사이트 정상(icepush/grafana/metabase/circuit 200, airflow 302) + access 로그 journald 적재 확인. 백업: `/etc/caddy/Caddyfile.bak.accesslog`.
  - 미니PC는 NAT 뒤라 외부 요청 시 실제 source IP 캡처 여부 Phase 1에서 검증 필요(헤어핀 NAT로 내부 테스트는 공유기 IP로 찍힘). 필요시 `trusted_proxies`/X-Forwarded-For 보정.

## 다음 (Phase 1)
- Oracle(12GB)에 Elasticsearch + Kibana 단일노드 docker-compose 기동 (힙 1g cap, single-node, xpack 초기 off).
- Filebeat로 **SSH journald(메인) + Caddy access journald** 수집 → ES 적재.
- Kibana에서 GeoIP·Top공격IP·계정사전·시간대 패턴 대시보드 + 탐지룰 1개.

## 메모
- ES 적재 소스가 **파일이 아니라 journald** 중심이 됨 → Filebeat journald input 사용(또는 journald→파일 export). Phase 1 설계 시 반영.
- 발견된 실제 취약점: ClickHouse가 미니PC `0.0.0.0:8123` 무인증 노출(별도 보안 위생 항목, 프로젝트 서사로도 활용 가능).
