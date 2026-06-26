# scripts — SOC 포워더 · 위협 인텔 갱신

탐지 파이프라인의 Python 구성요소. 둘 다 오라클 노드에서 **systemd 타이머**로 주기 실행한다.
**시크릿(Slack webhook, AbuseIPDB 키)은 전부 환경변수로만 읽고, 코드에는 없다.**

## `soc-slack-forwarder.py`
- Kibana 룰이 `security-alerts` 인덱스에 기록한 brute-force 탐지를 **새 건만** 골라 Slack으로 릴레이.
- 탐지(임계값 판정)는 Kibana 룰이 담당, 이 스크립트는 **전달 + 등급화**만 한다.
- 심각도는 횟수가 아니라 현업 신호로: 성공 로그인 흔적→critical / 권한계정·피크100+→high / 그 외→medium.
- 알림 시 해당 IP를 AbuseIPDB로 실시간 조회해 평판을 카드에 표시. MITRE T1110 태깅, 표적 서버 표시.
- 상태파일(`state.json`)에 last_seen + IP별 쿨다운(60분)을 저장해 같은 공격 도배 방지.
- 환경변수: `SOC_SLACK_WEBHOOK`(필수), `ABUSEIPDB_KEY`(선택). systemd 타이머 2분 주기.

## `threat-intel-refresh.py`
- AbuseIPDB 블랙리스트(confidence≥90, 최대 1만 IP)를 받아 ES enrich 소스 인덱스에 재적재 + enrich 정책 재실행.
- 수집 파이프라인의 enrich 프로세서가 들어오는 이벤트의 `source.ip`를 이 목록과 대조해 평판 태깅.
- 환경변수: `ABUSEIPDB_KEY`(필수). systemd 타이머 일일 갱신(피드 신선도 유지).

> 참고: `HOST_LABEL`의 호스트명은 예시값(`oracle-arm`/`mini-pc`)으로, 자기 환경의 `host.name`에 맞게 조정. 상세 설계는 `docs/phase-3-slack-alerting.md`, `docs/phase-4-threat-intel.md`.
