"""Перенести все объекты из одного S3-хранилища в другое.

Написан под переезд с MinIO на сервере в Yandex Object Storage (сентябрь 2026),
но ни про одно из них не знает: откуда и куда — из переменных окружения.

    SRC_ENDPOINT, SRC_REGION, SRC_BUCKET, SRC_ACCESS_KEY, SRC_SECRET_KEY
    DST_ENDPOINT, DST_REGION, DST_BUCKET, DST_ACCESS_KEY, DST_SECRET_KEY

    python scripts/copy_storage.py            # только посчитать, что поедет
    python scripts/copy_storage.py --apply    # перенести

Повторяемый: объект, который в назначении уже есть того же размера,
пропускается. Отсюда и главный приём переезда — скопировать всё, переключить
сервер, а затем запустить ещё раз: второй проход довезёт только то, что успело
лечь в старое хранилище между копированием и переключением.

Кроме байтов переносит тип содержимого и пользовательские метаданные — без
типа картинка ушла бы в приложение как `binary/octet-stream`.

Ключи не печатает никогда: в выводе только имена объектов и числа.
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def клиент(приставка: str):
    e = os.environ
    return boto3.client(
        "s3",
        endpoint_url=e[f"{приставка}_ENDPOINT"],
        region_name=e.get(f"{приставка}_REGION") or "us-east-1",
        aws_access_key_id=e[f"{приставка}_ACCESS_KEY"],
        aws_secret_access_key=e[f"{приставка}_SECRET_KEY"],
        # MinIO без path-style не отвечает; Яндексу всё равно.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      retries={"max_attempts": 5, "mode": "standard"}),
    )


def все_объекты(s3, бакет: str) -> dict[str, int]:
    """Ключ → размер. Постранично: список за раз отдаётся по тысяче."""
    найдено: dict[str, int] = {}
    for страница in s3.get_paginator("list_objects_v2").paginate(Bucket=бакет):
        for объект in страница.get("Contents", []):
            найдено[объект["Key"]] = объект["Size"]
    return найдено


def перенести(откуда, куда, из_бакета: str, в_бакет: str, ключ: str) -> None:
    объект = откуда.get_object(Bucket=из_бакета, Key=ключ)
    доп = {}
    if объект.get("ContentType"):
        доп["ContentType"] = объект["ContentType"]
    if объект.get("CacheControl"):
        доп["CacheControl"] = объект["CacheControl"]
    if объект.get("Metadata"):
        доп["Metadata"] = объект["Metadata"]
    куда.put_object(Bucket=в_бакет, Key=ключ, Body=объект["Body"].read(), **доп)


def main() -> int:
    применить = "--apply" in sys.argv
    откуда, куда = клиент("SRC"), клиент("DST")
    из_бакета, в_бакет = os.environ["SRC_BUCKET"], os.environ["DST_BUCKET"]

    источник = все_объекты(откуда, из_бакета)
    назначение = все_объекты(куда, в_бакет)
    нужно = [к for к, размер in источник.items() if назначение.get(к) != размер]

    print(f"в источнике: {len(источник)} объектов, "
          f"{sum(источник.values()) / 1e6:.1f} МБ")
    print(f"уже на месте: {len(источник) - len(нужно)}, перенести: {len(нужно)} "
          f"({sum(источник[к] for к in нужно) / 1e6:.1f} МБ)")
    if not применить:
        print("пробный прогон — ничего не перенесено; --apply, чтобы перенести")
        return 0

    сбои: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as пул:
        задачи = {пул.submit(перенести, откуда, куда, из_бакета, в_бакет, к): к for к in нужно}
        for готово, задача in enumerate(as_completed(задачи), 1):
            ключ = задачи[задача]
            try:
                задача.result()
            except ClientError as e:
                сбои.append((ключ, e.response.get("Error", {}).get("Code", "?")))
            if готово % 200 == 0:
                print(f"  … {готово}/{len(нужно)}")

    # Сверка — заново, по назначению, а не по нашему счётчику: считается то,
    # что лежит в новом хранилище, а не то, что мы думаем, что туда положили.
    после = все_объекты(куда, в_бакет)
    не_хватает = [к for к, размер in источник.items() if после.get(к) != размер]
    print(f"перенесено: {len(нужно) - len(сбои)}, сбоев: {len(сбои)}")
    for ключ, код in сбои[:20]:
        print(f"  ✗ {ключ}: {код}")
    print(f"сверка: из {len(источник)} в назначении совпадают "
          f"{len(источник) - len(не_хватает)}, расходятся {len(не_хватает)}")
    return 0 if not не_хватает else 1


if __name__ == "__main__":
    sys.exit(main())
