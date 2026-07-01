#!/usr/bin/env python3
"""SOC Slack 포워더.

Kibana 룰이 `security-alerts` 인덱스에 기록한 SSH brute-force 탐지를
새로 들어온 것만 골라 Slack Webhook으로 릴레이한다.

- 탐지(임계값 판정)는 Kibana 룰이 담당. 이 스크립트는 '전달 + 등급화'만 한다.
- 심각도는 횟수가 아니라 현업 신호로 매긴다:
    성공 로그인 흔적 → critical(침해 의심)
    권한 계정(root/admin 등) 표적 또는 피크 100회+ → high
    그 외(invalid user 위주, 20~99회) → medium
- 상태파일(state.json)에 마지막 처리시각 + IP별 쿨다운을 저장해
  같은 공격을 매분 반복 전송하지 않는다.
- Webhook URL은 환경변수 SOC_SLACK_WEBHOOK 에서만 읽는다(코드에 박지 않음).
"""

import json
import os
import sys
import urllib.request
import datetime
import pathlib
import base64
import time

ES = "http://127.0.0.1:9200"
INDEX = "security-alerts"
STATE = pathlib.Path("/var/lib/soc-forwarder/state.json")
COOLDOWN_MIN = 60                       # 같은 IP 재알림 최소 간격(분)
PRIV_ACCOUNTS = {"root", "admin", "administrator", "ubuntu", "oracle"}  # 권한/실계정
HOST_LABEL = {"oracle-arm": "오라클 클라우드", "mini-pc": "미니PC 홈서버"}  # host.name → 표시 라벨 (자기 환경의 host.name 값에 맞게 조정)
WEBHOOK = os.environ.get("SOC_SLACK_WEBHOOK")
ABUSEIPDB_KEY = os.environ.get("ABUSEIPDB_KEY")  # 없으면 평판 줄 생략(알림은 계속)
ES_USER = os.environ.get("ES_USER")              # ES 인증(켜져 있으면 필요)
ES_PASS = os.environ.get("ES_PASS")
_ES_AUTH = ("Basic " + base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()) if ES_USER and ES_PASS else None


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def es(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if _ES_AUTH:
        headers["Authorization"] = _ES_AUTH
    req = urllib.request.Request(ES + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def enrich(ip):
    """seclogs-live에서 국가·ASN·성공여부·노린계정을 조회(대시보드와 동일 출처)."""
    q = {
        "size": 0,
        "query": {"bool": {"filter": [{"term": {"source.ip": ip}}]}},
        "aggs": {
            "cc": {"terms": {"field": "source.geo.country_name", "size": 1}},
            "asn": {"terms": {"field": "source.as.organization_name.keyword", "size": 1}},
            "succ": {"filter": {"term": {"event.outcome.keyword": "success"}}},
            "users": {"terms": {"field": "user.name", "size": 5}},
            "hosts": {"terms": {"field": "host.name.keyword", "size": 3}},
        },
    }
    try:
        r = es("POST", "/seclogs-live/_search", q)
        a = r["aggregations"]
        cc = a["cc"]["buckets"]
        asn = a["asn"]["buckets"]
        return {
            "country": cc[0]["key"] if cc else "Unknown",
            "asn": asn[0]["key"] if asn else "Unknown",
            "success_count": a["succ"]["doc_count"],
            "top_users": [b["key"] for b in a["users"]["buckets"] if b["key"]],
            "target_hosts": [b["key"] for b in a["hosts"]["buckets"]],
        }
    except Exception:
        return {"country": "Unknown", "asn": "Unknown",
                "success_count": 0, "top_users": [], "target_hosts": []}


def severity(fc, geo):
    """현업 신호 기반 등급. 횟수는 보조 지표."""
    if geo["success_count"] > 0:
        return "critical"          # 실패하다 성공 = 침해 의심
    targeted_priv = any(u.lower() in PRIV_ACCOUNTS for u in geo["top_users"])
    if fc >= 100 or targeted_priv:
        return "high"
    return "medium"


def ti_check(ip):
    """AbuseIPDB 실시간 평판 조회. 실패하면 None을 반환(알림은 계속 나감)."""
    if not ABUSEIPDB_KEY:
        return None
    import urllib.parse
    u = "https://api.abuseipdb.com/api/v2/check?" + urllib.parse.urlencode(
        {"ipAddress": ip, "maxAgeInDays": "90"})
    req = urllib.request.Request(
        u, headers={"Key": ABUSEIPDB_KEY, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r).get("data", {})
        return {"score": d.get("abuseConfidenceScore", 0),
                "reports": d.get("totalReports", 0)}
    except Exception:
        return None


def post_slack(ip, info, geo, sev, ti):
    """단일 세로 레이아웃 + MITRE 태그 + 위협 인텔. 기본 이모지 미사용."""
    users_txt = ", ".join(geo["top_users"][:3]) if geo["top_users"] else "(미상)"
    hosts_txt = ", ".join(
        f"{h} ({HOST_LABEL[h]})" if h in HOST_LABEL else h
        for h in geo["target_hosts"]) if geo["target_hosts"] else "Unknown"

    # 악성 평판 — 점수가 0~100 '확신도'임을 명시
    ti_txt = ""
    if ti:
        if ti["score"] >= 75:
            level = "상습범"
        elif ti["score"] == 0:
            level = "신고 이력 없음(신규/미상)"
        else:
            level = "신고 이력 있음"
        ti_txt = (f"*악성 평판:*  악성 확신도 {ti['score']}/100, "
                  f"전 세계 {ti['reports']}건 신고 ({level})\n")

    if info.get("detection") == "suspicious_success":
        # 침해 의심 — known-good 아닌 외부 IP의 로그인 '성공'
        header = "의심 성공 로그인 — 침해 가능성"
        mitre = "MITRE ATT&CK: T1078 Valid Accounts"
        text = (
            f"*출처 IP:*  `{ip}`  (국가: {geo['country']})\n"
            f"*대상 서버:*  {hosts_txt}\n"
            f"*상황:*  known-good 아닌 외부 IP에서 **로그인 성공**\n"
            f"*계정:*  {users_txt}\n"
            f"{ti_txt}"
            f"*심각도:*  CRITICAL — 침해 의심, 즉시 확인 필요\n"
            f"*ASN(호스팅):*  {geo['asn']}\n"
            f"*탐지시각(UTC):*  {info['ts']}"
        )
    elif info.get("detection") == "low_and_slow":
        # 저속·계정 스프레이 — 버스트 방어(fail2ban·brute-force 룰)를 회피하는 장기 저속 실패
        header = "저속·계정 스프레이 의심"
        mitre = "MITRE ATT&CK: T1110.003 Password Spraying"
        text = (
            f"*공격 IP:*  `{ip}`  (국가: {geo['country']})\n"
            f"*표적 서버:*  {hosts_txt}\n"
            f"*상황:*  12시간 내 {info['fc']}회 저속 실패 — 버스트 방어(fail2ban·5분룰) 회피\n"
            f"*노린 계정:*  {users_txt}\n"
            f"{ti_txt}"
            f"*심각도:*  {sev.upper()} — 저속·다계정 시도(rate 회피형)\n"
            f"*ASN(호스팅):*  {geo['asn']}\n"
            f"*탐지시각(UTC):*  {info['ts']}"
        )
    else:
        # SSH brute-force
        header = "SSH Brute-force 탐지"
        mitre = "MITRE ATT&CK: T1110.001 Password Guessing"
        if geo["success_count"] > 0:
            reason = "외부 IP에서 로그인 성공, 침해 의심"
        elif any(u.lower() in PRIV_ACCOUNTS for u in geo["top_users"]):
            reason = "권한·실계정(root/admin 등) 표적"
        elif info["fc"] >= 100:
            reason = "5분 100회 이상, 집요한 대량 시도"
        else:
            reason = "표준적인 봇 사전대입"
        text = (
            f"*공격 IP:*  `{ip}`  (국가: {geo['country']})\n"
            f"*표적 서버:*  {hosts_txt}\n"
            f"*공격 방식:*  5분간 {info['fc']}회 로그인 실패 (탐지 기준 20회 초과)\n"
            f"*노린 계정:*  {users_txt}\n"
            f"{ti_txt}"
            f"*심각도:*  {sev.upper()} — {reason}\n"
            f"*ASN(호스팅):*  {geo['asn']}\n"
            f"*탐지시각(UTC):*  {info['ts']}"
        )
    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"{mitre}  |  rule: {info['rule']}"}
            ]},
        ]
    }
    data = json.dumps(payload).encode()
    err = None
    for attempt in range(3):                       # 일시적 실패(Slack 5xx·네트워크) 재시도
        try:
            req = urllib.request.Request(
                WEBHOOK, data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(2 * (attempt + 1))
    raise err  # 3회 실패 → 호출부에서 처리(쿨다운 미설정 = 다음 실행 재시도)


def main():
    if not WEBHOOK:
        print("ERROR: SOC_SLACK_WEBHOOK 미설정", file=sys.stderr)
        sys.exit(1)

    STATE.parent.mkdir(parents=True, exist_ok=True)

    # 최초 실행: 기존 백로그(백필 34 + 직전 탐지)는 보내지 않고 기준선만 잡는다.
    if not STATE.exists():
        STATE.write_text(json.dumps({"last_seen": iso(now()), "ip_cooldown": {}}))
        print("init: 기준선 설정, 백로그 전송 안 함")
        return

    state = json.loads(STATE.read_text())
    last_seen = state["last_seen"]
    cooldown = state.get("ip_cooldown", {})

    # last_seen 이후의 새 탐지만. 과거 백필(historical backtest)은 제외.
    q = {
        "size": 200, "sort": [{"@timestamp": "asc"}],
        "query": {"bool": {
            "must": [{"range": {"@timestamp": {"gt": last_seen}}}],
            "must_not": [{"term": {"detection": "historical backtest"}}],
        }},
    }
    hits = es("POST", f"/{INDEX}/_search", q)["hits"]["hits"]

    new_last = last_seen
    best = {}  # ip -> 우선순위 높은 탐지 (성공 로그인 > 높은 실패횟수)
    for h in hits:
        s = h["_source"]
        ts = s["@timestamp"]
        if ts > new_last:
            new_last = ts
        ip = s.get("source_ip")
        if not ip:
            continue
        fc = int(s.get("failure_count", 0) or 0)
        det = s.get("detection")
        rank = 2 if det == "suspicious_success" else (1 if det == "low_and_slow" else 0)
        prio = (rank, fc)
        if ip not in best or prio > best[ip]["prio"]:
            best[ip] = {"fc": fc, "ts": ts, "rule": s.get("rule", ""),
                        "detection": det, "prio": prio}

    sent = 0
    any_fail = False
    for ip, info in best.items():
        last_a = cooldown.get(ip)
        if last_a:
            prev = datetime.datetime.fromisoformat(last_a.replace("Z", "+00:00"))
            if (now() - prev).total_seconds() / 60 < COOLDOWN_MIN:
                continue
        try:
            geo = enrich(ip)
            post_slack(ip, info, geo, severity(info["fc"], geo), ti_check(ip))
            cooldown[ip] = iso(now())   # 성공 시에만 → 실패는 다음 실행 재시도
            sent += 1
        except Exception as e:
            any_fail = True
            print(f"전송 실패 {ip}: {e}", file=sys.stderr)

    # 하루 지난 쿨다운 기록 정리
    cutoff = now() - datetime.timedelta(days=1)
    cooldown = {ip: t for ip, t in cooldown.items()
                if datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) > cutoff}

    state["last_seen"] = last_seen if any_fail else new_last  # 실패 있으면 미전진 → 재조회(성공분은 쿨다운 스킵)
    state["ip_cooldown"] = cooldown
    STATE.write_text(json.dumps(state))
    print(f"new_hits={len(hits)} unique_ip={len(best)} sent={sent}")


if __name__ == "__main__":
    main()
