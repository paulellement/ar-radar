"""JSONL read/write that works for local paths and Unity Catalog Volume paths (/Volumes/...)."""

import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path


def partition_dir(root: str, dataset: str, snapshot_date: str) -> Path:
    return Path(root) / dataset / f"dt={snapshot_date}"


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    """Write rows to a local temp file, then copy into place (overwrites; idempotent per path).

    Staging locally avoids partial files in the Volume if the run dies midway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tmp:
        for row in rows:
            tmp.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    try:
        shutil.copyfile(tmp.name, path)
    finally:
        os.unlink(tmp.name)
    return count


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
