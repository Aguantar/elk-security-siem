# 기술 선택 근거 (Design Decisions)

> 각 선택의 *왜*를 기록. 제약(12GB·무스왑·이기종 2노드) 기반 의사결정.

## D1. 왜 Elasticsearch인가? (이미 ClickHouse 있는데)
- **ClickHouse = 컬럼형 분석 DB** — `count/group by` 집계엔 최강이나 **풀텍스트 검색·로그 needle 찾기·보안 UI가 약함**.
- **Elasticsearch = 역색인 + 로그 친화** — 자유 텍스트(SSH 메시지 `Failed password ... from 1.2.3.4`)를 필드로 색인해 즉시 검색/필터, GeoIP 인리치, **Kibana 보안 시각화** 제공.
- 직무 필수자격이 **Elastic Stack 이해**. → 보안 로그엔 ES가 정답. (ClickHouse는 거래/분석용으로 계속 사용 = 도구를 용도에 맞게 분리한다는 스토리.)

## D2. 왜 Logstash 대신 Filebeat인가?
- **리소스**: Logstash는 JVM(수백 MB~). Filebeat는 Go, ~50MB. **12GB 공유 박스**(n8n·keep-warm 등 동거)에서 Logstash는 사치.
- **불필요**: 무거운 변환이 필요 없음 — 파싱은 **ES ingest pipeline**(grok/GeoIP)으로 충분.
- → "리소스 제약 속 의사결정"으로 설명 가능.

## D3. 왜 Filebeat를 컨테이너 아닌 호스트 패키지로?
- 8.x **journald 입력은 `journalctl` 바이너리를 호출**하는데, **Filebeat 공식 도커 이미지엔 journalctl이 없음**(minimal). 컨테이너에선 `executable file not found` 실패.
- 호스트 패키지는 시스템 journalctl을 그대로 사용 → journald 입력 동작.
- 부수효과: 로그 셔퍼는 **패키지 설치가 Ansible(Phase 2)에 더 정석** — Phase 2와도 일관.

## D4. 왜 로그 소스를 journald로 통일했나? (파일 아님)
- SSH 인증 로그는 원래 journald(systemd)에 적재됨.
- Caddy 접속로그를 **파일로** 내보내려 했으나 **caddy.service의 systemd 샌드박스(`ProtectSystem=full`)가 caddy 소유 디렉토리 신규 파일 쓰기까지 차단** → 파일 출력 실패(서비스 다운까지 경험). `output stdout` → journald로 우회.
- 결과: 두 소스(SSH·웹) 모두 **journald 단일 창구** → Filebeat journald 입력 하나로 통합 수집.

## D5. 왜 ELK를 Oracle에 두나? (미니PC 아님)
- 미니PC(16GB)는 컨테이너 22개로 빡빡(스왑 사용 중). ES는 JVM 힙만 1~2GB.
- Oracle은 ES 올리기 전 **10GB 여유**. → 무거운 ES는 여유 있는 보조 노드에. (운영 부담을 프로덕션 박스에서 분리.)

## D6. ES 메모리/모드 설정 근거
- `discovery.type=single-node`: 데모 규모엔 단일 노드 충분.
- `ES_JAVA_OPTS=-Xms1g -Xmx1g`: **힙 명시적 cap**. 안 하면 ES가 메모리 절반을 잡으려 함. 게다가 **스왑 0**이라 OOM=즉사 → 힙 제한 필수.
- `bootstrap.memory_lock=true` + `vm.max_map_count=262144`: ES 권장 기동 조건.

## D7. ES 포트를 공개하지 않은 이유 (보안)
- ES를 `0.0.0.0`에 열면 **무인증 ES = 데이터 유출/장악 위험**(실제로 미니PC ClickHouse가 무인증 노출된 걸 이 프로젝트 중 발견).
- → ES 9200을 **`127.0.0.1` + WireGuard IP(`<WG_ORACLE_IP>`)에만 바인드**. 미니PC Filebeat는 **WireGuard 터널**로 적재, 공개 인터넷엔 미노출.

