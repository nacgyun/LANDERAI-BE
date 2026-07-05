import boto3
from botocore.exceptions import ClientError

from app.config.settings import settings

dynamodb = boto3.resource(
    "dynamodb",
    endpoint_url=settings.DYNAMODB_ENDPOINT_URL,
    region_name=settings.REGION_NAME,
    aws_access_key_id="dummy",          # 로컬 개발용 가짜 자격 증명
    aws_secret_access_key="dummy",      # 로컬 개발용 가짜 자격 증명
    aws_session_token=None              # 윈도우 실제 AWS 토큰 간섭 차단
)


def create_core_table():
    table_name = "coreTable"
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
        print("✅ [Infra] coreTable 테이블 로컬 생성 성공")
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceInUseException":
            print("ℹ️ [Infra] coreTable 테이블이 이미 존재합니다.")
        else:
            raise


# 테이블이 없으면 생성
def init_tables():
    create_core_table()

    try:
        dynamodb.create_table(
            TableName='LandingRequest',
            KeySchema=[{'AttributeName': 'request_id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'request_id', 'AttributeType': 'S'}],
            ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        )
        print("✅ [Infra] LandingRequest 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print("ℹ️ [Infra] LandingRequest 테이블이 이미 존재합니다.")
        else:
            print("❌ [Infra] LandingRequest 테이블 생성 실패:", e)

    try:
        dynamodb.create_table(
            TableName='LandingResult',
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
        print("✅ [Infra] LandingResult 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print("ℹ️ [Infra] LandingResult 테이블이 이미 존재합니다.")
        else:
            print("❌ [Infra] LandingResult 테이블 생성 실패:", e)

    try:
        dynamodb.create_table(
            TableName='RAG_DESIGNPLAN',
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
        print("✅ [Infra] RAG_DESIGNPLAN 테이블 로컬 생성 성공")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print("ℹ️ [Infra] RAG_DESIGNPLAN 테이블이 이미 존재합니다.")
        else:
            print("❌ [Infra] RAG_DESIGNPLAN 테이블 생성 실패:", e)


def get_request_table():
    return dynamodb.Table('LandingRequest')


def get_result_table():
    return dynamodb.Table('LandingResult')


def get_rag_design_plan_table():
    return dynamodb.Table('RAG_DESIGNPLAN')


def get_core_table():
    return dynamodb.Table("coreTable")
