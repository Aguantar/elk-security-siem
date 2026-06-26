# Phase 1 — ELK 스택 구축 & 로그 적재 (2026-06-24)

> 목표: Oracle에 Elasticsearch+Kibana 단일노드 기동 → Filebeat로 양쪽 호스트의 journald(SSH+Caddy) 로그를 ES에 적재 → Kibana에서 검색/시각화 가능 상태.
> 기술 선택 근거는 `decisions.md`(D1~D10) 참조.

## 구성 요약
```
[미니PC] journald(ssh,caddy) → Filebeat(호스트) ─WireGuard(<WG_ORACLE_IP>:9200)─┐
                                                                           ├→ [Oracle] Elasticsearch ← Kibana
[Oracle] journald(ssh,caddy) → Filebeat(호스트) → localhost:9200 ──────────┘
```

## 완료 (Oracle)
- **ES + Kibana 8.18.1 (arm64)** docker-compose 기동. `/home/ubuntu/elk/docker-compose.yml`.
  - single-node, 힙 `-Xms1g -Xmx1g`, `xpack.security.enabled=false`(초기), `vm.max_map_count=262144`(/etc/sysctl.d).
  - 포트 비공개: ES `127.0.0.1:9200` + `<WG_ORACLE_IP>:9200`(WireGuard)만, Kibana `127.0.0.1:5601`만.
- **Filebeat 8.19.x 호스트 패키지** 설치(Elastic 공식 apt repo, 서명). `/etc/filebeat/filebeat.yml`.
  - journald 입력 2개: `_SYSTEMD_UNIT=ssh.service`, `caddy.service`. 필드 `log_source`,`host_name=oracle`.
  - 출력: `localhost:9200`, 인덱스 `seclogs-oracle-*`.
- **검증**: 적재 확인 — **SSH 24,730건 + Caddy 716건**. 실공격 샘플: `Invalid user kolten from 213.209.159.x port 46116`.

## 트러블 & 해결 (재현방지)
- **Filebeat를 컨테이너로 띄웠더니 journald 입력 실패**: `journalctl: executable file not found in $PATH`. 8.x journald 입력은 journalctl 바이너리를 호출하는데 공식 Filebeat 이미지엔 없음. → **호스트 패키지로 전환**(decisions D3). Ansible(Phase 2)에도 더 적합.
- compose append 실수로 filebeat가 `volumes:` 밑으로 들어가 파싱오류 → 전체 재작성.

## 완료 (미니PC)
- **Filebeat 호스트 패키지** 설치(사용자 sudo), 설정 `~/filebeat-minipc.yml` → 출력 `<WG_ORACLE_IP>:9200`(WireGuard), 인덱스 `seclogs-minipc-*`.
- **검증(양쪽 통합)**: ES 총 **41,536건** — oracle 25,455(ssh 24,739·caddy 716) + minipc 16,081(ssh 8,212·caddy 7,869).

## 알려진 개선점 (샤드 스프롤)
- 인덱스명에 **이벤트 날짜**(`%{+yyyy.MM.dd}`)를 써서, 미니PC의 2월~ 이력이 **일자별 140개+ 데이터스트림**으로 분할됨. 단일노드에 작은 샤드 과다 = 비효율.
- 개선안: 월별 인덱스 or 단일 데이터스트림 + ILM 롤오버(크기 기반). Phase 1 폴리시/다음에 정리.

## 다음 (Phase 1 후반)
1. **Kibana 접속 경로**: 즉시는 SSH 터널(`ssh -L 5601:127.0.0.1:5601 oracle`), 정식은 `kibana.example.com`+Caddy+basic auth(+Gabia DNS).
2. **ingest pipeline**: SSH 메시지에서 `source.ip`·`user.name` grok 파싱 + **GeoIP** 인리치(국가/좌표). 지금은 message가 자유텍스트라 Top-IP/지도엔 파싱 필요.
3. **Kibana 대시보드 + 탐지 시나리오**: Top 공격 IP, 시도 계정 사전, 시간대 추이, 국가 지도, brute-force/스캐너 룰.

## 메모
- ES status `yellow` = 단일노드라 복제샤드 미할당(정상). 필요 시 인덱스 템플릿에서 `number_of_replicas:0`로 green.
- 미니PC NAT 헤어핀으로 내부 테스트는 공유기 IP로 찍힘 — 실외부 공격 IP는 정상 기록될 것(GeoIP 단계에서 실데이터 검증).