## D8. xpack.security 초기 off (정직한 한계)
- 빠른 반복을 위해 초기엔 보안 끔. **단, 보안 직무 프로젝트에서 ES 인증 off는 아이러니** → **하드닝 단계에서 xpack 네이티브 인증 활성화 완료(D23).** "초기 iteration엔 off, 이후 켰다"는 의사결정 + 보안 인지 스토리로 전환.

## D9. ES 버전 8.18.1 선택
- 9.x보다 **8.x가 문서·트러블슈팅 성숙**. arm64 이미지 검증 완료. (Filebeat는 8.19.x 호스트 패키지 — 동일 major 내 Filebeat≥ES 호환.)

## D10. 인덱스 설계 `seclogs-<host>-<date>` + `host_name`/`log_source` 필드
- 이기종 2노드(oracle/minipc) × 2소스(ssh/caddy)를 **host_name·log_source 필드로 분리** → Kibana에서 호스트별/소스별 필터·비교 용이. 날짜 접미사로 시계열 관리.

---
*업데이트 규칙: 새 기술/설정 선택 때마다 여기에 "무엇을·왜·트레이드오프" 한 줄씩 추가.*

## D11. 단일 데이터스트림 `seclogs-live` (인덱스 전략)
- 초기 Filebeat가 인덱스명에 **이벤트 날짜**를 넣어 일자별 데이터스트림 140개+ 생성(샤드 스프롤, 단일노드 비효율).
- → 정적 `seclogs-live` 하나로 통합(ILM 롤오버로 크기 관리 가능). host_name·log_source 필드로 호스트/소스 구분.
- 트레이드오프: 단일 인덱스라 ILM 롤오버 설정은 추후 과제(데모 규모엔 충분).

## D12. grok + GeoIP를 ES ingest pipeline에서 처리 (Logstash 아님)
- 파싱/인리치를 **ES ingest pipeline**(`default_pipeline`)으로 → Logstash 불필요(D2 일관). 적재 시 자동 파싱.
- GeoLite2는 ES geoip 다운로더가 자동 취득(라이선스/수동 DB 불필요).

## D13. Kibana 보호를 Caddy basic_auth로 (xpack off 보완)
- Kibana 자체 인증 off 상태(D8)라 공개 노출 시 위험 → **Caddy basic_auth(bcrypt)**로 1차 보호. ES 포트는 비공개(D7).
- 이후 xpack 네이티브 인증 활성화 완료(D23) → 현재 xpack 인증 + Caddy basic_auth 2겹(basic_auth는 추가 방어로 유지).

## D14. user.name과 source.geo 둘 다 유지 (필드 선택 근거)
- 질문: user.name은 빈값 많은데 빼도 되지 않나? (국가가 더 그림이 됨)
- 답: **다른 축이라 둘 다 필요.** source.ip/geo = "어디서"(위협 출처·지도·차단), user.name = "무엇을 노리나"(타깃 계정 → 하드닝·침해탐지).
- ECS의 `누가-어디서-무엇을-성공했나` 모델 중 source(geo) vs target(account). 특히 **"노린 계정에 성공 로그인"**이 핵심 탐지 시나리오라 user.name 필수.
- 시각적 임팩트는 geo가 크지만, "보안 시나리오 개발"(JD)엔 user.name이 대응 스토리(root 집중공격→key-only 전환)를 만든다.

## D15. 집계 단위는 카디널리티에 맞춘다 + ASN 인리치 추가
- 문제: "Top 공격 IP" 막대가 의미있나? SSH 고유 IP **3,146개**, Top10 집중도 **15%**(롱테일). 봇넷이 IP를 분산해 개별 IP는 전체 그림을 못 담음.
- 원칙: **고유값(카디널리티)이 크면 집계 단위를 올린다.** IP(3,146) → 국가(89) → ASN(호스팅업체). 단위를 올릴수록 롱테일이 압축돼 신호가 보임.
- 조치: geoip 프로세서로 **ASN 인리치 추가**(GeoLite2-ASN, `source.as.organization_name`).
- 결과: **DigitalOcean 한 곳이 SSH 공격의 ~34%(20,131건)** — IP로는 안 보이던 신호. (cloud VPS 남용 패턴.)
- Top IP는 폐기 X, **용도 축소**: 집요한 단일 공격자 = 차단/fail2ban 후보 명단.

