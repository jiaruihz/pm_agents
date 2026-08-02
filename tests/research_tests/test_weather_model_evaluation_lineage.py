import gzip
from pathlib import Path

from weather_model_evaluation import gzip_content_sha256


def test_gzip_content_hash_ignores_header_timestamp(tmp_path: Path) -> None:
    first = tmp_path / "first.gz"
    second = tmp_path / "second.gz"
    with gzip.GzipFile(first, "wb", mtime=1) as handle:
        handle.write(b"same semantic content\n")
    with gzip.GzipFile(second, "wb", mtime=2) as handle:
        handle.write(b"same semantic content\n")
    assert first.read_bytes() != second.read_bytes()
    assert gzip_content_sha256(first) == gzip_content_sha256(second)
