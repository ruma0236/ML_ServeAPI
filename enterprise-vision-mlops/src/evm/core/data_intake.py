from __future__ import annotations

import csv
import hashlib
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from evm.core.image_quality import byte_quality_proxies, read_image_dimensions, sha256_file, stable_split
from evm.core.pipeline import utc_now


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MASK_DIR_NAMES = {"mask", "masks", "ground_truth", "groundtruth", "gt"}


def _norm_parts(path: Path) -> list[str]:
    return [part.lower() for part in path.parts]


def is_image_file(path: Path, allowed_extensions: set[str] | None = None) -> bool:
    extensions = allowed_extensions or IMAGE_EXTENSIONS
    return path.is_file() and path.suffix.lower() in extensions


def is_mask_path(path: Path) -> bool:
    return any(part in MASK_DIR_NAMES for part in _norm_parts(path))


def iter_image_files(root: Path, allowed_extensions: set[str], max_files: int = 0) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if is_image_file(path, allowed_extensions):
            files.append(path)
            if max_files and len(files) >= max_files:
                break
    return sorted(files)


def infer_dataset_layout(dataset_id: str, root: Path) -> str:
    if dataset_id == "visa":
        return "visa"
    if dataset_id == "mvtec_ad":
        return "mvtec_ad"
    if (root / "image_anno.csv").exists():
        return "visa"
    if any((root / child).exists() for child in ("train", "test", "ground_truth")):
        return "mvtec_ad"
    return "generic"


