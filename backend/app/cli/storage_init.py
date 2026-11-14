import os
import time

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.infrastructure.storage import make_s3_client


def initialize_storage() -> None:
    settings = get_settings()
    admin = make_s3_client(
        settings,
        access_key=os.environ["S3_ADMIN_ACCESS_KEY"],
        secret_key=os.environ["S3_ADMIN_SECRET_KEY"],
    )
    anonymous = make_s3_client(settings, anonymous=True)
    application = make_s3_client(settings)
    try:
        for attempt in range(30):
            try:
                admin.list_buckets()
                break
            except (BotoCoreError, ClientError):
                if attempt == 29:
                    raise RuntimeError("Authenticated S3 did not become ready") from None
                time.sleep(2)
        try:
            admin.head_bucket(Bucket=settings.s3_bucket)
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise RuntimeError("S3 bucket check failed") from None
            admin.create_bucket(Bucket=settings.s3_bucket)
        application.head_bucket(Bucket=settings.s3_bucket)
        try:
            anonymous.head_bucket(Bucket=settings.s3_bucket)
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] not in (401, 403):
                raise RuntimeError("Anonymous denial could not be verified") from None
        else:
            raise RuntimeError("Unsafe storage: anonymous bucket access is enabled")
        print("S3 initialized: authenticated bucket access OK, anonymous access denied")
    finally:
        admin.close()
        anonymous.close()
        application.close()


if __name__ == "__main__":
    initialize_storage()
