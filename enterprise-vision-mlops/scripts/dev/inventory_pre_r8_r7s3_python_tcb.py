"""Build a review-only, handle-bound inventory of the current Python TCB.

This module is diagnostic inventory code, not a production approval mechanism.
The direct entry point requires ``python -I -S -B`` and performs no process
creation, network access, service control, or evidence publication.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import ntpath
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol


MODULE_PATH = Path(__file__).resolve()
ROOT = MODULE_PATH.parents[2]
SOURCE_ROOT = ROOT / "src"

try:
    from evm.scale_validation.phase_b2_r7s3_handle_io import read_bound_file
except ModuleNotFoundError as exc:
    if exc.name != "evm":
        raise
    sys.path.insert(0, str(SOURCE_ROOT))
    from evm.scale_validation.phase_b2_r7s3_handle_io import read_bound_file


SCHEMA_VERSION = "evm.s8-v4.x1.pre-r8-r7s3.python-tcb-inventory.v1"
PURPOSE = "review_only_python_tcb_inventory_not_production_approval"
STATUS = "review_pending"
HEX64_RE = re.compile(r"[0-9a-f]{64}")
FLAG_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
REQUIRED_FLAGS = {
    "dont_write_bytecode": 1,
    "ignore_environment": 1,
    "isolated": 1,
    "no_site": 1,
    "no_user_site": 1,
    "safe_path": 1,
}
MODULE_CLASSIFICATIONS = {
    "built_in",
    "bytecode",
    "extension",
    "frozen",
    "namespace",
    "source",
}
FILE_ROLES = {
    "python_base_executable",
    "python_executable",
    "python_module_bytecode",
    "python_module_extension",
    "python_module_source",
    "windows_loaded_dll",
}
CLASSIFICATION_ROLE = {
    "bytecode": "python_module_bytecode",
    "extension": "python_module_extension",
    "source": "python_module_source",
}
CALL_COUNTS = {
    "docker": 0,
    "external_approval": 0,
    "live": 0,
    "network": 0,
    "process_spawn": 0,
    "service": 0,
    "wsl": 0,
}
READ_POLICY = {
    "authority": "open_kernel_handle",
    "protected_dacl_required_for_inventory": False,
    "read_only": True,
    "reader": "evm.scale_validation.phase_b2_r7s3_handle_io.read_bound_file",
    "require_protected_dacl_argument": False,
    "same_handle_bytes_and_sha256": True,
}


class PythonTcbInventoryError(RuntimeError):
    """Raised when collection or exact validation fails closed."""


class BoundReader(Protocol):
    def __call__(self, path: str, *, require_protected_dacl: bool) -> Any: ...


class DllPathProvider(Protocol):
    def __call__(self) -> Sequence[str]: ...


def _duplicate_key_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PythonTcbInventoryError(f"json_duplicate_key:{key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise PythonTcbInventoryError(f"json_non_finite:{value}")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PythonTcbInventoryError("canonical_json_serialization_failed") from exc
    return (text + "\n").encode("utf-8")


def _strict_json(raw: bytes) -> dict[str, Any]:
    if not raw or raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise PythonTcbInventoryError("inventory_json_not_canonical_utf8_lf")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_key_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PythonTcbInventoryError("inventory_json_parse_failed") from exc
    if not isinstance(value, dict):
        raise PythonTcbInventoryError("inventory_json_root_not_object")
    if canonical_json_bytes(value) != raw:
        raise PythonTcbInventoryError("inventory_json_not_canonical")
    return value


def _expect_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PythonTcbInventoryError(f"{label}_fields_mismatch")


def _require_lower_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise PythonTcbInventoryError(f"{label}_sha256_invalid")
    return value


def _require_plain_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PythonTcbInventoryError(f"{label}_integer_invalid")
    return value


def _comparable_path(value: str) -> str:
    normalized = value
    if normalized.startswith("\\\\?\\UNC\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    return ntpath.normcase(ntpath.normpath(normalized))


def _windows_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or not ntpath.isabs(value):
        raise PythonTcbInventoryError(f"{label}_absolute_windows_path_required")
    normalized = ntpath.normpath(value)
    if normalized != value:
        raise PythonTcbInventoryError(f"{label}_path_not_normalized")
    return value


def _sorted_paths(values: Sequence[str]) -> list[str]:
    return sorted(values, key=lambda value: (_comparable_path(value), value))


def snapshot_sys_flags(flags: Any = None) -> dict[str, int]:
    source = sys.flags if flags is None else flags
    result: dict[str, int] = {}
    for name in dir(source):
        if name.startswith("_") or name in {
            "count",
            "index",
            "n_fields",
            "n_sequence_fields",
            "n_unnamed_fields",
        }:
            continue
        value = getattr(source, name)
        if isinstance(value, (bool, int)):
            result[name] = int(value)
    return dict(sorted(result.items()))


def require_isolated_runtime_flags(flags: Mapping[str, Any]) -> None:
    for name, expected in REQUIRED_FLAGS.items():
        if flags.get(name) != expected:
            raise PythonTcbInventoryError(f"required_python_flag_missing:{name}={expected}")


def require_isolated_direct_invocation(values: Sequence[str] | None = None) -> None:
    """Require the exact review-inventory interpreter entry shape.

    This closes only accidental flag drift.  It is not an external authority
    or a Python TCB closure proof.
    """

    source = list(sys.orig_argv if values is None else values)
    if len(source) != 5 or source[1:4] != ["-I", "-S", "-B"]:
        raise PythonTcbInventoryError("isolated_direct_invocation_required:-I -S -B")
    if ntpath.normcase(ntpath.abspath(source[4])) != ntpath.normcase(str(MODULE_PATH)):
        raise PythonTcbInventoryError("inventory_direct_script_path_mismatch")


def _normalize_sys_path(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(values):
        value = _windows_path(raw, f"sys_path_{index}")
        comparable = _comparable_path(value)
        if comparable in seen:
            raise PythonTcbInventoryError("sys_path_duplicate")
        seen.add(comparable)
        result.append(value)
    return result


def snapshot_current_runtime() -> dict[str, Any]:
    flags = snapshot_sys_flags()
    require_isolated_runtime_flags(flags)
    executable = ntpath.normpath(os.path.abspath(sys.executable))
    base_executable = ntpath.normpath(
        os.path.abspath(getattr(sys, "_base_executable", None) or sys.executable)
    )
    return {
        "base_executable_path": _windows_path(base_executable, "python_base_executable"),
        "executable_path": _windows_path(executable, "python_executable"),
        "flags": flags,
        "implementation": {
            "cache_tag": sys.implementation.cache_tag,
            "hexversion": sys.hexversion,
            "name": sys.implementation.name,
        },
        "sys_path": _normalize_sys_path([ntpath.normpath(value) for value in sys.path]),
        "version": {
            "major": sys.version_info.major,
            "micro": sys.version_info.micro,
            "minor": sys.version_info.minor,
            "releaselevel": sys.version_info.releaselevel,
            "serial": sys.version_info.serial,
            "text": sys.version,
        },
    }


def _module_origin(module: ModuleType) -> tuple[str, str | None]:
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None)
    display = getattr(module, "__file__", None)
    if origin in {"built-in", "builtin"}:
        return "built_in", None
    if origin == "frozen":
        return "frozen", None
    if origin is None and not display:
        return "namespace", None
    candidate = origin if isinstance(origin, str) and origin else display
    if not isinstance(candidate, str):
        raise PythonTcbInventoryError("python_module_origin_invalid")
    path = _windows_path(ntpath.normpath(os.path.abspath(candidate)), "python_module")
    suffix = ntpath.splitext(path)[1].lower()
    if suffix in {".pyd", ".dll"}:
        return "extension", path
    if suffix in {".pyc", ".pyo"}:
        return "bytecode", path
    if suffix in {".py", ".pyw"}:
        return "source", path
    raise PythonTcbInventoryError(f"python_module_origin_suffix_unsupported:{suffix}")


def snapshot_loaded_modules(
    modules: Mapping[str, ModuleType | None] | None = None,
) -> list[dict[str, Any]]:
    source = sys.modules if modules is None else modules
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, module in sorted(source.items()):
        if module is None:
            continue
        if not isinstance(name, str) or not name or "\x00" in name or name in seen:
            raise PythonTcbInventoryError("python_module_name_invalid_or_duplicate")
        seen.add(name)
        classification, path = _module_origin(module)
        records.append({"classification": classification, "file_path": path, "name": name})
    return records


class WindowsCurrentProcessModuleApi:
    """Read-only K32 enumeration of every image loaded by the current process."""

    _LIST_MODULES_ALL = 0x03
    _MAX_MODULES = 16384
    _PATH_BUFFER = 32768

    def __init__(self) -> None:
        if os.name != "nt":
            raise PythonTcbInventoryError("windows_dll_inventory_requires_windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()

    @staticmethod
    def _raise(label: str) -> None:
        raise PythonTcbInventoryError(f"{label}:win32={ctypes.get_last_error()}")

    def _declare(self) -> None:
        module_type = wintypes.HANDLE
        self.kernel32.GetCurrentProcess.argtypes = []
        self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel32.K32EnumProcessModulesEx.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(module_type),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        self.kernel32.K32EnumProcessModulesEx.restype = wintypes.BOOL
        self.kernel32.K32GetModuleFileNameExW.argtypes = [
            wintypes.HANDLE,
            module_type,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        self.kernel32.K32GetModuleFileNameExW.restype = wintypes.DWORD

    def _snapshot(self) -> list[str]:
        process = self.kernel32.GetCurrentProcess()
        capacity = 256
        module_type = wintypes.HANDLE
        while True:
            modules = (module_type * capacity)()
            needed = wintypes.DWORD()
            if not self.kernel32.K32EnumProcessModulesEx(
                process,
                modules,
                ctypes.sizeof(modules),
                ctypes.byref(needed),
                self._LIST_MODULES_ALL,
            ):
                self._raise("K32EnumProcessModulesEx")
            if needed.value % ctypes.sizeof(module_type):
                raise PythonTcbInventoryError("windows_module_buffer_size_invalid")
            count = needed.value // ctypes.sizeof(module_type)
            if count > self._MAX_MODULES:
                raise PythonTcbInventoryError("windows_module_count_unbounded")
            if count <= capacity:
                break
            capacity = count + 16

        paths: list[str] = []
        seen: set[str] = set()
        for module in modules[:count]:
            buffer = ctypes.create_unicode_buffer(self._PATH_BUFFER)
            written = self.kernel32.K32GetModuleFileNameExW(process, module, buffer, len(buffer))
            if not written or written >= len(buffer) - 1:
                self._raise("K32GetModuleFileNameExW")
            path = _windows_path(ntpath.normpath(buffer.value), "windows_loaded_module")
            comparable = _comparable_path(path)
            if comparable in seen:
                raise PythonTcbInventoryError("windows_loaded_module_duplicate")
            seen.add(comparable)
            paths.append(path)
        return _sorted_paths(paths)

    def paths(self) -> list[str]:
        before = self._snapshot()
        after = self._snapshot()
        if [_comparable_path(path) for path in before] != [
            _comparable_path(path) for path in after
        ]:
            raise PythonTcbInventoryError("windows_loaded_module_snapshot_unstable")
        return before


def enumerate_windows_loaded_module_paths() -> list[str]:
    return WindowsCurrentProcessModuleApi().paths()


def _validate_flags(flags: Any) -> dict[str, int]:
    if not isinstance(flags, dict) or not flags:
        raise PythonTcbInventoryError("python_flags_invalid")
    if list(flags) != sorted(flags):
        raise PythonTcbInventoryError("python_flags_not_sorted")
    for name, value in flags.items():
        if FLAG_NAME_RE.fullmatch(name) is None:
            raise PythonTcbInventoryError("python_flag_name_invalid")
        if isinstance(value, bool) or not isinstance(value, int):
            raise PythonTcbInventoryError(f"python_flag_{name}_integer_invalid")
    require_isolated_runtime_flags(flags)
    return flags


def _validate_runtime(runtime: Any) -> dict[str, Any]:
    if not isinstance(runtime, dict):
        raise PythonTcbInventoryError("python_runtime_not_object")
    _expect_exact_keys(
        runtime,
        {
            "base_executable_path",
            "executable_path",
            "flags",
            "implementation",
            "sys_path",
            "version",
        },
        "python_runtime",
    )
    _windows_path(runtime["executable_path"], "python_executable")
    _windows_path(runtime["base_executable_path"], "python_base_executable")
    _validate_flags(runtime["flags"])

    implementation = runtime["implementation"]
    if not isinstance(implementation, dict):
        raise PythonTcbInventoryError("python_implementation_not_object")
    _expect_exact_keys(implementation, {"cache_tag", "hexversion", "name"}, "implementation")
    if not isinstance(implementation["name"], str) or not implementation["name"]:
        raise PythonTcbInventoryError("python_implementation_name_invalid")
    if not isinstance(implementation["cache_tag"], str) or not implementation["cache_tag"]:
        raise PythonTcbInventoryError("python_cache_tag_invalid")
    _require_plain_int(implementation["hexversion"], "python_hexversion", minimum=1)

    version = runtime["version"]
    if not isinstance(version, dict):
        raise PythonTcbInventoryError("python_version_not_object")
    _expect_exact_keys(
        version,
        {"major", "micro", "minor", "releaselevel", "serial", "text"},
        "python_version",
    )
    for name in ("major", "minor", "micro", "serial"):
        _require_plain_int(version[name], f"python_version_{name}")
    if (version["major"], version["minor"]) < (3, 11):
        raise PythonTcbInventoryError("python_version_below_contract")
    if not isinstance(version["releaselevel"], str) or not version["releaselevel"]:
        raise PythonTcbInventoryError("python_releaselevel_invalid")
    if not isinstance(version["text"], str) or not version["text"]:
        raise PythonTcbInventoryError("python_version_text_invalid")

    sys_path = runtime["sys_path"]
    if not isinstance(sys_path, list):
        raise PythonTcbInventoryError("sys_path_not_array")
    normalized = _normalize_sys_path(sys_path)
    if normalized != sys_path:
        raise PythonTcbInventoryError("sys_path_changed_during_validation")
    return runtime


def _validate_modules(modules: Any) -> list[dict[str, Any]]:
    if not isinstance(modules, list):
        raise PythonTcbInventoryError("python_modules_not_array")
    if modules != sorted(modules, key=lambda record: record.get("name", "")):
        raise PythonTcbInventoryError("python_modules_not_sorted")
    names: set[str] = set()
    for record in modules:
        if not isinstance(record, dict):
            raise PythonTcbInventoryError("python_module_not_object")
        _expect_exact_keys(record, {"classification", "file_path", "name"}, "python_module")
        name = record["name"]
        classification = record["classification"]
        if not isinstance(name, str) or not name or "\x00" in name or name in names:
            raise PythonTcbInventoryError("python_module_name_invalid_or_duplicate")
        names.add(name)
        if classification not in MODULE_CLASSIFICATIONS:
            raise PythonTcbInventoryError("python_module_classification_invalid")
        if classification in CLASSIFICATION_ROLE:
            _windows_path(record["file_path"], "python_module")
        elif record["file_path"] is not None:
            raise PythonTcbInventoryError("non_file_module_has_path")
    return modules


def _read_file_pin(path: str, roles: set[str], reader: BoundReader) -> dict[str, Any]:
    try:
        result = reader(path, require_protected_dacl=False)
    except Exception as exc:
        raise PythonTcbInventoryError(f"same_handle_inventory_read_failed:{path}") from exc
    raw = getattr(result, "raw", None)
    identity = getattr(result, "identity", None)
    final_path = getattr(identity, "final_path", None)
    size = getattr(identity, "size", None)
    digest = getattr(result, "sha256", None)
    if not isinstance(raw, bytes):
        raise PythonTcbInventoryError("same_handle_result_raw_invalid")
    final = _windows_path(final_path, "same_handle_final")
    if _comparable_path(final) != _comparable_path(path):
        raise PythonTcbInventoryError("same_handle_final_path_mismatch")
    if size != len(raw):
        raise PythonTcbInventoryError("same_handle_result_size_mismatch")
    _require_lower_sha256(digest, "same_handle_result")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise PythonTcbInventoryError("same_handle_result_hash_mismatch")
    if not roles or not roles <= FILE_ROLES:
        raise PythonTcbInventoryError("same_handle_roles_invalid")
    return {"bytes": len(raw), "path": final, "roles": sorted(roles), "sha256": digest}


def _copy_runtime(runtime: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(runtime, allow_nan=False))


def build_inventory(
    *,
    runtime: Mapping[str, Any],
    modules: Sequence[Mapping[str, Any]],
    windows_module_paths: Sequence[str],
    reader: BoundReader = read_bound_file,
) -> dict[str, Any]:
    runtime_value = _copy_runtime(runtime)
    _validate_runtime(runtime_value)
    module_values = [dict(record) for record in modules]
    module_values.sort(key=lambda record: record.get("name", ""))
    _validate_modules(module_values)

    requests: dict[str, dict[str, Any]] = {}

    def request(path: str, role: str) -> None:
        normalized = _windows_path(ntpath.normpath(path), "inventory_request")
        key = _comparable_path(normalized)
        entry = requests.setdefault(key, {"display": normalized, "roles": set()})
        entry["roles"].add(role)

    request(runtime_value["executable_path"], "python_executable")
    request(runtime_value["base_executable_path"], "python_base_executable")
    for module in module_values:
        classification = module["classification"]
        if classification in CLASSIFICATION_ROLE:
            request(module["file_path"], CLASSIFICATION_ROLE[classification])

    executable_keys = {
        _comparable_path(runtime_value["executable_path"]),
        _comparable_path(runtime_value["base_executable_path"]),
    }
    dll_inputs: list[str] = []
    dll_seen: set[str] = set()
    for raw_path in windows_module_paths:
        path = _windows_path(ntpath.normpath(raw_path), "windows_loaded_module")
        key = _comparable_path(path)
        if key in dll_seen:
            raise PythonTcbInventoryError("windows_loaded_module_duplicate")
        dll_seen.add(key)
        if key in executable_keys:
            continue
        dll_inputs.append(path)
        request(path, "windows_loaded_dll")

    files: list[dict[str, Any]] = []
    final_by_input: dict[str, str] = {}
    final_seen: set[str] = set()
    for key in sorted(requests):
        entry = requests[key]
        pin = _read_file_pin(entry["display"], entry["roles"], reader)
        final_key = _comparable_path(pin["path"])
        if final_key in final_seen:
            raise PythonTcbInventoryError("same_handle_final_path_duplicate")
        final_seen.add(final_key)
        final_by_input[key] = pin["path"]
        files.append(pin)
    files = sorted(files, key=lambda record: (_comparable_path(record["path"]), record["path"]))

    runtime_value["executable_path"] = final_by_input[
        _comparable_path(runtime_value["executable_path"])
    ]
    runtime_value["base_executable_path"] = final_by_input[
        _comparable_path(runtime_value["base_executable_path"])
    ]
    for module in module_values:
        if module["file_path"] is not None:
            module["file_path"] = final_by_input[_comparable_path(module["file_path"])]
    dll_values = _sorted_paths([final_by_input[_comparable_path(path)] for path in dll_inputs])

    classification_counts = {
        classification: sum(module["classification"] == classification for module in module_values)
        for classification in sorted(MODULE_CLASSIFICATIONS)
    }
    counts = {
        "files": len(files),
        "module_built_in": classification_counts["built_in"],
        "module_bytecode": classification_counts["bytecode"],
        "module_extension": classification_counts["extension"],
        "module_frozen": classification_counts["frozen"],
        "module_namespace": classification_counts["namespace"],
        "module_source": classification_counts["source"],
        "modules": len(module_values),
        "windows_loaded_dlls": len(dll_values),
    }
    payload = {
        "approval": {
            "external_receipt_present": False,
            "production_approval_eligible": False,
        },
        "call_counts": dict(CALL_COUNTS),
        "counts": counts,
        "files": files,
        "modules": module_values,
        "purpose": PURPOSE,
        "python_runtime": runtime_value,
        "read_policy": dict(READ_POLICY),
        "status": STATUS,
        "windows_loaded_dlls": dll_values,
    }
    document = {
        "payload": payload,
        "payload_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "schema_version": SCHEMA_VERSION,
    }
    raw = canonical_json_bytes(document)
    validate_inventory(raw, expected_document_sha256=hashlib.sha256(raw).hexdigest())
    return document


def collect_current_inventory(
    *,
    reader: BoundReader = read_bound_file,
    dll_provider: DllPathProvider = enumerate_windows_loaded_module_paths,
) -> dict[str, Any]:
    runtime = snapshot_current_runtime()
    modules = snapshot_loaded_modules()
    windows_modules = list(dll_provider())
    return build_inventory(
        runtime=runtime,
        modules=modules,
        windows_module_paths=windows_modules,
        reader=reader,
    )


def _validate_files_and_references(payload: dict[str, Any]) -> None:
    files = payload["files"]
    if not isinstance(files, list):
        raise PythonTcbInventoryError("inventory_files_not_array")
    if files != sorted(
        files, key=lambda record: (_comparable_path(record.get("path", "")), record.get("path", ""))
    ):
        raise PythonTcbInventoryError("inventory_files_not_sorted")

    by_path: dict[str, dict[str, Any]] = {}
    comparable_seen: set[str] = set()
    for record in files:
        if not isinstance(record, dict):
            raise PythonTcbInventoryError("inventory_file_not_object")
        _expect_exact_keys(record, {"bytes", "path", "roles", "sha256"}, "inventory_file")
        path = _windows_path(record["path"], "inventory_file")
        comparable = _comparable_path(path)
        if comparable in comparable_seen:
            raise PythonTcbInventoryError("inventory_file_path_duplicate")
        comparable_seen.add(comparable)
        _require_plain_int(record["bytes"], "inventory_file_bytes")
        _require_lower_sha256(record["sha256"], "inventory_file")
        roles = record["roles"]
        if (
            not isinstance(roles, list)
            or not roles
            or roles != sorted(set(roles))
            or not set(roles) <= FILE_ROLES
        ):
            raise PythonTcbInventoryError("inventory_file_roles_invalid")
        by_path[path] = record

    expected_roles: dict[str, set[str]] = {}

    def add(path: str, role: str) -> None:
        if path not in by_path:
            raise PythonTcbInventoryError("inventory_file_reference_missing")
        expected_roles.setdefault(path, set()).add(role)

    runtime = payload["python_runtime"]
    add(runtime["executable_path"], "python_executable")
    add(runtime["base_executable_path"], "python_base_executable")
    for module in payload["modules"]:
        classification = module["classification"]
        if classification in CLASSIFICATION_ROLE:
            add(module["file_path"], CLASSIFICATION_ROLE[classification])
    dlls = payload["windows_loaded_dlls"]
    if not isinstance(dlls, list) or dlls != _sorted_paths(dlls):
        raise PythonTcbInventoryError("windows_loaded_dlls_not_sorted")
    dll_seen: set[str] = set()
    for path in dlls:
        _windows_path(path, "windows_loaded_dll")
        comparable = _comparable_path(path)
        if comparable in dll_seen:
            raise PythonTcbInventoryError("windows_loaded_dll_duplicate")
        dll_seen.add(comparable)
        add(path, "windows_loaded_dll")

    if set(by_path) != set(expected_roles):
        raise PythonTcbInventoryError("inventory_file_set_not_exact")
    for path, expected in expected_roles.items():
        if by_path[path]["roles"] != sorted(expected):
            raise PythonTcbInventoryError("inventory_file_role_binding_mismatch")


def validate_inventory(
    raw: bytes,
    *,
    expected_document_sha256: str,
) -> dict[str, Any]:
    expected = _require_lower_sha256(expected_document_sha256, "expected_document")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise PythonTcbInventoryError("inventory_document_sha256_mismatch")
    document = _strict_json(raw)
    _expect_exact_keys(document, {"payload", "payload_sha256", "schema_version"}, "document")
    if document["schema_version"] != SCHEMA_VERSION:
        raise PythonTcbInventoryError("inventory_schema_version_mismatch")
    payload = document["payload"]
    if not isinstance(payload, dict):
        raise PythonTcbInventoryError("inventory_payload_not_object")
    _require_lower_sha256(document["payload_sha256"], "payload")
    if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != document["payload_sha256"]:
        raise PythonTcbInventoryError("inventory_payload_sha256_mismatch")
    _expect_exact_keys(
        payload,
        {
            "approval",
            "call_counts",
            "counts",
            "files",
            "modules",
            "purpose",
            "python_runtime",
            "read_policy",
            "status",
            "windows_loaded_dlls",
        },
        "payload",
    )
    if payload["status"] != STATUS or payload["purpose"] != PURPOSE:
        raise PythonTcbInventoryError("inventory_status_or_purpose_mismatch")
    if payload["approval"] != {
        "external_receipt_present": False,
        "production_approval_eligible": False,
    }:
        raise PythonTcbInventoryError("inventory_approval_boundary_mismatch")
    if payload["call_counts"] != CALL_COUNTS:
        raise PythonTcbInventoryError("inventory_call_counts_nonzero_or_unknown")
    if payload["read_policy"] != READ_POLICY:
        raise PythonTcbInventoryError("inventory_read_policy_mismatch")
    _validate_runtime(payload["python_runtime"])
    modules = _validate_modules(payload["modules"])
    _validate_files_and_references(payload)

    counts = payload["counts"]
    if not isinstance(counts, dict):
        raise PythonTcbInventoryError("inventory_counts_not_object")
    expected_counts = {
        "files": len(payload["files"]),
        "module_built_in": sum(module["classification"] == "built_in" for module in modules),
        "module_bytecode": sum(module["classification"] == "bytecode" for module in modules),
        "module_extension": sum(module["classification"] == "extension" for module in modules),
        "module_frozen": sum(module["classification"] == "frozen" for module in modules),
        "module_namespace": sum(module["classification"] == "namespace" for module in modules),
        "module_source": sum(module["classification"] == "source" for module in modules),
        "modules": len(modules),
        "windows_loaded_dlls": len(payload["windows_loaded_dlls"]),
    }
    if counts != expected_counts:
        raise PythonTcbInventoryError("inventory_counts_mismatch")
    return document


def render_inventory(document: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(document)


def main() -> int:
    if len(sys.argv) != 1:
        raise PythonTcbInventoryError("inventory_cli_accepts_no_arguments")
    require_isolated_direct_invocation()
    document = collect_current_inventory()
    raw = render_inventory(document)
    validate_inventory(raw, expected_document_sha256=hashlib.sha256(raw).hexdigest())
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
