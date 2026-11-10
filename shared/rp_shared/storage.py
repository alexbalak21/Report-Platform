"""Object storage shared by the services: local disk (dev/tests) and S3/MinIO.

`cfg` for make_storage is any object with storage_backend, local_storage_dir, s3_endpoint,
s3_bucket, s3_access_key, s3_secret_key and s3_region (ingestion and report both have one).
"""

from __future__ import annotations

import os
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)

    def _path(self, key: str) -> str:
        path = os.path.abspath(os.path.join(self.root, key))
        if os.path.commonpath([self.root, path]) != self.root:  # path traversal guard
            raise ValueError("invalid storage key")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as fh:
            return fh.read()

    def delete(self, key: str) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass


class S3Storage:
    """Works with AWS S3 and MinIO. The bucket is created on first use if it doesn't exist."""

    def __init__(self, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket
        self._ready = False

    @classmethod
    def from_config(cls, cfg) -> S3Storage:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint,
            aws_access_key_id=cfg.s3_access_key,
            aws_secret_access_key=cfg.s3_secret_key,
            region_name=cfg.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        return cls(client, cfg.s3_bucket)

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:  # 404 -> create it (MinIO starts empty)
            self.client.create_bucket(Bucket=self.bucket)
        self._ready = True

    def put(self, key: str, data: bytes) -> None:
        self._ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def make_storage(cfg) -> Storage:
    if cfg.storage_backend == "s3":
        return S3Storage.from_config(cfg)
    return LocalStorage(cfg.local_storage_dir)