## D16. 라이브 SIEM(Kibana) URL을 공개하지 않음
- 이제 Kibana는 xpack 인증 + Caddy basic_auth 2겹(D23)이지만, 공개 URL 자체가 관제 콘솔을 광고하는 꼴이라 비공개 유지.
- 공격자는 노출된 Kibana를 스캔함(우리 로그에 Shodan 적중). cert transparency로도 도메인 발견 가능.
- 보안 직무에선 *상시 공개 노출 자체가 마이너스 신호* → **공개 문서엔 라이브 링크 대신 스크린샷으로.**
- (xpack 인증은 활성화됨(D23). 라이브 공개엔 읽기전용 계정 + HTTP TLS가 추가로 필요. 현재는 스크린샷으로.)
- 관제 콘솔을 공개하지 않는 판단 자체가 보안 인지의 근거.

## D17. Slack 알림을 Kibana 커넥터 대신 독립 포워더로 (basic 라이선스 제약)
- Kibana 무료 라이선스는 알림 커넥터(`.slack`/`.webhook`/`.email` 등)가 전부 Gold+ 유료. enabled는 `.index`/`.server-log`뿐.
- 트라이얼(30일 만료)·유료는 지속 불가 → **오라클에 경량 포워더(systemd 타이머)** 자체 제작해 ES를 로컬로 읽고 Slack webhook으로 POST.
- 의의: "라이선스 제약을 인지하고 파이프라인으로 우회".

## D18. 탐지=Kibana 룰, 전달=포워더로 역할 분리 (단일 탐지 소스)
- 대안(포워더가 seclogs를 직접 재판정)은 탐지 로직이 두 곳에 중복.
- 채택: 임계값 판정은 **Kibana 룰**이 전담하고 `security-alerts` 인덱스에 기록 → 포워더는 **새 doc만 릴레이**.
- 결과: security-alerts가 *대시보드 알림 큐 + Slack 소스*를 공유하는 단일 알림 저장소.
- 상태파일로 last_seen + IP 쿨다운(60분) 관리 → 진행 중 공격 도배 방지.
- (크립토 파이프라인은 Airflow+Variable로 Slack 전송. SOC는 결합 회피 위해 Airflow 미사용, Block Kit 포맷만 차용. 채널·webhook 분리.)

## D19. 심각도는 횟수가 아니라 현업 신호로 차등 + MITRE 태깅
- 안티패턴: "시도 횟수=심각도". 볼륨은 소음/우선순위지 진짜 심각도가 아님.
- 채택 모델: **성공 로그인 흔적→critical(침해 의심)** / **권한·실계정(root·admin 등) 표적 또는 피크 100회+→high** / 그 외→medium.
- 참고: Elastic Security(severity+risk_score), MITRE ATT&CK **T1110**(Brute Force), CVSS 밴드. 알림에 **T1110.001** 태깅.
- 함정 기록: `user.name`은 ECS상 **keyword**라 집계는 `user.name`으로. `user.name.keyword`로 조회하면 빈 결과 → "데이터 없음" 오판 주의(대시보드 동일 필드 점검 후속).
- 스토리: 임계값(D-detection)도, 등급도 *데이터/신호 기반*으로 정한다는 일관성.

