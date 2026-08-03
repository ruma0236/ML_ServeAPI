from __future__ import annotations

import hashlib
import struct
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


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


def _decode_file_path(value: str) -> str:
    if not value.startswith("file://"):
        return value
    parsed = urlparse(value)
    decoded = unquote(parsed.path)
    if parsed.netloc and len(parsed.netloc) == 2 and parsed.netloc.endswith(":"):
        decoded = f"{parsed.netloc}{decoded}"
    if decoded.startswith("/") and len(decoded) > 3 and decoded[2] == ":":
        decoded = decoded[1:]
    return decoded


def _map_runtime_path(
    value: str,
    *,
    host_data_root: Path | None,
    data_mount_root: str | Path | None,
) -> Path:
    decoded = _decode_file_path(value).replace("\\", "/")
    direct = Path(decoded)
    if direct.exists():
        return direct

    host_root = str(host_data_root or "").replace("\\", "/").rstrip("/")
    mount_root = str(data_mount_root or "").replace("\\", "/").rstrip("/")
    if host_root and mount_root and (
        decoded.lower() == mount_root.lower()
        or decoded.lower().startswith(f"{mount_root.lower()}/")
    ):
        return Path(f"{host_root}{decoded[len(mount_root):]}")
    return direct


def resolve_local_image(
    record: dict[str, Any],
    raw_image_root: Path,
    *,
    host_data_root: Path | None = None,
    data_mount_root: str | Path | None = None,
) -> Path | None:
    first_candidate: Path | None = None
    for field in ("image_path", "local_path", "file_path", "image_uri"):
        value = str(record.get(field, "") or "")
        if not value:
            continue
        candidate = _map_runtime_path(
            value,
            host_data_root=host_data_root,
            data_mount_root=data_mount_root,
        )
        if candidate.exists():
            return candidate
        first_candidate = first_candidate or candidate

    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    relative_path = str(metadata.get("relative_path") or "").replace("\\", "/").lstrip("/")
    if relative_path:
        candidate = raw_image_root / Path(relative_path)
        if candidate.exists():
            return candidate
        first_candidate = first_candidate or candidate

    filename = image_filename_from_uri(str(record.get("image_uri", "") or ""))
    if filename:
        candidate = raw_image_root / filename
        if candidate.exists():
            return candidate
        first_candidate = first_candidate or candidate
    return first_candidate


def canonical_runtime_image_path(
    local_image: Path | None,
    *,
    host_data_root: Path | None,
    data_mount_root: str | Path | None,
) -> str:
    if local_image is None:
        return ""
    normalized = str(local_image).replace("\\", "/")
    host_root = str(host_data_root or "").replace("\\", "/").rstrip("/")
    mount_root = str(data_mount_root or "").replace("\\", "/").rstrip("/")
    if host_root and mount_root and (
        normalized.lower() == host_root.lower()
        or normalized.lower().startswith(f"{host_root.lower()}/")
    ):
        return f"{mount_root}{normalized[len(host_root):]}"
    return normalized


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
