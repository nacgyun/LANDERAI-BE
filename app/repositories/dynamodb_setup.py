import time

import boto3
from botocore.exceptions import ClientError

from app.config.settings import settings


def _is_local_env() -> bool:
    return settings.APP_ENV == "local"


def _build_dynamodb_resource():
    client_kwargs = {
        "region_name": settings.REGION_NAME,
    }

    if _is_local_env():
        client_kwargs.update(
            {
                "endpoint_url": settings.DYNAMODB_ENDPOINT_URL,
                "aws_access_key_id": "dummy",
                "aws_secret_access_key": "dummy",
                "aws_session_token": None,
            }
        )

    return boto3.resource("dynamodb", **client_kwargs)


dynamodb = _build_dynamodb_resource()

LANDING_REQUEST_USER_INDEX_PROJECTION = [
    "project_id",
    "industry",
    "sub_industry",
    "target",
    "style",
    "goal",
    "status",
    "current_step",
    "progress",
    "selection_status",
    "chosen_variant",
    "updated_at",
]


def _landing_request_user_index_definition() -> dict:
    return {
        "IndexName": settings.LANDING_REQUEST_USER_INDEX_NAME,
        "KeySchema": [
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "created_at", "KeyType": "RANGE"},
        ],
        "Projection": {
            "ProjectionType": "INCLUDE",
            "NonKeyAttributes": LANDING_REQUEST_USER_INDEX_PROJECTION,
        },
        "ProvisionedThroughput": {
            "ReadCapacityUnits": 5,
            "WriteCapacityUnits": 5,
        },
    }


def _ensure_landing_request_user_index(table_name: str) -> None:
    client = dynamodb.meta.client
    table = client.describe_table(TableName=table_name)["Table"]
    indexes = table.get("GlobalSecondaryIndexes", [])
    if any(
        index["IndexName"] == settings.LANDING_REQUEST_USER_INDEX_NAME
        for index in indexes
    ):
        return

    client.update_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexUpdates=[
            {"Create": _landing_request_user_index_definition()}
        ],
    )

    for _ in range(100):
        table = client.describe_table(TableName=table_name)["Table"]
        indexes = table.get("GlobalSecondaryIndexes", [])
        index = next(
            (
                value
                for value in indexes
                if value["IndexName"] == settings.LANDING_REQUEST_USER_INDEX_NAME
            ),
            None,
        )
        if index and index.get("IndexStatus") == "ACTIVE":
            print(
                f"✅ [Infra] {settings.LANDING_REQUEST_USER_INDEX_NAME} "
                "인덱스 로컬 생성 성공"
            )
            return
        time.sleep(0.2)

    raise TimeoutError(
        f"{settings.LANDING_REQUEST_USER_INDEX_NAME} 인덱스가 ACTIVE 상태가 되지 않았습니다."
    )


def create_core_table():
    table_name = settings.CORE_TABLE_NAME
    try:
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST"
        )
        waiter = dynamodb.meta.client.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        print(f"✅ [Infra] {table_name} 테이블 로컬 생성 성공")
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceInUseException":
            print(f"ℹ️ [Infra] {table_name} 테이블이 이미 존재합니다.")
        else:
            raise


# 테이블이 없으면 생성
def init_tables():
    if not _is_local_env():
        return

    create_core_table()

    try:
        table_name = settings.LANDING_REQUEST_TABLE_NAME
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{'AttributeName': 'request_id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[
                {'AttributeName': 'request_id', 'AttributeType': 'S'},
                {'AttributeName': 'user_id', 'AttributeType': 'S'},
                {'AttributeName': 'created_at', 'AttributeType': 'S'},
            ],
            GlobalSecondaryIndexes=[_landing_request_user_index_definition()],
            ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        )
        dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)
        print(f"✅ [Infra] {table_name} 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"ℹ️ [Infra] {table_name} 테이블이 이미 존재합니다.")
        else:
            print(f"❌ [Infra] {table_name} 테이블 생성 실패:", e)

    _ensure_landing_request_user_index(table_name)

    try:
        table_name = settings.LANDING_RESULT_TABLE_NAME
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{'AttributeName': 'result_id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[
                {'AttributeName': 'result_id', 'AttributeType': 'S'},
                {'AttributeName': 'request_id', 'AttributeType': 'S'}
            ],
            # request_id로 결과 데이터를 빠르게 쿼리하기 위한 보조 인덱스 설정
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'RequestIdIndex',
                    'KeySchema': [{'AttributeName': 'request_id', 'KeyType': 'HASH'}],
                    'Projection': {'ProjectionType': 'ALL'},
                    'ProvisionedThroughput': {'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
                }
            ],
            ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        )
        print(f"✅ [Infra] {table_name} 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"ℹ️ [Infra] {table_name} 테이블이 이미 존재합니다.")
        else:
            print(f"❌ [Infra] {table_name} 테이블 생성 실패:", e)

    try:
        table_name = settings.RAG_DESIGN_PLAN_TABLE_NAME
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{'AttributeName': 'design_plan_id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[
                {'AttributeName': 'design_plan_id', 'AttributeType': 'S'},
                {'AttributeName': 'request_id', 'AttributeType': 'S'},
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'RequestIdIndex',
                    'KeySchema': [{'AttributeName': 'request_id', 'KeyType': 'HASH'}],
                    'Projection': {'ProjectionType': 'ALL'},
                    'ProvisionedThroughput': {'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
                }
            ],
            ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        )
        print(f"✅ [Infra] {table_name} 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"ℹ️ [Infra] {table_name} 테이블이 이미 존재합니다.")
        else:
            print(f"❌ [Infra] {table_name} 테이블 생성 실패:", e)


def get_request_table():
    return dynamodb.Table(settings.LANDING_REQUEST_TABLE_NAME)


def get_result_table():
    return dynamodb.Table(settings.LANDING_RESULT_TABLE_NAME)


def get_rag_design_plan_table():
    return dynamodb.Table(settings.RAG_DESIGN_PLAN_TABLE_NAME)


def get_core_table():
    return dynamodb.Table(settings.CORE_TABLE_NAME)
