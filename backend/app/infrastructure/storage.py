import boto3
from botocore import UNSIGNED
from botocore.config import Config

from app.core.config import Settings


def make_s3_client(
    settings: Settings,
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
    anonymous: bool = False,
):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=access_key or settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=secret_key or settings.s3_secret_key.get_secret_value(),
        config=Config(
            signature_version=UNSIGNED if anonymous else "s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 1},
        ),
    )


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client = make_s3_client(settings)

    def check_ready(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)

    def head(self, key):
        return self.client.head_object(Bucket=self.bucket, Key=key)

    def upload(self, path, key, content_type):
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def download(self, key, path):
        self.client.download_file(self.bucket, key, str(path))

    def close(self) -> None:
        self.client.close()
