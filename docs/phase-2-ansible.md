# Phase 2 — Ansible 배포 자동화 (2026-06-25)

> Phase 1에서 *손으로* 한 ELK 파이프라인 구축을 **코드(playbook)로 외재화**. 암묵지 → 실행가능 형식지.
> 위치: `~/elk-security/ansible/`

## 구조
```
ansible/
  ansible.cfg              # inventory 경로, become 등
  inventory.ini            # oracle(elk_server) + minipc(log_shippers). 실IP — 공개시 제외
  deploy-elk.yml           # 플레이북 (2 plays)
  templates/
    docker-compose.yml.j2  # ES+Kibana (변수: es_version, wg_ip, kibana_domain)
    filebeat.yml.j2        # journald ssh+caddy → ES (변수: host_name, es_host)
```

## 무엇을 코드화했나 (수동 → task)
| 수동 작업 | Ansible |
|---|---|
| `sysctl vm.max_map_count=262144` | `ansible.posix.sysctl` |
| (docker 설치) | *전제 — 부트스트랩 별도* |
| compose 작성 + `docker compose up` | `template` + `community.docker.docker_compose_v2` |
| (filebeat 설치) | *전제 — 부트스트랩 별도* |
| filebeat.yml 작성(0600) + enable/start | `template`(mode 0600) + `systemd` + handler(restart) |

- **이기종 2노드**: oracle(arm64, ELK서버+수집) / minipc(amd64 local, 수집). `host_name`·`es_host`를 호스트변수로 분기.

## 검증 (`--check --diff`, Oracle)
- 문법 OK, 실행 OK(`failed=0, unreachable=0`).
- **템플릿 내용은 현재 설정을 바이트 단위로 정확히 재현** — `--diff`상 compose·filebeat *content diff 없음*, 차이는 파일모드뿐(→ playbook이 표준화).
- "changed"는 *수동 구축과 코드 표준 간 드리프트 정규화*: 키링 `.gpg`→`.asc`, repo signed-by 경로, 파일모드. = IaC의 핵심(코드가 단일 진실원, 손drift를 수렴).
- **보안 개선**: filebeat.yml 모드 0600(root-only)로 고정.

## 실행
```
ansible-playbook deploy-elk.yml              # oracle passwordless sudo
ansible-playbook deploy-elk.yml -K           # minipc sudo 비번
ansible-playbook deploy-elk.yml --check --diff   # dry-run
```
- **멱등성**: 1회 적용 후 재실행 → `changed=0` (코드와 호스트 상태 일치).

## 공개(GitHub) 시
- 올림: playbook·templates(로직). 빼기: `inventory.ini`(실IP) → `.gitignore` + `inventory.example.ini`(더미) 제공. 비밀은 vault.

## 가치 (JD)
- "다양한 환경 서비스 아키텍처"(이기종 2노드) + Ansible 우대 충족. 멱등성·감사가능성(코드+Git)·재현성.
- 암묵지(머릿속 셋업 절차)를 *실행가능 형식지*로 — 버스팩터 제거·일관성·변경 감사.

## 트러블 & 정정 — config-only 설계 (2026-06-25)
- **초안**: repo/패키지 설치(docker·filebeat)까지 playbook에 포함.
- **실제 적용 시 apt 충돌**: 수동 구축의 키링(`.gpg`)과 playbook(`.asc`)이 *같은 docker 저장소에 두 줄*로 공존 → `E: Conflicting values ... Signed-By docker.gpg != docker.asc` → Oracle apt 깨짐. (`.asc` 줄 제거로 즉시 복구.)
- **교훈**: *이미 손으로 깐 호스트*에 playbook이 repo를 재설치하면 충돌난다. config-management(구성)와 bootstrap(설치)은 **분리**하는 게 실무 패턴.
- **정정**: playbook은 **구성(IaC)만** — repo/패키지 설치는 전제로 분리. → 멱등·안전.
- **멱등성 증명**: 1차 적용 `changed=2`(파일 모드 정규화 0664→0644)·`failed=0` → **2차 재실행 `changed=0`**. 라이브 무손상(filebeat 미재시작, ES/Kibana no-op, ES green, seclogs 98,973건 유지, filebeat.yml 0600).

## 2026-11-24 드리프트 수렴

06-25 이후 처음으로 플레이북을 다시 돌렸다. 그 사이 코드와 호스트가 어긋나 있었다.

