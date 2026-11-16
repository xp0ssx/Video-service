import os
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import Database
from app.infrastructure.storage import make_s3_client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Compose services required"),
]


def test_postgresql_connection_and_migration():
    database = Database(get_settings())
    try:
        database.check_ready()
        with database.session_factory() as session:
            assert session.execute(text("SELECT current_database()")).scalar_one() == (
                get_settings().postgres_db
            )
            assert session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("694349d098b8")
    finally:
        database.close()


def test_authenticated_bucket_creation():
    settings = get_settings()
    client = make_s3_client(
        settings,
        access_key=os.environ["S3_ADMIN_ACCESS_KEY"],
        secret_key=os.environ["S3_ADMIN_SECRET_KEY"],
    )
    bucket = f"video-service-check-{uuid4().hex}"
    try:
        client.create_bucket(Bucket=bucket)
        client.head_bucket(Bucket=bucket)
    finally:
        client.close()


def test_application_put_head_get_range_and_anonymous_denial():
    settings = get_settings()
    client = make_s3_client(settings)
    anonymous = make_s3_client(settings, anonymous=True)
    key = f"checks/{uuid4().hex}/probe.bin"
    content = bytes(range(256)) * 32
    try:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=content)
        head = client.head_object(Bucket=settings.s3_bucket, Key=key)
        assert head["ContentLength"] == len(content)
        response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        with response["Body"] as body:
            assert body.read() == content
        response = client.get_object(Bucket=settings.s3_bucket, Key=key, Range="bytes=10-29")
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 206
        assert response["ContentRange"] == f"bytes 10-29/{len(content)}"
        with response["Body"] as body:
            assert body.read() == content[10:30]
        for operation in (
            lambda: anonymous.head_bucket(Bucket=settings.s3_bucket),
            lambda: anonymous.head_object(Bucket=settings.s3_bucket, Key=key),
            lambda: anonymous.get_object(Bucket=settings.s3_bucket, Key=key),
            lambda: anonymous.get_object(Bucket=settings.s3_bucket, Key=key, Range="bytes=0-9"),
            lambda: anonymous.put_object(
                Bucket=settings.s3_bucket, Key=key + ".anonymous", Body=b"x"
            ),
        ):
            with pytest.raises(ClientError) as denied:
                operation()
            assert denied.value.response["ResponseMetadata"]["HTTPStatusCode"] in (401, 403)
    finally:
        client.close()
        anonymous.close()
