# YAML을 설명하기 위한 해설

이 문서는 `202024191 김성욱`의 제출 YAML과 실제 서비스 동작을 연결합니다.

## 기본 구조

- `apiVersion`: API 그룹과 버전입니다. 기본 Object는 `v1`, Deployment는 `apps/v1`, OpenShift Route는 `route.openshift.io/v1`입니다.
- `kind`: 생성할 Object 종류입니다.
- `metadata.name`: 프로젝트 안에서 Object를 식별하는 이름입니다. 과제 prefix `portfolio-202024191`로 범위를 구분합니다.
- `metadata.labels`: `app=portfolio-202024191`은 Service 선택과 상태 조회에 사용합니다. 나머지 표준 label은 앱의 이름·인스턴스·소속을 설명합니다.
- `spec`: 원하는 상태입니다. ConfigMap과 Secret은 `data` 또는 `stringData`가 실제 값을 전달합니다.

`namespace`, Route `host`, `uid`, `resourceVersion`, `creationTimestamp`, `managedFields`, `status`, Service `clusterIP`는 넣지 않았습니다. 다른 환경에서 바뀌거나 클러스터가 생성할 값이기 때문입니다. 배포 후 상태 출력에는 이런 값이 보일 수 있지만 제출 YAML에는 없습니다.

## Deployment

| 필드 | 선택 이유 |
|---|---|
| `replicas: 1` | SQLite 단일 파일을 사용하는 실습 서비스 |
| `strategy.type: Recreate` | 새 Pod 생성 전에 이전 Pod 종료; RWO 볼륨의 동시 부착 경쟁 감소 |
| `selector.matchLabels` | Deployment가 관리할 Pod와 `template.metadata.labels`를 일치시킴 |
| `template` | 생성될 Pod의 선언; 여기 내용이 바뀌면 교체됨 |
| `portfolio/content-sha256` | 앱·설정·키 검증값이 바뀌면 Pod 템플릿도 변경돼 업데이트 유도 |
| `command` | UBI 이미지의 기본 명령을 `python3 /app/server.py`로 대체 |
| `containerPort: 8080`, `name: http` | 앱 포트를 명명; Service와 Probe가 같은 이름 참조 |
| `requests` | 스케줄링에 필요한 최소 자원 기준 |
| `limits` | CPU 사용 상한과 메모리 상한; 메모리 초과는 OOM 종료 가능 |
| `env.valueFrom.configMapKeyRef` | 포트와 DB 경로를 설정 Object에서 받음 |
| `volumeMounts` | 컨테이너 안의 경로와 `volumes` 정의를 연결 |
| `terminationGracePeriodSeconds: 20` | SIGTERM 뒤 종료 유예; 서버는 새 요청 수락을 중지 |

`readOnlyRootFilesystem`은 이미지 파일 시스템을 쓰기 금지로 만듭니다. `.pyc` 생성을 끄고 `/data` PVC와 `/tmp` emptyDir에만 쓰기를 허용합니다. `automountServiceAccountToken: false`는 앱에 필요 없는 Kubernetes API 인증 토큰의 주입을 막습니다. `runAsNonRoot`를 사용하되 `runAsUser`·`fsGroup`를 고정하지 않고 OpenShift의 보안 정책이 값을 부여하게 합니다.

## Service와 Route

Service는 `spec.selector.app` 값으로 Pod를 찾습니다. `port: 8080`은 Service 포트이고 `targetPort: http`는 Pod의 `name: http` 포트를 가리킵니다. Route의 `to.name`은 Service 이름이고 `port.targetPort: http`는 Service의 포트 이름입니다. 세 Object 사이에서 이 이름들이 맞아야 요청이 연결됩니다.

`tls.termination: edge`는 OpenShift Router에서 TLS를 종료합니다. 외부 브라우저–Router 구간은 HTTPS이고 Router–Pod 구간은 HTTP입니다. `insecureEdgeTerminationPolicy: Redirect`는 HTTP 접속을 HTTPS로 이동시킵니다. 자체 인증서나 고정 도메인을 포함하지 않고 Sandbox의 기본 라우터 인증서를 사용합니다.

## ConfigMap과 Secret

앱 ConfigMap은 서버·HTML·CSS·JS·SVG를 포함합니다. `items`로 `server.py`는 `/app/server.py`, 나머지는 `/app/static/` 아래에 배치합니다. 공개 소개와 일반 설정은 별도 ConfigMap으로 분리했습니다. `defaultMode: 292`는 10진수 표현이며 8진수 `0444`(읽기 전용)입니다. 코드 전체 크기는 ConfigMap 한도보다 충분히 작고 테스트로 900KB 미만을 확인합니다.

