"""Optional S3 delivery: upload large files and share presigned (expiring) URLs."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import time
from pathlib import Path

from ..config import Settings

log = logging.getLogger(__name__)


class S3Store:
    def __init__(self, settings: Settings):
        import boto3  # optional dependency
        from boto3.s3.transfer import TransferConfig

        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix
        self.ttl = int(settings.s3_url_ttl_hours * 3600)
        self.client = boto3.client("s3", region_name=settings.s3_region)
        self.transfer = TransferConfig(
            multipart_threshold=64 * 1024 * 1024, multipart_chunksize=64 * 1024 * 1024, max_concurrency=8
        )

    def _upload(self, path: Path, user_id: int) -> str:
        key = f"{self.prefix}{user_id}/{int(time.time())}/{path.name}"
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            Config=self.transfer,
            ExtraArgs={"ContentType": ctype, "ContentDisposition": f'attachment; filename="{path.name}"'},
        )
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.ttl
        )

    async def upload(self, path: Path, user_id: int) -> str:
        return await asyncio.to_thread(self._upload, path, user_id)