### 무엇이 어긋나 있었나
| 항목 | 상태 | 근거 |
|---|---|---|
| 인벤토리 변수 누락 | 06-27 xpack 인증을 켜며 템플릿에 `kibana_system_password`·`es_password` 가 들어갔지만 `inventory.ini`(06-25 수정본)에는 없었다. 그대로 실행하면 템플릿 렌더링이 undefined 변수로 실패한다 | `inventory.ini` 변수 목록 |
| compose 줄 순서 | Oracle 의 `~/elk/docker-compose.yml` 과 템플릿의 Kibana 환경변수 순서가 달랐다(내용은 같음). 호스트 파일은 이 템플릿에서 렌더링된 것이 아니었다 | 렌더링 결과와 실제 파일 `diff` |
| 인증을 손으로 반영 | 06-27 인증 추가는 호스트에서 직접 했다. ES·Kibana 컨테이너 생성 06-27 05:07·05:16, `docker-compose.yml.bak` 05:07, filebeat.yml 05:18(Oracle)·05:22(minipc). 마지막 Ansible 실행 흔적(`~/.ansible/tmp`)은 06-25 | 컨테이너 `Created`, 파일 mtime |

즉 현재 떠 있는 ES·Kibana 는 플레이북이 올린 것이 아니다. 플레이북은 그 구성을 기술할 뿐이었고, 06-27 이후로는 실행조차 안 되는 상태였다.

### 어떻게 맞췄나
- `inventory.ini`: `kibana_system_password`·`es_username`·`es_password` 추가. 값은 Oracle 의 실제 compose 와 `/etc/filebeat/filebeat.yml` 에서 가져왔다(Oracle `~/elk/` 에 `.env` 는 없다). 파일 모드 644 → 600. gitignore 유지. `inventory.example.ini` 는 06-27 에 이미 placeholder 가 있어 변경 없음.
- `templates/docker-compose.yml.j2`: Kibana 환경변수 순서를 호스트 파일에 맞춤. 순서 외 차이는 없었다.
- `templates/filebeat.yml.j2`: 변경 없음. 06-27 인증 추가분(`username`·`password`)이 이미 들어 있었다.
- 적용 전 대조: 두 템플릿을 로컬에서 렌더링해 Oracle 실제 파일과 `cmp` → 둘 다 바이트 단위 동일.

### 재실행 결과
| 실행 | 결과 |
|---|---|
| `--limit oracle --check --diff` | `ok=8 changed=0 failed=0`, diff 없음 |
| `--limit oracle` 1회차 (11:32:13 UTC) | `ok=8 changed=0 failed=0` |
| `--limit oracle` 2회차 (11:32:22 UTC) | `ok=8 changed=0 failed=0` |
| `--limit minipc --check --diff` | 실패 - `sudo: a password is required`(`-K` 없이 실행). 판정 불가 |
| `--limit minipc --check --diff -K` (2026-11-24) | `ok=3 changed=0 failed=0`, diff 없음 |

- 적용 전후 `StartedAt`(elasticsearch 06-27 05:07:38, kibana 06-27 05:16:42)과 filebeat `ActiveEnterTimestamp`(06-27 05:18:16)가 그대로다. 재시작 없음. Kibana `/api/status` 200.
- minipc 는 `/etc/filebeat/filebeat.yml` 이 root 600 이라 내용 대조를 못 했다. 렌더링 결과와 실제 파일의 크기는 같다(643바이트). 크기만으로는 동일하다고 볼 수 없었고, 2026-11-24 `-K` 로 `--check --diff` 를 돌려 `changed=0` 을 확인했다. 두 노드 모두 플레이북과 실제 구성이 일치한다.

### 아직 코드에 없는 것
ingest pipeline·인덱스 템플릿·enrich 정책은 ES API 로 직접 넣었고 플레이북에 없다. 이번에는 적용 코드를 쓰지 않고 현재 정의만 `elk/pipelines/` 에 덤프했다(ES 8.18.1, 2026-11-24).

| 파일 | 출처 |
|---|---|
| `ingest-pipeline.seclogs-parse.json` | `GET _ingest/pipeline/seclogs-parse` - 프로세서 11개(json, set×4, grok, script, geoip×2, enrich, remove) |
| `index-template.seclogs-tmpl.json` | `GET _index_template/seclogs-tmpl` - `seclogs-*`, priority 500, `default_pipeline=seclogs-parse` |
| `enrich-policy.abuseipdb-policy.json` | `GET _enrich/policy/abuseipdb-policy` |

`seclogs` 템플릿(priority 150)은 Filebeat 가 `setup.template.name` 으로 자동 생성하는 것이라 덤프하지 않았다. Kibana 대시보드·Alerting 규칙, `soc-slack-forwarder`·`threat-intel-refresh` systemd 유닛도 플레이북 밖이다.
