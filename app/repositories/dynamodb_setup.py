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
            AttributeDefinitions=[{'AttributeName': 'request_id', 'AttributeType': 'S'}],
            ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        )
        print(f"✅ [Infra] {table_name} 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"ℹ️ [Infra] {table_name} 테이블이 이미 존재합니다.")
        else:
            print(f"❌ [Infra] {table_name} 테이블 생성 실패:", e)

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
