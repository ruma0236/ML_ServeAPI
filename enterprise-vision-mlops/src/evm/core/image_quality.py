from __future__ import annotations

import hashlib
import struct
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_image_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in (0xD8, 0xD9):
                continue
            if offset + 2 > len(data):
                return None
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                return None
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
                return width, height
            offset += segment_length
    return None


def byte_quality_proxies(path: Path, sample_size: int = 65536) -> dict[str, float]:
    data = path.read_bytes()[:sample_size]
    if not data:
        return {"brightness_proxy": 0.0, "blur_proxy": 0.0}
    brightness = sum(data) / (255 * len(data))
    if len(data) < 2:
        blur_proxy = 0.0
    else:
        diffs = [abs(data[idx] - data[idx - 1]) for idx in range(1, len(data))]
        blur_proxy = sum(diffs) / (255 * len(diffs))
    return {
        "brightness_proxy": round(brightness, 6),
        "blur_proxy": round(blur_proxy, 6),
    }


def image_filename_from_uri(image_uri: str) -> str:
    normalized = image_uri.rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def resolve_local_image(record: dict[str, Any], raw_image_root: Path) -> Path | None:
    for field in ("image_path", "local_path", "file_path"):
        value = str(record.get(field, "") or "")
        if value:
            path = Path(value)
            return path if path.is_absolute() else raw_image_root.parent / path
    filename = image_filename_from_uri(str(record.get("image_uri", "") or ""))
    if filename:
        return raw_image_root / filename
    return None


def stable_split(sample_id: str, ratios: dict[str, float]) -> str:
    value = int(hashlib.sha256(sample_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    train_cutoff = float(ratios.get("train", 0.6))
    validation_cutoff = train_cutoff + float(ratios.get("validation", 0.2))
    if value < train_cutoff:
        return "train"
    if value < validation_cutoff:
        return "validation"
    return "test"


def summarize_counts(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(Counter(str(record.get(field, "")) for record in records if record.get(field)))
