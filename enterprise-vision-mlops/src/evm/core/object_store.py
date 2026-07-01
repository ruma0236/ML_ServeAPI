from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.core.config import get_nested


class ObjectStoreUnavailable(RuntimeError):
    """Raised when the configured object store cannot be used."""


@dataclass(frozen=True)
class ObjectRef:
    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


def parse_s3_uri(uri: str) -> ObjectRef:
    if not uri.startswith("s3://"):
        raise ValueError(f"Expected s3:// URI, got: {uri}")
    bucket_and_key = uri.removeprefix("s3://")
    bucket, _, key = bucket_and_key.partition("/")
    if not bucket or not key:
        raise ValueError(f"Expected s3://bucket/key URI, got: {uri}")
    return ObjectRef(bucket=bucket, key=key)


class ObjectStoreClient:
    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str = "us-east-1",
    ) -> None:
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region_name = region_name
        self._client: Any | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ObjectStoreClient":
        endpoint_url = str(get_nested(config, "object_store.endpoint_url", "http://localhost:9000"))
        region_name = str(get_nested(config, "object_store.region_name", "us-east-1"))
        access_key_id = (
            os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("MINIO_ROOT_USER")
            or str(get_nested(config, "object_store.access_key_id", "minioadmin"))
        )
        secret_access_key = (
            os.getenv("AWS_SECRET_ACCESS_KEY")
            or os.getenv("MINIO_ROOT_PASSWORD")
            or str(get_nested(config, "object_store.secret_access_key", "minioadmin123"))
        )
        return cls(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region_name=region_name,
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ModuleNotFoundError as exc:
                raise ObjectStoreUnavailable(
                    "boto3 is required for MinIO/S3 access. Install project dependencies "
                    "or run the pipeline inside the Airflow container."
                ) from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region_name,
                config=Config(s3={"addressing_style": "path"}),
            )
        return self._client

    def uri(self, bucket: str, key: str) -> str:
        return ObjectRef(bucket=bucket, key=key.strip("/")).uri

    def ensure_bucket(self, bucket: str) -> str:
        try:
            self.client.head_bucket(Bucket=bucket)
        except Exception:
            self.client.create_bucket(Bucket=bucket)
        return bucket

    def ensure_buckets(self, buckets: list[str]) -> list[str]:
        return [self.ensure_bucket(bucket) for bucket in buckets]

    def upload_file(self, path: Path, bucket: str, key: str, content_type: str | None = None) -> str:
        extra_args: dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.upload_file(
            str(path),
            bucket,
            key.strip("/"),
            ExtraArgs=extra_args or None,
        )
        return self.uri(bucket, key)

    def put_json(self, payload: dict[str, Any] | list[Any], bucket: str, key: str) -> str:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.client.put_object(
            Bucket=bucket,
            Key=key.strip("/"),
            Body=body,
            ContentType="application/json",
        )
        return self.uri(bucket, key)

    def object_exists(self, uri: str) -> bool:
        ref = parse_s3_uri(uri)
        try:
            self.client.head_object(Bucket=ref.bucket, Key=ref.key)
            return True
        except Exception:
            return False

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix.strip("/")):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys


def configured_buckets(config: dict[str, Any]) -> list[str]:
    buckets = [
        str(get_nested(config, "object_store.raw_bucket", "raw")),
        str(get_nested(config, "object_store.processed_bucket", "processed")),
        str(get_nested(config, "object_store.validated_bucket", "validated")),
        str(get_nested(config, "object_store.artifact_bucket", "mlflow-artifacts")),
    ]
    return list(dict.fromkeys(bucket for bucket in buckets if bucket))
