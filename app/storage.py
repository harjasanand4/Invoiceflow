"""File storage: a local folder in development, S3 in production. Same interface for both."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.config import Settings


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> str: ...
    def read(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"Invalid storage key: {key}")
        return path

    def save(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:
    def __init__(self, bucket: str, prefix: str = ""):
        import boto3  # imported lazily so boto3 is only needed when S3 is used

        self.client = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix

    def save(self, key: str, data: bytes) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self.prefix + key,
            Body=data,
            ContentType="application/pdf",
            ServerSideEncryption="AES256",
        )
        return key

    def read(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self.prefix + key)
            return True
        except ClientError:
            return False


def build_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 requires S3_BUCKET")
        return S3Storage(settings.s3_bucket, settings.s3_prefix)
    return LocalStorage(settings.storage_dir)
