from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import ntpath
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "dev" / "inventory_pre_r8_r7s3_python_tcb.py"
SPEC = importlib.util.spec_from_file_location("pre_r8_r7s3_python_tcb_inventory", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
tcb = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tcb
SPEC.loader.exec_module(tcb)


@dataclass(frozen=True)
class _FakeIdentity:
    final_path: str
    size: int


@dataclass(frozen=True)
class _FakeRead:
    raw: bytes
    identity: _FakeIdentity
    sha256: str


class _FakeReader:
    def __init__(self, content: dict[str, bytes]) -> None:
        self.content = {
            ntpath.normcase(ntpath.normpath(path)): raw for path, raw in content.items()
        }
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, path: str, *, require_protected_dacl: bool) -> _FakeRead:
        self.calls.append((path, require_protected_dacl))
        raw = self.content[ntpath.normcase(ntpath.normpath(path))]
        return _FakeRead(
            raw=raw,
            identity=_FakeIdentity(final_path=ntpath.normpath(path), size=len(raw)),
            sha256=hashlib.sha256(raw).hexdigest(),
        )


def _runtime() -> dict[str, Any]:
    return {
        "base_executable_path": r"C:\Python311\python.exe",
        "executable_path": r"C:\Python311\python.exe",
        "flags": {
            "dont_write_bytecode": 1,
            "ignore_environment": 1,
            "isolated": 1,
            "no_site": 1,
            "no_user_site": 1,
            "safe_path": 1,
        },
        "implementation": {"cache_tag": "cpython-311", "hexversion": 51054832, "name": "cpython"},
        "sys_path": [r"C:\Python311\python311.zip", r"C:\Python311\Lib"],
        "version": {
            "major": 3,
            "micro": 15,
            "minor": 11,
            "releaselevel": "final",
            "serial": 0,
            "text": "3.11.15 test fixture",
        },
    }


def _modules() -> list[dict[str, Any]]:
    return [
        {"classification": "source", "file_path": r"C:\repo\alpha.py", "name": "alpha"},
        {"classification": "built_in", "file_path": None, "name": "builtins"},
        {"classification": "extension", "file_path": r"C:\repo\native.pyd", "name": "native"},
        {"classification": "frozen", "file_path": None, "name": "ntpath"},
        {"classification": "namespace", "file_path": None, "name": "plugins"},
    ]


def _content() -> dict[str, bytes]:
    return {
        r"C:\Python311\python.exe": b"python-executable",
        r"C:\repo\alpha.py": b"VALUE = 1\n",
        r"C:\repo\native.pyd": b"native-extension",
        r"C:\Windows\System32\kernel32.dll": b"kernel32-fixture",
    }


def _valid() -> tuple[dict[str, Any], bytes, str, _FakeReader]:
    reader = _FakeReader(_content())
    document = tcb.build_inventory(
        runtime=_runtime(),
        modules=_modules(),
        windows_module_paths=[
            r"C:\Windows\System32\kernel32.dll",
            r"C:\Python311\python.exe",
            r"C:\repo\native.pyd",
        ],
        reader=reader,
    )
    raw = tcb.render_inventory(document)
    digest = hashlib.sha256(raw).hexdigest()
    return document, raw, digest, reader


def _resign(document: dict[str, Any]) -> tuple[bytes, str]:
    document["payload_sha256"] = hashlib.sha256(
        tcb.canonical_json_bytes(document["payload"])
    ).hexdigest()
    raw = tcb.canonical_json_bytes(document)
    return raw, hashlib.sha256(raw).hexdigest()


def test_inventory_is_deterministic_review_pending_and_handle_bound() -> None:
    document, raw, digest, reader = _valid()
    payload = document["payload"]

    assert tcb.validate_inventory(raw, expected_document_sha256=digest) == document
    assert payload["status"] == "review_pending"
    assert payload["approval"] == {
        "external_receipt_present": False,
        "production_approval_eligible": False,
    }
    assert set(payload["call_counts"].values()) == {0}
    assert payload["read_policy"]["require_protected_dacl_argument"] is False
    assert payload["read_policy"]["protected_dacl_required_for_inventory"] is False
    assert all(required is False for _, required in reader.calls)
    assert {record["classification"] for record in payload["modules"]} >= {
        "built_in",
        "extension",
        "frozen",
        "namespace",
        "source",
    }

    reverse_reader = _FakeReader(_content())
    reversed_document = tcb.build_inventory(
        runtime=_runtime(),
        modules=list(reversed(_modules())),
        windows_module_paths=list(
            reversed(
                [
                    r"C:\Windows\System32\kernel32.dll",
                    r"C:\Python311\python.exe",
                    r"C:\repo\native.pyd",
                ]
            )
        ),
        reader=reverse_reader,
    )
    assert tcb.render_inventory(reversed_document) == raw


@pytest.mark.parametrize("field", ["path", "sha256", "bytes"])
def test_duplicate_file_identity_json_key_is_rejected(field: str) -> None:
    _, raw, _, _ = _valid()
    text = raw.decode("utf-8")
    marker = f'"{field}":'
    offset = text.index(marker)
    value_start = offset + len(marker)
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text[value_start:])
    duplicate = f"{marker}{json.dumps(value, separators=(',', ':'))},"
    mutated = (text[:offset] + duplicate + text[offset:]).encode("utf-8")

    with pytest.raises(tcb.PythonTcbInventoryError, match=f"json_duplicate_key:{field}"):
        tcb.validate_inventory(
            mutated,
            expected_document_sha256=hashlib.sha256(mutated).hexdigest(),
        )


