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
