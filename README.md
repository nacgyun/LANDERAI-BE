# LANDERAI — Backend

LANDERAI는 입력한 정보를 기반으로 랜딩페이지를 생성하고, 마음에 드는 시안을 선택해 수정하거나 게시할 수 있는 서비스입니다.

이 저장소에는 API와 랜딩페이지 생성·수정 로직을 관리합니다. AWS 리소스 구성이나 Terraform 코드는 별도 인프라 저장소에서 관리합니다.

- [서비스](https://landerai.un-box.click)
- [Infrastructure](https://github.com/nacgyun/LANDERAI-INFRA)
- [Frontend](https://github.com/nacgyun/LANDERAI-FE)

## 1. 기술 구성

| 구분 | 사용 기술 |
| --- | --- |
| API | Python 3.12, FastAPI, Pydantic, Mangum |
| 비동기 처리 | AWS Step Functions, Lambda, SQS |
| 데이터 | DynamoDB, S3, S3 Vectors |
| AI | OpenAI API, Embedding, RAG |
| 인증 | Clerk, Bearer Token 검증 |
| 배포 | GitHub Actions, AWS Lambda |

## 2. 주요 처리 흐름

### 2.1 랜딩페이지 생성

```text
POST /api/v1/requests
  → API Lambda: 요청 저장 및 워크플로 시작
  → Embedding Lambda: 요청 벡터화 및 유사 디자인 계획 검색(top-k 3)
  → Design Plan Lambda: 디자인 계획 생성
  → Variant Lambda: A/B 시안 생성
  → Persist Result Lambda: 결과 및 요청 상태 저장
```

처음에는 하나의 Lambda에서 전체 작업을 처리하는 방식도 고려했습니다. 하지만 어느 단계에서 문제가 발생했는지 확인하기 어렵고, 작업별 재시도 정책을 따로 적용하기도 불편했습니다.

Step Functions를 사용하면 단계별 실행 상태를 볼 수 있고, 실패한 단계에 재시도를 적용할 수 있습니다. 단일 Lambda보다 실행 시간이 약 1초 늘어났지만 이런 점을 고려해 Step Functions를 선택했습니다. A/B 시안은 `Map` 상태에서 생성하며, 설정에 따라 순차 또는 병렬 실행할 수 있습니다.

각 Lambda는 현재 처리 단계와 진행 상태를 DynamoDB에 기록합니다. 오류가 발생하면 실패한 단계와 오류 정보도 함께 남깁니다.

관련 코드: [`app/lambdas/`](app/lambdas/), [`app/pipelines/landing_page/`](app/pipelines/landing_page/)

### 2.2 RAG 기반 디자인 계획

사용자가 선택한 시안의 디자인 계획을 저장하고, 이후 비슷한 요청이 들어왔을 때 참고 자료로 사용합니다.

요청 정보를 Embedding한 뒤 업종별 S3 Vectors 인덱스에서 유사한 사례를 최대 3건 검색합니다. 검색한 사례는 디자인 계획 생성 프롬프트에 함께 전달합니다.

다만 RAG 검색에 실패했다고 랜딩페이지 생성까지 중단할 필요는 없다고 판단했습니다. 그래서 검색 중 오류가 발생하면 로그를 남기고, 참고 사례 없이 다음 단계로 진행합니다.

관련 코드: [`app/repositories/s3_vector_repository.py`](app/repositories/s3_vector_repository.py), [`app/lambdas/embedding_handler.py`](app/lambdas/embedding_handler.py), [`app/lambdas/design_plan_handler.py`](app/lambdas/design_plan_handler.py)

### 2.3 수정 요청과 Revision 관리

수정 요청은 SQS로 전달하고 Revision Worker에서 처리합니다. API에서는 먼저 Revision을 `QUEUED` 상태로 저장하고, Worker가 기준 HTML을 S3에서 가져와 수정 결과를 생성합니다.

- Revision 상태는 `QUEUED → PROCESSING → COMPLETED`로 관리합니다. 실패한 경우에는 오류 정보를 기록합니다.
- 수정할 때마다 새로운 Revision ID와 S3 Key를 사용해 이전 HTML을 보존합니다.
- 완료된 Revision만 다음 수정의 기준으로 사용할 수 있습니다.
- SQS 메시지가 다시 전달되는 경우를 고려해 이미 완료된 작업은 다시 처리하지 않습니다.
- 미리보기와 다운로드에는 만료 시간이 있는 URL을 사용합니다.

현재 공개된 코드에서는 수정 요청을 처리할 때 전체 HTML을 다시 생성합니다. 부분 수정 방식인 PATCH는 별도로 실험했으며, 아직 이 저장소의 수정 API에는 반영하지 않았습니다.

관련 코드: [`app/services/revision_service.py`](app/services/revision_service.py), [`app/lambdas/revision_worker_handler.py`](app/lambdas/revision_worker_handler.py), [`app/repositories/s3_repository.py`](app/repositories/s3_repository.py)

### 2.4 시안 선택과 게시

A/B 시안 중 하나를 선택하면 해당 HTML을 초기 Revision으로 등록합니다. 이때 Revision 생성과 최신 Revision 참조 변경은 DynamoDB 트랜잭션으로 처리합니다. 한쪽만 저장되는 상황을 막기 위해서입니다.

페이지를 게시할 때는 프로젝트 이름으로 slug를 만들고, 중복 여부를 확인한 뒤 서브도메인을 할당합니다. 게시할 HTML은 호스팅용 S3 버킷에 저장하고 CloudFront를 통해 제공합니다.

관련 코드: [`app/services/request_service.py`](app/services/request_service.py), [`app/services/hosting_service.py`](app/services/hosting_service.py), [`app/repositories/hosting_repository.py`](app/repositories/hosting_repository.py)

## 3. 개선 사항

### 3.1 DynamoDB 조회 개선

사용자별 요청 목록을 조회할 때 처음에는 Scan을 사용했습니다. 하지만 Scan은 필요한 사용자 데이터 외에도 테이블의 다른 항목을 읽기 때문에 데이터가 늘어날수록 비용이 커집니다.

이를 해결하기 위해 `UserIdCreatedAtIndex` GSI를 추가하고, `user_id`를 기준으로 Query하도록 변경했습니다.

10,000건의 데이터로 비교한 결과는 다음과 같습니다.

| 지표 | Scan | GSI Query |
| --- | ---: | ---: |
| RCU | 2,231 | 1 |

측정 환경에서 읽기 용량 사용량이 크게 줄었습니다. 현재 요청 목록 조회 코드에는 `LastEvaluatedKey`를 이용한 페이지네이션도 적용되어 있습니다.

관련 코드: [`app/repositories/request_repository.py`](app/repositories/request_repository.py), [INFRA DynamoDB 모듈](https://github.com/nacgyun/LANDERAI-INFRA/blob/master/modules/dynamodb/main.tf)

### 3.2 HTML 수정 방식 개선 실험

기존 FULL 방식은 버튼 색상이나 문구처럼 일부만 바꾸는 요청에도 HTML 전체를 다시 생성합니다. 작은 수정에도 출력 토큰이 많이 발생하고 생성 시간이 길어진다는 점이 아쉬웠습니다.

그래서 변경할 부분만 PATCH 목록으로 생성하고, Python 엔진에서 기존 HTML에 적용하는 방식을 별도로 실험했습니다.

| 지표 | FULL | PATCH | 변화 |
| --- | ---: | ---: | ---: |
| 총 입력 토큰 | 663,903 | 845,782 | 27.4% 증가 |
| 총 출력 토큰 | 670,638 | 58,173 | 91.3% 감소 |
| 총 토큰 | 1,334,541 | 903,955 | 32.3% 감소 |
| 평균 생성 시간 | 36.08초 | 5.94초 | 83.5% 감소 |

135건의 본실험에서는 132건이 평가 요구사항을 충족했습니다(97.8%). 입력 토큰은 늘었지만 출력 토큰이 크게 줄면서 전체 토큰과 생성 시간도 감소했습니다.

이 결과는 별도로 진행한 PATCH 실험 기준입니다. 현재 공개된 BE 코드에는 FULL 방식이 적용되어 있으며, PATCH 실험 코드와 재현 자료는 이 저장소에 포함되어 있지 않습니다.

## 4. 프로젝트 구조

```text
app/
├── common/              # 인증, 공통 유틸리티, 상태 값
├── config/              # 환경 설정 및 비밀정보 조회
├── lambdas/             # Lambda 진입점
├── messaging/           # Revision 이벤트 발행
├── pipelines/           # 랜딩페이지 생성 파이프라인
├── prompts/             # 디자인 계획·HTML 생성·수정 프롬프트
├── repositories/        # DynamoDB, S3, S3 Vectors 접근
├── routers/             # HTTP API
├── schemas/             # 요청 및 응답 스키마
└── services/            # 요청, 수정, 게시 등 비즈니스 로직
tests/                   # Revision 관련 테스트
.github/workflows/       # Lambda 배포
```

## 5. PR 및 변경 관리

기능 추가나 오류 수정 시에는 PR 템플릿을 사용해 변경 이유와 구현 내용을 정리합니다.

Lambda가 공통 모듈을 공유하기 때문에 한 파일의 수정이 다른 Lambda에 영향을 줄 수 있습니다. 그래서 PR에서 다음 항목도 함께 확인하도록 구성했습니다.

- 로컬 테스트, Lambda 패키징, 관련 로그 확인 여부
- 영향을 받는 Lambda
- 환경변수, DynamoDB 스키마, Step Functions 입출력 변경 여부
- IAM 권한 변경 여부

관련 파일: [PR 템플릿](.github/PULL_REQUEST_TEMPLATE.md)

## 6. CI/CD 및 배포

`develop` 브랜치는 dev, `main` 브랜치는 prod 환경에 연결합니다. 해당 브랜치에 push가 발생하면 GitHub Actions에서 의존성과 애플리케이션 코드를 ZIP으로 패키징하고, 6개 Lambda의 코드를 업데이트합니다.

배포할 때는 장기 액세스 키 대신 GitHub OIDC를 통해 IAM Role을 수임합니다. Lambda, Step Functions, SQS 등 AWS 리소스와 권한 설정은 [LANDERAI-INFRA](https://github.com/nacgyun/LANDERAI-INFRA)에서 관리합니다.

관련 코드: [`.github/workflows/deploy-lambda.yml`](.github/workflows/deploy-lambda.yml)