## D20. 공격자 시점 점검 후 미니PC SSH 비번 인증 차단 (탐지보다 원천 차단 우선)
- 데이터로 **미니PC가 최대 피격 노드**(69,764건, 2,684 IP)임을 발견 — 직관과 반대. 2월 피크 37,880 → 3월 10일 하루 만에 70배 급감(노출 닫힌 시그니처).
- 점검: 오라클은 `PasswordAuthentication no`(키 전용)라 brute-force 성공이 구조적 불가 → "침해 0"은 운이 아니라 구조. 반면 **미니PC는 `yes`** = 최대 피격인데 비번 문 열림 = 진짜 구멍.
- 조치: 미니PC `PasswordAuthentication no`. 드롭인 **순서 함정**(50-cloud-init이 99를 눌러 미적용) → `00-hardening.conf`로 앞번호화 해결. `sshd -T`로 실효값 검증.
- 실질 노출면이 SSH→**웹(셸 가능 개발 IDE)**으로 이동했음을 식별. 앞단 인증 없던 것에 **basic_auth 2차 방어 적용**(무인증 401 차단) → IDE 자체 로그인과 합쳐 2겹 독립 방어.
- 의의: *원천 차단이 탐지보다 우선*. 위험을 데이터로 식별하고 우선순위대로 막는 판단력. (상세 `host-hardening.md`)

## D21. v1 완성 후 외부 위협 인텔(AbuseIPDB)로 v2 강화 (한계 인식 → 개선 서사)
- v1 한계: 내부 신호만 봐서 "신규 IP vs 글로벌 상습범"을 구분 못 함.
- v2: AbuseIPDB 연동으로 우리 공격자를 외부 평판과 교차검증. 상위 200개 중 50%가 신고 이력(누적 140만 건), **27%는 미신고** → "평판은 보강이지 대체가 아니다"는 균형 인식.
- 직무 적합: 외부 피드 수집·정규화·조인이 곧 "정보보호 데이터 엔지니어"의 일. 성장(반복·성숙) 서사. (상세 `phase-4-threat-intel.md`)

## D22. 위협 인텔은 벌크 enrich + 알림시 per-IP의 2계층
- `/blacklist`(글로벌 top-10k)는 우리 특정 공격자를 다 못 덮음(주 공격자가 개별 100%인데 블랙리스트엔 없던 사례).
- 그래서 **벌크 enrich(근사·상시·무료)** + **알림 시 per-IP /check(정밀·탐지수만큼만 호출)**로 분리. 비용·커버리지·정밀도 균형.
- enrich는 ES enrich 정책(match on ip) + ingest 프로세서, 피드는 일일 systemd 타이머로 갱신. 평판은 정보 표시용이고 심각도는 영향 기반 유지. 키는 `/etc/threat-intel.env`(600).

## D23. ES/Kibana xpack 네이티브 인증 활성화 (관제 스택이 안 잠긴 아이러니 해소)
- 초기엔 빠른 반복 위해 ES 보안 off였음. 보안 프로젝트에서 ES/Kibana가 무인증인 건 가장 아픈 모순 → 하드닝에서 해소.
- 조치: `xpack.security.enabled=true`(단일노드라 transport TLS 불필요), elastic·kibana_system 비번 설정, Kibana·Filebeat(양 노드)·포워더·위협인텔에 자격 주입(시크릿은 전부 env/vault).
- 함정 기록: 보안 켜니 기존 Alerting 룰이 옛 API 키로 `security_exception` → 룰 disable/enable로 API 키 재발급해 해결.
- 결과: 무인증 401 / 인증 200, 전체 파이프라인 정상. Kibana는 xpack 인증 + Caddy basic_auth 2겹.
- 일상 로그인은 최소권한 개인계정(Kibana 기능 + 보안인덱스 read, 클러스터 슈퍼유저 아님)으로, elastic 슈퍼유저는 설정·비상용으로 봉인. 남은 과제: HTTP TLS.

