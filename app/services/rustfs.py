from __future__ import annotations

import uuid
from typing import Optional

import boto3
from botocore.client import Config
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings


class RustFSClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.rustfs_base_url:
            raise RuntimeError("RUSTFS_BASE_URL not configured")
        if not settings.rustfs_api_key or not settings.rustfs_secret:
            raise RuntimeError("RUSTFS_API_KEY and RUSTFS_SECRET must be configured")

        self.base_url = settings.rustfs_base_url.rstrip("/")
        self.bucket = settings.rustfs_bucket or "aistudio"

        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.base_url,
            aws_access_key_id=settings.rustfs_api_key,
            aws_secret_access_key=settings.rustfs_secret,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

    def _object_url(self, key: str) -> str:
        return f"{self.base_url}/{self.bucket}/{key}"

    async def upload_file(self, file_bytes: bytes, filename: str, content_type: Optional[str] = None) -> str:
        key = f"uploads/{uuid.uuid4()}_{filename}"

        def _put() -> None:
            extra = {"ContentType": content_type} if content_type else None
            if extra is not None:
                self._s3.put_object(Bucket=self.bucket, Key=key, Body=file_bytes, **extra)
            else:
                self._s3.put_object(Bucket=self.bucket, Key=key, Body=file_bytes)

        await run_in_threadpool(_put)
        return self._object_url(key)

    async def generate_presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        def _sign() -> str:
            return self._s3.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
            )

        return await run_in_threadpool(_sign)

    async def generate_presigned_put_url(self, key: str, content_type: Optional[str] = None, expires_in: int = 900) -> str:
        def _sign() -> str:
            params = {"Bucket": self.bucket, "Key": key}
            if content_type:
                params["ContentType"] = content_type
            return self._s3.generate_presigned_url("put_object", Params=params, ExpiresIn=expires_in)

        return await run_in_threadpool(_sign)


def get_rustfs_client() -> RustFSClient:
    return RustFSClient()
