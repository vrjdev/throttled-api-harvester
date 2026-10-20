import os
from typing import Optional, Protocol


class Store(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> Optional[bytes]: ...


class S3Store:
    def __init__(self, bucket: str):
        import boto3

        self._s3 = boto3.client("s3")
        self._bucket = bucket

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)

    def get(self, key: str) -> Optional[bytes]:
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return None


class LocalStore:
    """Filesystem store for local runs and tests."""

    def __init__(self, root: str):
        self._root = root

    def put(self, key: str, data: bytes) -> None:
        path = os.path.join(self._root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def get(self, key: str) -> Optional[bytes]:
        try:
            with open(os.path.join(self._root, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