## D24. 자동 대응(fail2ban) — 알림과 분리한, 더 공격적인 호스트 레벨 자동 차단
- 탐지(SIEM)만으론 반쪽 → 오라클에 fail2ban으로 SSH 자동 차단. **차단 임계값 = 5분 5회**(알림 룰 20/5분보다 공격적).
- **왜 알림(20)과 다르게 차단(5)인가**: 알림을 20으로 높인 건 *노이즈 경제성*(알림 폭탄 방지) 때문. 그런데 차단은 ① 조용히 밴만 할 뿐 알림을 안 쏟고 ② 내 IP는 `ignoreip`로 보호됨 → 알림을 억제하던 이유가 차단엔 적용 안 됨 → 더 빨리 막는 게 합리적. **"알림으로 받을 가치" ≠ "차단할 가치" → 임계값을 의도적으로 분리.**
- 안전: fail2ban 전용 iptables 체인(기존 방화벽 불변) + `ignoreip` 허용목록(localhost·WireGuard·known-good 내 IP)으로 자기 lockout 방지. bantime 1h, systemd 백엔드(journald 감시).
- 검증: 차단→iptables REJECT 생성, 해제→제거 확정.
- 정직: password 인증 OFF·키전용이라 brute-force 성공 자체가 불가 → fail2ban 실이득은 *노이즈/리소스 감소 + 대응 루프 시연*. 진짜 차단은 이미 구조(키전용)가 함.
- 설계: SIEM(ES)과 독립(병렬, 자체 로그 감시). SIEM-트리거 차단(SOAR)은 라이브 방화벽 직접조작 리스크라 미채택.

## D25. 왜 단일 노드·복제본 0인가 — 규모에 맞춘 선택, 프로덕션이면 멀티노드
- **현재 선택**: `seclogs-live`(데이터 스트림, primary 1 / **replica 0**). 약 13만 건·117MB·단일 백킹 인덱스라 단일 노드로 충분. replica 0 = 노드 장애 시 유실 위험이나, 개인 운영 SIEM이라 RPO/RTO 요구가 낮아 의식적으로 감수.
- **한계**: replica 0 → 노드 다운 시 가용성·내구성 0, 검색 부하 분산도 불가.
- **프로덕션이면**: 마스터 후보 홀수(최소 3노드) + `number_of_replicas≥1`로 내구성 확보. primary shard 수는 인덱스 크기·검색 동시성 기준 산정(샤드당 수십 GB 권장). 마스터/데이터/coordinating 역할 분리.
- **트레이드오프**: 복제본은 내구성·읽기 처리량을 올리지만 저장·쓰기 비용 증가. 현 규모에선 복제본은 과투자라 0으로 둠.

## D26. 인덱스 수명관리(ILM) — 지금은 미적용, 프로덕션이면 자동 계층화
- **현재 선택**: `seclogs-live`는 **데이터 스트림(롤오버 가능 구조)**이나 ILM 정책 미부착 + Filebeat `setup.ilm.enabled: false`. 보존·삭제는 수동. 현 규모(13만 건/117MB)에선 계층화 이득이 미미해 의식적으로 미적용.
- **한계**: 데이터가 무한 증가하면 단일 노드·단일 인덱스에서 검색 성능 저하·디스크 압박. 오래된 로그와 최신 로그가 같은 비용으로 저장됨.
- **프로덕션이면**: 데이터 스트림에 ILM 정책 부착 — hot→warm→cold→delete. 최근은 hot(빠른 검색), 오래된 데이터는 warm/cold(압축·저비용)로 이동 후 보존기간 경과 시 자동 삭제. rollover는 크기(예: 50GB)·기간 기준. **이미 데이터 스트림이라 정책만 붙이면 되는 구조.**
- **트레이드오프 / 규제 연결**: ILM은 운영 부담을 줄이지만 정책 설계가 틀리면 필요한 데이터를 조기 삭제하거나 비용이 새는 위험. 특히 **금융권은 접근기록·거래기록 등 로그 보존 의무가 법규로 정해져 있고(로그 종류·적용 법규별로 기간 상이), ILM의 delete 단계는 해당 보존 의무기간 이후로 정렬해야 함** — 규제 보존요건과 자동 삭제 정책의 정합이 핵심.

