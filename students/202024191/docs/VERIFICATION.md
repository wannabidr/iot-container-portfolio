# 실제 검증 기록

검증일: **2026-10-26 KST**. 대상은 Red Hat Developer Sandbox의 `sungwooki9-dev` 프로젝트와 실제 HTTPS Route입니다. 아래 JSON의 시각은 UTC(`+00:00`)이며 이 문서의 표는 한국 시각으로 환산했습니다. 화면과 명령 출력은 실제 실행 결과입니다.

## 결과

| 검증 | 실제 결과 | 근거 |
|---|---|---|
| 로컬 자동 테스트 | 20개 통과, 8.798초 | [unit-tests.txt](unit-tests.txt), [테스트 원본](../tests/) |
| JavaScript 문법 | `app.js`, `admin.js` 모두 통과 | `node --check` 실행 |
| YAML 사전 검증 | 서버 측 dry-run에서 Object 7개 허용 | `oc apply --dry-run=server -f k8s/` 실행 |
| 실제 서비스 + Pod 교체 | 24개 점검 통과, 19:26:35–19:27:02 | [live-verification.json](live-verification.json) |
| 전체 삭제·재배포 | 19:36:27–19:37:05 성공 | [redeploy-verification.json](redeploy-verification.json) |
| 실제 DB 장애 상황 | Ready True → False → True, 재시작 0회 | [probe-verification.json](probe-verification.json) |
| 재배포 이후 API 재검증 | 21개 점검 통과, 19:38:26–19:38:41 | [final-live-verification.json](final-live-verification.json) |
| 최종 클러스터 | Deployment 1/1, Pod Running 1/1, PVC Bound, Service endpoint 존재 | [cluster-status.txt](cluster-status.txt) |

24개와 21개는 일부가 겹치는 별도 실행 기록입니다. 서로 다른 45개 기능을 검증했다는 의미는 아닙니다.

## 배포·연결·실행 권한

![실제 OpenShift 토폴로지와 Running Pod](../images/08-openshift-topology.jpg)

- Deployment `portfolio-202024191`: Ready 1/1.
- Pod `portfolio-202024191-577d9d4cf6-666kb`: Running, Ready 1/1, 재시작 0.
- Service `portfolio-202024191`: 8080/TCP, EndpointSlice에 준비된 Pod 주소 존재.
- Route: `edge/Redirect`, HTTPS에서 포트폴리오·API 응답 정상.
- PVC `portfolio-202024191-data`: Bound, 1Gi, RWO. 실제 클러스터가 기본 `gp3`를 선택했으며 제출 YAML에는 StorageClass를 고정하지 않았습니다.
- 실행 계정: `uid=1010470000`, 보안 제약 `restricted-v2`. UID는 0이 아닙니다. UBI의 기본 root group 소속은 root 사용자 실행과 다릅니다.
- 실제 런타임과 Probe 로그: [cluster-status.txt](cluster-status.txt).

`oc get all`은 이 Sandbox에서 앱 리소스를 출력한 뒤, `applications.app.k8s.io` 목록 권한 부족으로 종료 코드 1을 반환했습니다. [원래 출력](oc-get-all.txt)을 남겼으며 Deployment·ReplicaSet·Pod·Service·Route와 ConfigMap·Secret·PVC를 **종류별로 명시하여 조회**한 결과는 모두 정상입니다. 이 오류를 앱 배포 실패나 성공한 명령으로 바꾸어 기록하지 않았습니다.

## 방명록과 인증

실제 Route에서 다음을 확인했습니다.

- `/`, `/admin`, `/healthz`, `/readyz`, `/api/config` 200.
- 이름이 공백인 입력, 501자 메시지 400.
- 다른 Origin의 변경 요청 403.
- 잘못된 키·인증 없는 삭제 401, 올바른 키 확인 200.
- JSON 작성 201 → 목록에서 이름·내용·ID·작성 시각 확인.
- `<script>`와 SQL 특수 문자는 데이터로 그대로 저장되고 화면에서 일반 텍스트로 표시됨.
- 관리자 삭제 204 → 목록에서 사라짐 → 같은 ID 재삭제 404.
- `/server.py`, `/config.json`, `/data/guestbook.sqlite3`, `/.local/admin-key.txt` 직접 접근 404.
- 테스트가 생성한 글만 삭제했으며 최종 상태는 빈 방명록입니다.

자동 테스트는 위 동작에 더해 한글·이모지, 정확한 입력 경계, malformed JSON·과대 본문·잘못된 형식, cursor 중복 방지, 동시 작성 12개, DB 용량·빈도 제한, Secret 파일 누락, DB 오류와 liveness 분리를 다룹니다.

## Pod를 교체해도 데이터가 남는지

실서비스 검증 도구가 고유한 검증용 글을 작성한 후 해당 앱 Pod를 삭제했습니다. Deployment가 만든 새 Pod에서 **같은 ID·내용·시각**의 글을 확인하고 관리자 API로 삭제했습니다.

