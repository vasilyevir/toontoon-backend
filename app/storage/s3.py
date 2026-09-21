"""S3-compatible backend — MinIO for us, unchanged for any other provider."""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Optional

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings
from app.storage.base import Storage

logger = logging.getLogger("toontoon.storage")


class S3Storage(Storage):
    def __init__(self) -> None:
        self._session = aioboto3.Session()
        self._bucket = settings.s3_bucket
        # MinIO needs path-style addressing: bucket.localhost does not resolve.
        #
        # Сроки и повторы заданы явно. Без них один запрос к хранилищу в
        # другой стране висел полминуты, и витринная картинка не приходила
        # вовсе (замер 21 сентября 2026). Пул шире умолчания: одна главная —
        # это десятки картинок разом.
        self._config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=20,
            retries={"max_attempts": 3, "mode": "standard"},
            max_pool_connections=32,
        )
        self._shared: Optional[tuple[asyncio.AbstractEventLoop, AsyncExitStack, object]] = None
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _new_client(self):
        return self._session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=self._config,
        )

    @asynccontextmanager
    async def _client(self):
        """Одно соединение с хранилищем на процесс, а не новое на каждый запрос.

        Раньше клиент создавался заново на каждое чтение — и каждое чтение
        платило за новое TLS-соединение. Пока хранилищем был MinIO в соседнем
        контейнере, это ничего не стоило. После переезда в Yandex Object
        Storage (Казахстан) каждое такое рукопожатие шло через границу:
        витринная картинка — 0,5–2,5 с, и главная при первом запуске не
        успевала показаться до экрана награды (задача #7 Андрея, 21 сентября
        2026). Клиент botocore для этого и создан: живёт долго, держит пул.

        Клиент привязан к циклу событий. Скрипты и тесты заводят свой цикл —
        для него и заводится свой клиент.
        """
        loop = asyncio.get_running_loop()
        shared = self._shared
        if shared is None or shared[0] is not loop:
            if self._lock is None or self._lock_loop is not loop:
                self._lock, self._lock_loop = asyncio.Lock(), loop
            async with self._lock:
                shared = self._shared
                if shared is None or shared[0] is not loop:
                    stack = AsyncExitStack()
                    client = await stack.enter_async_context(self._new_client())
                    self._shared = shared = (loop, stack, client)
        yield shared[2]

    async def close(self) -> None:
        """Закрыть соединение — при остановке процесса."""
        shared, self._shared = self._shared, None
        if shared is not None:
            await shared[1].aclose()

    async def ensure_bucket(self) -> None:
        """Create the bucket on first run so a fresh environment just works.

        The bucket is left private — no public read policy is ever applied. That
        is the whole point of the migration away from a served ``uploads/`` dir.
        """
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError:
                # Не смогли увидеть — не значит, что его нет. У ключа облачного
                # хранилища (Yandex Object Storage) права только на свой бакет,
                # а не на каталог: создать он не может ничего, и отказ здесь
                # ронял бы запуск сервера из-за одного сетевого сбоя. Бакет там
                # заводит администратор; настоящая беда всплывёт на первой же
                # записи, с понятной ошибкой, а не молчаливым падением API.
                try:
                    await s3.create_bucket(Bucket=self._bucket)
                    logger.info("Created bucket %s", self._bucket)
                except ClientError as e:
                    logger.warning("Bucket %s: не удалось ни увидеть, ни создать (%s) — "
                                   "работаем дальше", self._bucket,
                                   e.response.get("Error", {}).get("Code"))

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