def test_role_swap_is_rejected_even_when_document_is_self_consistently_resigned() -> None:
    document, _, _, _ = _valid()
    mutated = copy.deepcopy(document)
    files = mutated["payload"]["files"]
    source = next(record for record in files if record["roles"] == ["python_module_source"])
    dll = next(record for record in files if record["roles"] == ["windows_loaded_dll"])
    source["roles"], dll["roles"] = dll["roles"], source["roles"]
    raw, digest = _resign(mutated)

    with pytest.raises(tcb.PythonTcbInventoryError, match="role_binding_mismatch"):
        tcb.validate_inventory(raw, expected_document_sha256=digest)


@pytest.mark.parametrize("operation", ["duplicate", "missing", "extra"])
def test_duplicate_missing_or_extra_file_record_is_rejected(operation: str) -> None:
    document, _, _, _ = _valid()
    mutated = copy.deepcopy(document)
    files = mutated["payload"]["files"]
    if operation == "duplicate":
        files.append(copy.deepcopy(files[0]))
        expected = "path_duplicate"
    elif operation == "missing":
        files.pop(0)
        expected = "file_reference_missing"
    else:
        files.append(
            {
                "bytes": 1,
                "path": r"C:\unexpected\extra.bin",
                "roles": ["python_module_source"],
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
        )
        expected = "file_set_not_exact"
    files.sort(key=lambda record: (ntpath.normcase(record["path"]), record["path"]))
    mutated["payload"]["counts"]["files"] = len(files)
    raw, digest = _resign(mutated)

    with pytest.raises(tcb.PythonTcbInventoryError, match=expected):
        tcb.validate_inventory(raw, expected_document_sha256=digest)


@pytest.mark.parametrize("field", ["path", "sha256", "bytes"])
def test_pinned_file_field_mutation_is_rejected(field: str) -> None:
    document, _, digest, _ = _valid()
    mutated = copy.deepcopy(document)
    record = mutated["payload"]["files"][0]
    if field == "path":
        record[field] = r"C:\mutated\file.bin"
    elif field == "sha256":
        record[field] = "f" * 64
    else:
        record[field] += 1
    raw, _ = _resign(mutated)

    with pytest.raises(tcb.PythonTcbInventoryError, match="document_sha256_mismatch"):
        tcb.validate_inventory(raw, expected_document_sha256=digest)


@pytest.mark.parametrize("operation", ["missing", "extra"])
def test_missing_or_extra_top_level_field_is_rejected(operation: str) -> None:
    document, _, _, _ = _valid()
    mutated = copy.deepcopy(document)
    if operation == "missing":
        del mutated["payload"]["purpose"]
    else:
        mutated["payload"]["unexpected"] = 0
    raw, digest = _resign(mutated)

    with pytest.raises(tcb.PythonTcbInventoryError, match="payload_fields_mismatch"):
        tcb.validate_inventory(raw, expected_document_sha256=digest)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("isolated", 0),
        ("no_site", 0),
        ("dont_write_bytecode", 0),
        ("ignore_environment", 0),
        ("no_user_site", 0),
        ("safe_path", 0),
    ],
)
def test_required_isolated_flags_are_fail_closed(name: str, value: int) -> None:
    flags = dict(_runtime()["flags"])
    flags[name] = value
    with pytest.raises(tcb.PythonTcbInventoryError, match=f"required_python_flag_missing:{name}=1"):
        tcb.require_isolated_runtime_flags(flags)


def test_direct_invocation_requires_exact_flags_and_script_path() -> None:
    valid = [r"C:\Python311\python.exe", "-I", "-S", "-B", str(tcb.MODULE_PATH)]
    assert tcb.require_isolated_direct_invocation(valid) is None

    for mutated in (
        [valid[0], "-S", "-I", "-B", valid[4]],
        [valid[0], "-I", "-S", valid[4]],
        [valid[0], "-I", "-S", "-B", str(ROOT / "other.py")],
    ):
        with pytest.raises(tcb.PythonTcbInventoryError):
            tcb.require_isolated_direct_invocation(mutated)


def test_loaded_module_classification_marks_built_in_and_frozen_explicitly() -> None:
    built_in = SimpleNamespace(__spec__=SimpleNamespace(origin="built-in"), __file__=None)
    frozen = SimpleNamespace(__spec__=SimpleNamespace(origin="frozen"), __file__=None)

    records = tcb.snapshot_loaded_modules({"built": built_in, "frozen": frozen})

    assert records == [
        {"classification": "built_in", "file_path": None, "name": "built"},
        {"classification": "frozen", "file_path": None, "name": "frozen"},
    ]


@pytest.mark.skipif(os.name != "nt", reason="read-only K32 smoke is Windows-specific")
def test_current_process_windows_module_and_same_handle_read_smoke() -> None:
    paths = tcb.enumerate_windows_loaded_module_paths()
    assert paths
    assert paths == sorted(paths, key=lambda value: (ntpath.normcase(value), value))
    assert len({ntpath.normcase(path) for path in paths}) == len(paths)

    result = tcb.read_bound_file(str(MODULE_PATH), require_protected_dacl=False)
    assert result.sha256 == hashlib.sha256(result.raw).hexdigest()
    assert len(result.raw) == result.identity.size
