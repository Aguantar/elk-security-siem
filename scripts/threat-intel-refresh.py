#!/usr/bin/env python3
"""AbuseIPDB 블랙리스트 일일 갱신 → ES enrich 소스 재적재 → 정책 재실행."""
import json, os, urllib.request, urllib.parse, sys, base64
ES="http://127.0.0.1:9200"
KEY=os.environ.get("ABUSEIPDB_KEY")
ES_USER=os.environ.get("ES_USER"); ES_PASS=os.environ.get("ES_PASS")
_ES_AUTH=("Basic "+base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()) if ES_USER and ES_PASS else None
def es(method,p,b=None,raw=False):
    data=(b.encode() if raw else json.dumps(b).encode()) if b is not None else None
    h={"Content-Type":"application/json"}
    if _ES_AUTH: h["Authorization"]=_ES_AUTH
    r=urllib.request.Request(ES+p,data=data,headers=h,method=method)
    try: return json.load(urllib.request.urlopen(r,timeout=60))
    except urllib.error.HTTPError as e: return {"_err":e.code,"body":e.read().decode()[:200]}

if not KEY: print("ABUSEIPDB_KEY 미설정", file=sys.stderr); sys.exit(1)
u="https://api.abuseipdb.com/api/v2/blacklist?"+urllib.parse.urlencode({"confidenceMinimum":"90","limit":"10000"})
r=urllib.request.Request(u,headers={"Key":KEY,"Accept":"application/json"})
bl=json.load(urllib.request.urlopen(r,timeout=60))["data"]

es("DELETE","/threat-intel-abuseipdb")
es("PUT","/threat-intel-abuseipdb",{"mappings":{"properties":{
  "ip":{"type":"keyword"},"abuse_score":{"type":"integer"},
  "last_reported":{"type":"date"},"country":{"type":"keyword"}}}})
lines=[]
for e in bl:
    lines.append(json.dumps({"index":{}}))
    lines.append(json.dumps({"ip":e["ipAddress"],"abuse_score":e.get("abuseConfidenceScore"),
                             "last_reported":e.get("lastReportedAt"),"country":e.get("countryCode")}))
es("POST","/threat-intel-abuseipdb/_bulk?refresh=true","\n".join(lines)+"\n",raw=True)
ex=es("POST","/_enrich/policy/abuseipdb-policy/_execute")
print(f"갱신 완료: {len(bl)} IP, 정책={ex.get('status',ex)}")
