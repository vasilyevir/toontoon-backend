"""S3-compatible backend — MinIO for us, unchanged for any other provider."""
from __future__ import annotations

import logging
from typing import Optional

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings
from app.storage.base import Storage

logger = logging.getLogger("arteki.storage")


class S3Storage(Storage):
    def __init__(self) -> None:
        self._session = aioboto3.Session()
        self._bucket = settings.s3_bucket
        # MinIO needs path-style addressing: bucket.localhost does not resolve.
        self._config = Config(signature_version="s3v4", s3={"addressing_style": "path"})

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=self._config,
        )

    async def ensure_bucket(self) -> None:
        """Create the bucket on first run so a fresh environment just works.

        The bucket is left private — no public read policy is ever applied. That
        is the whole point of the migration away from a served ``uploads/`` dir.
        """
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError:
                await s3.create_bucket(Bucket=self._bucket)
                logger.info("Created bucket %s", self._bucket)

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        return key

    async def get(self, key: str) -> Optional[bytes]:
        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self._bucket, Key=key)
            except ClientError:
                return None
            async with obj["Body"] as stream:
                return await stream.read()

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def signed_url(self, key: str, *, ttl_seconds: Optional[int] = None) -> str:
        ttl = ttl_seconds or settings.s3_signed_url_ttl_seconds
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl,
            )

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError:
                return False