Secret의 `stringData.key-sha256`에는 **원문 토큰이 아니라 검증 해시**가 들어갑니다. API 서버가 `data`의 base64 표현으로 바꾸는 것은 암호화와 다릅니다. `defaultMode: 288`은 `0440`이며 Secret은 읽기 전용 볼륨으로 제공됩니다. 서버는 인증할 때마다 파일을 읽고, 요청에 온 원문 키의 SHA-256과 `hmac.compare_digest`로 비교합니다.

관리자 인증은 UI를 숨기는 기능만으로 구현하지 않았습니다. `DELETE /api/guestbook/{id}` 자체에서 인증하므로 화면을 거치지 않고 API를 직접 호출해도 키가 없으면 401입니다. 올바른 해시 문자열 자체를 Bearer 키로 보내도 인증되지 않습니다.

## PVC

`ReadWriteOnce`는 한 노드에서 읽기·쓰기로 볼륨을 마운트하는 모드입니다. 이것만으로 단일 Pod 접근을 보장하는 것은 아니므로 Deployment도 단일 복제본·Recreate로 맞췄습니다. SQLite DB와 WAL이 같은 PVC에 저장됩니다. `storageClassName`을 생략해 기본 StorageClass를 이용하고 `resources.requests.storage: 1Gi`를 요청합니다.

Pod 삭제는 PVC 삭제와 다릅니다. Pod가 바뀌어도 같은 PVC를 사용하면 글이 남습니다. `oc delete -f k8s/`에는 PVC 삭제가 포함되므로 재배포 시 초기 데이터베이스가 생성됩니다. 영속 저장은 백업과 같지 않습니다.

## Probe 3종

| Probe | 경로 | 주기·실패 기준·타임아웃 | 동작 |
|---|---|---|---|
| startup | `/readyz` | 2초 · 30회 · 4초 | 시작 중 DB 준비 확인; 성공 전 다른 Probe를 유예 |
| readiness | `/readyz` | 5초 · 3회 · 4초 | DB 읽기·쓰기 불가 시 준비되지 않은 Pod로 판단 |
| liveness | `/healthz` | 10초 · 3회 · 2초 | 서버 응답 불가 시 컨테이너 재시작 판단 |

`/readyz`는 실제 DB에서 트랜잭션을 열고 읽기·삽입한 뒤 rollback합니다. 쓰기 권한이나 잠금 문제도 확인하면서 테스트 글은 남기지 않습니다. `/healthz`는 DB 장애와 무관한 서버 생존 확인입니다. DB 문제가 생겼다고 생존 Probe까지 실패시켜 무조건 반복 재시작하지 않도록 구분했습니다. 종료 시점과 체크 시간은 클러스터 스케줄링에 따라 달라질 수 있습니다.

## 설명 연습

1. **컨테이너 이미지를 새로 빌드하지 않고 어떻게 화면이 바뀌는가?** 공개 Python 런타임에 ConfigMap으로 코드를 마운트하며, YAML 생성 도구가 content checksum을 바꿔 Deployment를 갱신합니다.
2. **왜 Deployment와 Pod가 모두 필요한가?** Pod는 실제 실행 단위, Deployment는 해당 Pod가 유지되도록 ReplicaSet을 관리하는 선언입니다.
3. **Service가 있는데 Route도 필요한가?** Service는 클러스터 내부의 안정적 연결이고 Route는 외부 HTTP(S) 진입점입니다.
4. **왜 데이터는 ConfigMap에 넣지 않는가?** ConfigMap은 선언된 설정 전달용입니다. 계속 바뀌는 DB는 쓰기 가능한 PVC에 둡니다.
5. **Secret을 공개 저장소에 두어도 되는가?** 원문 키라면 안 됩니다. 여기서는 무작위 고엔트로피 키의 검증 해시만 게시했고, 다른 배포자는 자기 키를 생성합니다.
6. **왜 무중단 RollingUpdate가 아닌가?** 단일 SQLite·RWO 구조에 맞추어 겹치는 Pod 실행을 줄였습니다. 고가용성으로 확장하려면 별도 DB와 배포 전략을 다시 설계해야 합니다.
7. **namespace를 지정하지 않으면 어디에 만들어지는가?** `oc`의 현재 프로젝트에 생성됩니다.
