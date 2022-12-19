from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Iterable, TypedDict

import boto3
from tqdm import tqdm
from zarr.storage import NestedDirectoryStore

from zarr_checksum.tree import ZarrChecksumTree

__all__ = [
    "AWSCredentials",
    "ChecksummedFile",
    "FileGenerator",
    "yield_files_s3",
    "yield_files_local",
    "compute_zarr_checksum",
]


class AWSCredentials(TypedDict):
    key: str
    secret: str
    region: str


@dataclass
class ChecksummedFile:
    path: Path
    size: int
    digest: str


FileGenerator = Iterable[ChecksummedFile]


def yield_files_s3(
    bucket: str, prefix: str = "", credentials: AWSCredentials | None = None
) -> FileGenerator:
    if credentials is None:
        credentials = {
            "key": None,
            "secret": None,
            "region": "us-east-1",
        }

    client = boto3.client(
        "s3",
        region_name=credentials["region"],
        aws_access_key_id=credentials["key"],
        aws_secret_access_key=credentials["secret"],
    )

    continuation_token = None
    options = {"Bucket": bucket, "Prefix": prefix}

    print("Retrieving files...")

    # Test that url is fully qualified path by appending slash to prefix and listing objects
    test_resp = client.list_objects_v2(Bucket=bucket, Prefix=os.path.join(prefix, ""))
    if "Contents" not in test_resp:
        print(f"Warning: No files found under prefix: {prefix}.")
        print(
            "Please check that you have provided the fully qualified path to the zarr root."
        )
        yield from []
        return

    # Iterate until all files found
    while True:
        if continuation_token is not None:
            options["ContinuationToken"] = continuation_token

        # Fetch
        res = client.list_objects_v2(**options)

        # Fix keys of listing to be relative to zarr root
        mapped = (
            ChecksummedFile(
                path=Path(obj["Key"]).relative_to(prefix),
                size=obj["Size"],
                digest=obj["ETag"].strip('"'),
            )
            for obj in res.get("Contents", [])
        )

        # Yield as flat iteratble
        yield from mapped

        # If all files fetched, end
        continuation_token = res.get("NextContinuationToken", None)
        if continuation_token is None:
            break


def yield_files_local(directory: str | Path) -> FileGenerator:
    root_path = Path(directory)
    if not root_path.exists():
        raise Exception("Path does not exist")

    print("Discovering files...")
    store = NestedDirectoryStore(root_path)
    for file in tqdm(list(store.keys())):
        path = Path(file)
        absolute_path = root_path / path
        size = absolute_path.stat().st_size

        # Compute md5sum of file
        md5sum = hashlib.md5()
        with open(absolute_path, "rb") as f:
            while chunk := f.read(8192):
                md5sum.update(chunk)
        digest = md5sum.hexdigest()

        # Yield file
        yield ChecksummedFile(path=path, size=size, digest=digest)


def compute_zarr_checksum(generator: FileGenerator) -> str:
    tree = ZarrChecksumTree()
    for file in generator:
        tree.add_leaf(
            path=file.path,
            size=file.size,
            digest=file.digest,
        )

    # Compute digest
    return tree.process()