## D27. 매핑 설계 — 핵심 필드는 명시 타입, dynamic 폭발은 프로덕션 과제
- **현재 선택**: 커스텀 인덱스 템플릿 `seclogs-tmpl`로 핵심 필드를 명시 매핑 — `source.ip`=**`ip` 타입**(서브넷·CIDR·범위 검색 가능), 집계 대상(country/ASN/`user.name`)=`keyword`, 원문만 `message`=`text`, 위치=`geo_point`. grok으로 자유 텍스트에서 뽑은 필드를 ECS 정렬로 타입화(현재 leaf 67개, 폭발 없음).
- **한계**: 템플릿에 없는 필드는 여전히 dynamic(기본 true)으로 자동 매핑 → 예기치 않은 필드 유입 시 mapping explosion·의도치 않은 text+keyword 멀티필드 중복 위험.
- **프로덕션이면**: 템플릿에 `dynamic: strict`(또는 false)로 미정의 필드 차단, ECS 필드명 전면 정렬, 멀티필드 최소화.
- **트레이드오프**: 명시 매핑은 초기 설계 비용이 들지만 검색 정확도·성능·안정성을 확보. **이 프로젝트가 `source.ip`를 ip로 타입한 것 자체가 이 방향의 첫 단계** — "매핑을 의식하고 필드를 설계했다".

## D28. 인덱스 분리 기준 — 왜 SSH·웹을 단일 데이터 스트림으로 통합했나
- **현재 선택**: SSH·웹 로그를 단일 데이터 스트림 `seclogs-live`로 통합하고, **`log_source` 필드로 출처를 구분**. 소스가 달라도 "보안 이벤트"라는 단일 분석 평면에서 교차 분석(같은 IP가 SSH·웹 양쪽 공격)하기 위함.
- **한계**: 소스별 보존정책·접근권한·매핑을 다르게 가져갈 수 없음. 한 소스의 트래픽 급증이 인덱스 전체에 영향.
- **프로덕션이면**: 소스별 데이터 스트림 분리(`logs-ssh-*`, `logs-web-*`) 후 검색 시 인덱스 패턴으로 통합 조회. 보존·권한·샤딩을 소스별 차등 적용. 단, 교차 분석 요구가 강하면 통합 유지가 더 나을 수 있음.
- **트레이드오프**: 통합은 교차 분석이 쉽고 운영이 단순, 분리는 정책 유연성·격리성이 높음. 규모와 분석 패턴이 분리 시점을 결정 — 현재는 교차 분석 가치가 커 통합 유지.

## D29. 탐지 vs 예방 — burst 알림 비활성화, 탐지는 "차단이 못 보는 축"으로 (2026-07-01)
- **문제**: fail2ban 차단(5분 5회, D24)을 넣자 같은 신호(SSH 실패)에서 **차단 임계값(5) < 탐지 알림 임계값(20, 시나리오1)** → 차단이 5에서 먼저 잘라 20-알림은 구조적으로 못 뜸(예방이 탐지를 선점). "예방보다 높은 탐지 임계값"은 거꾸로 된 설계.
- **결정**: 20/5분 **단독 brute-force 알림을 비활성화**(rule disable). 버스트 예방은 fail2ban이 전담.
- **탐지 재배치**: SIEM 탐지의 가치를 *차단이 못 보는 신호*로 이동 — ① known-good 아닌 **성공 로그인**(침해, 시나리오2) ② **저속·계정 스프레이**(시나리오4). 실측(2026-07-01) 실제 공격은 버스트가 아니라 저속·다계정형(한 IP 30계정·peak 1/5분)이라 fail2ban·20룰이 모두 놓치던 것 → 이 재배치로 실제 갭을 메움.
- **일반화(핵심)**: **inline 차단을 도입하면 그보다 높은 임계값의 탐지 알림은 구조적으로 중복이 된다.** 탐지 엔지니어링의 가치는 "예방이 이미 막는 것"이 아니라 "예방이 못 보는 것"에 있다. (임계값 분포 분석 자체는 탐지선 설계 사례로 유지 — 블로그 2·4편.)