| 항목 | 값 |
|---|---|
| 이전 Pod UID | `e61ff459-6bbd-4731-b877-a1d720afd7e3` |
| 교체 Pod UID | `4eea1ffb-afd5-482b-88e4-1ee70f6c0661` |
| 유지된 PVC UID | `73e8de01-e856-4166-a5a3-fc3969bd900b` |
| 메시지 비교 | 전체 entry 객체 동일, PASS |

Pod UID의 정확한 값은 [실행 JSON](live-verification.json)의 `replacement Pod has a new UID` 항목에 기록되어 있습니다. 데이터는 Pod 내부 임시 파일이 아니라 같은 PVC에서 복원되었습니다.

## `k8s/`만으로 완전히 재현되는지

1. 최종 앱·YAML을 Git index에 올린 뒤 `git checkout-index`로 별도 임시 폴더에 추출했습니다.
2. 그중 `k8s/`만 다른 격리 폴더로 복사했습니다. 실행 디렉터리에는 **`k8s` 하나만** 있었습니다.
3. 방명록이 비어 있는 것을 먼저 확인하고, 이 과제 YAML의 Object 7개를 삭제했습니다.
4. PVC 삭제 완료 후 해당 격리 폴더에서 `oc apply -f k8s/`와 rollout 확인을 실행했습니다.
5. 새 PVC UID가 생기고 방명록이 빈 상태로 응답하는 것을 확인했습니다.
6. 재배포에 사용한 YAML 7개의 SHA-256을 기록하고 제출본과 바이트 단위로 일치하는지 확인했습니다.

이 검증은 관리자 원문 키·로컬 DB·앱 소스 폴더·생성 스크립트 없이 배포가 된다는 것을 확인합니다. [재배포 기록](redeploy-verification.json)의 `manifest_sha256`으로 제출 파일과 비교할 수 있습니다. 이후 변경은 증빙·설명 문서에 한정하며, 최종 공개 Git clone에서도 다시 적용해 확인합니다.

## DB 장애 시 Probe가 구분해서 동작하는지

실제 컨테이너에서 SQLite 쓰기 트랜잭션을 35초간 잠근 뒤 rollback했습니다. 데이터 파일이나 메시지는 삭제하지 않았습니다.

| 단계 | `/healthz` | `/readyz` | Pod Ready | 재시작 |
|---|---:|---:|---|---:|
| 정상 | 200 | 200 | True | 0 |
| DB 쓰기 잠금 유지 | 200 | 503 | False | 0 |
| 잠금 해제 후 | 200 | 200 | True | 0 |

readiness가 DB 쓰기 불가를 반영하고, 생존 중인 서버를 liveness가 불필요하게 재시작하지 않는 것을 확인했습니다. 시작 Probe와 정상적인 readiness/liveness 요청은 컨테이너 로그에도 200으로 남았습니다. 서버 프로세스를 강제로 중지해 liveness 재시작까지 유도하는 별도 실험은 수행하지 않았습니다.

## 화면·키보드 확인

| 확인 항목 | 결과 |
|---|---|
| 1440×1000 데스크톱 | 첫 화면·WORKWAY·방명록·관리자 화면 확인 |
| 390×844 모바일 | 메뉴 펼침·섹션 이동·메뉴 닫힘·폼 확인, 가로 넘침 없음 |
| 320×760 모바일 | 문서 가로 폭 320px, 화면 밖으로 넘치는 주요 요소 없음 |
| 방명록 폼 | 이름/메시지 label, 필수 입력, maxlength 40/500, 등록 후 성공 안내 |
| 스크립트 입력 | 실행되지 않고 `<script>…</script>` 문자열로 표시 |
| 관리자 | 잘못된 키 오류, 올바른 키 인증, 글 개별 삭제, 관리 종료 확인 |
| 삭제 확인창 | 초점이 취소 버튼으로 이동, Escape로 취소 가능 |
| 키보드 진입 | 첫 Tab에서 본문 바로가기, Enter로 본문 이동 |
| 브라우저 콘솔 | 마지막 포트폴리오 확인 시 수집된 error 로그 없음 |

실제 화면: [첫 화면](../images/01-desktop-home.jpg), [대표 프로젝트](../images/02-workway.jpg), [모바일](../images/03-mobile-home.jpg), [모바일 방명록](../images/04-mobile-guestbook.jpg), [작성 결과](../images/05-desktop-guestbook.jpg), [관리자](../images/06-admin-authenticated.jpg), [삭제 결과](../images/07-admin-delete.jpg).

## 공개 전 확인

- Git 추적 파일이 `students/202024191/` 안에만 존재함.
- `.local/admin-key.txt`, 로컬 DB, 캐시·실행 로그 제외.
- 실제 원문 관리자 키가 추적 파일에 포함되지 않음.
- 비공개 저장소 링크·원본 코드·고객 자료·성능 수치 미포함.
- 앱 원본과 생성 YAML 일치, Object 간 이름·포트·selector 일치.
- 배포 파일의 namespace·고정 host·자동 생성 필드·특정 StorageClass 미포함.

검증 스크린샷은 각 기능을 확인한 시점의 화면입니다. 전체 재배포 전후로 앱 파일은 동일하며, 테스트 데이터와 Pod/PVC 식별자는 실험 단계에 따라 다릅니다.