def _safe_rel(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def infer_split(layout: str, rel_path: Path, sample_id: str) -> str:
    parts = _norm_parts(rel_path)
    if "train" in parts:
        return "train"
    if "validation" in parts or "val" in parts:
        return "validation"
    if "test" in parts:
        return "test"
    if layout == "visa":
        return stable_split(sample_id, {"train": 0.6, "validation": 0.2, "test": 0.2})
    return "unassigned"


def infer_label(layout: str, rel_path: Path) -> tuple[str, str]:
    parts = _norm_parts(rel_path)
    if "normal" in parts or "good" in parts:
        return "normal", "normal"
    if "anomaly" in parts:
        return "anomaly", "anomaly"
    if layout == "mvtec_ad" and "test" in parts:
        test_index = parts.index("test")
        if test_index + 1 < len(parts):
            label = rel_path.parts[test_index + 1]
            if label.lower() == "good":
                return "normal", "normal"
            return label, "anomaly"
    return "unknown", "unknown"


def infer_class_name(layout: str, rel_path: Path) -> str:
    parts = rel_path.parts
    lower = _norm_parts(rel_path)
    if layout == "visa" and parts:
        return parts[0]
    if layout == "mvtec_ad":
        for marker in ("train", "test", "ground_truth"):
            if marker in lower:
                idx = lower.index(marker)
                if idx > 0:
                    return parts[idx - 1]
    return parts[0] if parts else "unknown"


def sample_id_for(dataset_id: str, rel_path: Path) -> str:
    normalized = "/".join(rel_path.parts).replace("\\", "/")
    digest = hashlib.sha256(f"{dataset_id}:{normalized}".encode("utf-8")).hexdigest()[:16]
    stem = rel_path.stem.replace(" ", "_")
    return f"{dataset_id}_{stem}_{digest}"


def load_visa_annotations(root: Path) -> dict[str, dict[str, str]]:
    path = root / "image_anno.csv"
    if not path.exists():
        return {}
    annotations: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            image_key = (
                row.get("image")
                or row.get("image_path")
                or row.get("file")
                or row.get("filename")
                or row.get("path")
                or ""
            )
            if image_key:
                annotations[image_key.replace("\\", "/")] = {str(k): str(v) for k, v in row.items()}
    return annotations


def _mask_lookup(mask_files: list[Path], root: Path) -> dict[str, Path]:
    lookup: dict[str, Path] = {}
    for path in mask_files:
        rel = _safe_rel(path, root)
        lookup[str(rel).replace("\\", "/")] = path
        lookup[path.stem.replace("_mask", "")] = path
    return lookup


def find_mask(path: Path, root: Path, mask_files: list[Path], annotations: dict[str, dict[str, str]]) -> Path | None:
    rel = _safe_rel(path, root)
    rel_key = str(rel).replace("\\", "/")
    annotated = annotations.get(rel_key) or annotations.get(path.name) or {}
    for key in ("mask", "mask_path", "mask_file", "mask_uri"):
        value = annotated.get(key, "")
        if value:
            candidate = root / value
            if candidate.exists():
                return candidate
    lookup = _mask_lookup(mask_files, root)
    if path.stem in lookup:
        return lookup[path.stem]
    if f"{path.stem}_mask" in lookup:
        return lookup[f"{path.stem}_mask"]
    return None


def build_manifest_records(
    dataset: dict[str, Any],
    image_files: list[Path],
    mask_files: list[Path],
    *,
    dataset_version: str,
    allowed_extensions: set[str],
) -> list[dict[str, Any]]:
    dataset_id = str(dataset.get("id", "dataset"))
    root = Path(str(dataset.get("raw_root", "")))
    layout = infer_dataset_layout(dataset_id, root)
    annotations = load_visa_annotations(root) if layout == "visa" else {}
    records: list[dict[str, Any]] = []
    for path in image_files:
        if is_mask_path(path):
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        rel = _safe_rel(path, root)
        sample_id = sample_id_for(dataset_id, rel)
        label, label_type = infer_label(layout, rel)
        dimensions = read_image_dimensions(path)
        width, height = dimensions or (0, 0)
        mask = find_mask(path, root, mask_files, annotations)
        records.append(
            {
                "id": sample_id,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "sample_id": sample_id,
                "image_uri": path.as_uri(),
                "image_path": str(path),
                "split": infer_split(layout, rel, sample_id),
                "label": label,
                "label_type": label_type,
                "class_name": infer_class_name(layout, rel),
                "width": width,
                "height": height,
                "content_sha256": sha256_file(path),
                "source_uri": str(dataset.get("source_url", "")),
                "license_id": str(dataset.get("license_id", "manual-review-required")),
                "mask_uri": mask.as_uri() if mask else "",
                "defect_type": "" if label_type == "normal" else label,
                "metadata": {
                    "dataset_layout": layout,
                    "relative_path": str(rel).replace("\\", "/"),
                    "object_prefix": str(dataset.get("object_prefix", "")),
                },
            }
        )
    return records


def summarize_numbers(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "min": 0, "max": 0, "avg": 0}
    return {
        "count": len(values),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "avg": round(mean(values), 6),
    }


def cleaning_benchmark(records: list[dict[str, Any]], max_quality_samples: int = 200) -> dict[str, Any]:
    extension_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    hash_counts: Counter[str] = Counter()
    widths: list[float] = []
    heights: list[float] = []
    brightness: list[float] = []
    blur: list[float] = []
    unreadable = 0

    for index, record in enumerate(records):
        path = Path(str(record.get("image_path", "")))
        extension_counts[path.suffix.lower()] += 1
        label_counts[str(record.get("label", "unknown"))] += 1
        class_counts[str(record.get("class_name", "unknown"))] += 1
        split_counts[str(record.get("split", "unassigned"))] += 1
        content_hash = str(record.get("content_sha256", ""))
        if content_hash:
            hash_counts[content_hash] += 1
        width = int(record.get("width", 0) or 0)
        height = int(record.get("height", 0) or 0)
        if width <= 0 or height <= 0:
            unreadable += 1
        else:
            widths.append(float(width))
            heights.append(float(height))
        if index < max_quality_samples and path.exists():
            proxies = byte_quality_proxies(path)
            brightness.append(float(proxies["brightness_proxy"]))
            blur.append(float(proxies["blur_proxy"]))

    duplicate_groups = sum(1 for count in hash_counts.values() if count > 1)
    duplicate_files = sum(count for count in hash_counts.values() if count > 1)
    return {
        "record_count": len(records),
        "readable_images": len(records) - unreadable,
        "unreadable_images": unreadable,
        "duplicate_hash_groups": duplicate_groups,
        "duplicate_files": duplicate_files,
        "extension_counts": dict(extension_counts),
        "label_counts": dict(label_counts),
        "class_counts": dict(class_counts),
        "split_counts": dict(split_counts),
        "width_summary": summarize_numbers(widths),
        "height_summary": summarize_numbers(heights),
        "brightness_proxy_summary": summarize_numbers(brightness),
        "blur_proxy_summary": summarize_numbers(blur),
    }


def directory_size_bytes(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total
