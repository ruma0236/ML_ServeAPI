from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import ntpath
import py_compile
import struct
import sys
import uuid
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s7_admission as admission
from evm.scale_validation import phase_b2_r7s7_qualification_work_order as gate


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/dev/prepare_pre_r8_r7s7_windows_qualification.py"
SPEC = importlib.util.spec_from_file_location("prepare_pre_r8_r7s7_windows_qualification", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
preparer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preparer
SPEC.loader.exec_module(preparer)
assert Path(preparer.__file__).resolve() == SCRIPT.resolve()

GLOBAL_RUN_ID = "1980993b-1baf-4d52-9fd5-1c42be2b6559"
RUN_UUID = "896a94b6-a26b-4670-b185-04ff7778e06a"
ATTEMPT_UUID = "36f3b37a-cc30-4d9d-81e1-5fe01339af94"
COMMIT = "1" * 40
TREE = "2" * 40


@pytest.fixture(autouse=True)
def _isolate_verified_project_imports() -> Any:
    """Model the preparer's one-shot process boundary inside the shared pytest process."""
    original_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "evm" or name.startswith("evm.")
    }
    original_path = list(sys.path)
    original_bindings = (
        preparer.handle_io,
        preparer.publish_bound_no_replace_durable,
        preparer.work_order_gate,
    )
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if name == "evm" or name.startswith("evm."):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        sys.path[:] = original_path
        (
            preparer.handle_io,
            preparer.publish_bound_no_replace_durable,
            preparer.work_order_gate,
        ) = original_bindings


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normal(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def _untracked_digest(files: list[dict[str, Any]]) -> str:
    value = {
        "schema": gate.PRESERVED_UNTRACKED_SCHEMA,
        "scope": gate.PRESERVED_UNTRACKED_SCOPE,
        "files": files,
        "count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "import_active_count": 0,
    }
    return _sha(gate.canonical_json_bytes(value))


def _directory(role: str, path: str, *, protected: bool = False) -> dict[str, Any]:
    normalized = _normal(path)
    return {
        "role": role,
        "final_path": path,
        "volume_serial_number": 77,
        "file_id_hex": _sha(normalized.encode())[:32],
        "owner_sid": "S-1-5-21-1-2-3-1001",
        "security_descriptor_sha256": _sha(f"sd:{normalized}".encode()),
        "dacl_present": True,
        "dacl_protected": protected,
        "link_count": 1,
        "reparse_tag": 0,
        "file_type": 1,
        "is_directory": True,
    }


class FakeIdentities:
    def __init__(self, request: Any, *, mutation: str | None = None) -> None:
        self.request = request
        self.mutation = mutation
        self.calls: list[tuple[str, str, str]] = []
        self.pin_by_path = {
            _normal(request.interpreter.path): request.interpreter.sha256,
            _normal(request.powershell.path): request.powershell.sha256,
            _normal(request.codex.path): request.codex.sha256,
            _normal(request.command_processor.path): request.command_processor.sha256,
            _normal(str(SCRIPT)): request.expected_preparer_sha256,
        }

    def directory(self, role: str, path: str) -> dict[str, Any]:
        self.calls.append(("directory", role, path))
        if self.mutation == "canonical_parent_missing" and role == "qualification:output_parent":
            raise FileNotFoundError(path)
        canonical_parent = role in {
            "qualification:output_parent",
            "qualification:pycache_parent",
            "qualification:work_order_parent",
        }
        result = _directory(role, path, protected=canonical_parent or "powershell" in path.lower())
        if self.mutation == "directory_reparse" and role == "qualification:output_parent":
            result["reparse_tag"] = 0xA000000C
        if (
            self.mutation == "canonical_parent_unprotected"
            and role == "qualification:output_parent"
        ):
            result["dacl_protected"] = False
        return result

    def file(self, role: str, path: str) -> dict[str, Any]:
        self.calls.append(("file", role, path))
        normalized = _normal(path)
        candidate = Path(path)
        project_raw = (
            candidate.read_bytes() if candidate.is_file() and ROOT in candidate.parents else None
        )
        digest = self.pin_by_path.get(
            normalized,
            _sha(project_raw)
            if project_raw is not None
            else _sha(f"content:{role}:{normalized}".encode()),
        )
        parent = self.directory(f"qualification:{role}:parent", ntpath.dirname(path))
        result = {
            "role": f"qualification:{role}",
            "final_path": path,
            "volume_serial_number": 77,
            "file_id_hex": _sha(f"file:{normalized}".encode())[:32],
            "sha256": digest,
            "bytes": len(project_raw) if project_raw is not None else 100 + len(role),
            "owner_sid": "S-1-5-21-1-2-3-1001",
            "security_descriptor_sha256": _sha(f"file-sd:{normalized}".encode()),
            "dacl_present": True,
            "dacl_protected": role in {"powershell", "command_processor"},
            "link_count": 1,
            "reparse_tag": 0,
            "file_type": 1,
            "creation_time_ns": 1_000_000 + len(self.calls),
            "parent_directory_identity": parent,
        }
        if self.mutation == "preparer_sha" and role == "preparer":
            result["sha256"] = "f" * 64
        if self.mutation == "source_reparse" and role == "work_order_gate":
            result["reparse_tag"] = 0xA000000C
        return result


class FakeRepository:
    def __init__(self, request: Any, **changes: Any) -> None:
        self.calls = 0
        paths = preparer._project_paths(request)
        external = {"interpreter", "powershell", "codex", "command_processor"}
        tracked_files = tuple(
            preparer.TrackedFileObservation(
                relative_path=(
                    Path(paths[role]).resolve(strict=False).relative_to(ROOT.parent).as_posix()
                ),
                blob_oid=f"{index + 1:040x}",
                sha256=_sha(Path(paths[role]).read_bytes()),
                bytes=len(Path(paths[role]).read_bytes()),
            )
            for index, role in enumerate(gate.FILE_BINDING_ROLES)
            if role not in external
        )
        self.observation = preparer.RepositoryObservation(
            checkout_root=str(ROOT.parent),
            commit=request.expected_commit,
            commit_tree=request.expected_tree,
            index_tree=request.expected_tree,
            tracked_entry_count=len(tracked_files),
            tracked_bytes=sum(item.bytes for item in tracked_files),
            tracked_files=tracked_files,
            clean=True,
            untracked_examined=True,
            untracked_count=0,
            untracked_bytes=0,
            untracked_files=(),
            untracked_inventory_sha256=_untracked_digest([]),
            untracked_import_active_count=0,
            child_process_count=0,
        )
        self.observation = replace(self.observation, **changes)

    def inspect(self, start: Path) -> Any:
        self.calls += 1
        assert start == ROOT
        return self.observation


class FakeLineage:
    def __init__(self, identities: FakeIdentities, *, mismatch: bool = False) -> None:
        self.identities = identities
        self.mismatch = mismatch
        self.calls = 0

    def measure(self) -> dict[str, Any]:
        self.calls += 1
        paths = preparer._project_paths(self.identities.request)
        bindings = {
            role: self.identities.file(role, paths[role])
            for role in ("interpreter", "powershell", "codex")
        }

        def record(role: str, pid: int, ppid: int) -> dict[str, Any]:
            return {
                "pid": pid,
                "ppid": ppid,
                "session_id": 9,
                "path": bindings[role]["final_path"],
                "image_sha256": bindings[role]["sha256"],
                "token": {
                    "administrator": True,
                    "integrity": "High",
                    "token_elevation_type": "Full",
                },
            }

        python = record("interpreter", 300, 200)
        powershell = record("powershell", 200, 100)
        codex = record("codex", 100, 1)
        codex.update(
            {
                "danger_full_access_flag_present": True,
                "approval_never_flag_present": True,
                "command_line_persisted": False,
            }
        )
        if self.mismatch:
            python["image_sha256"] = "e" * 64
        return {"python": python, "powershell": powershell, "codex": codex}


class FakePublisher:
    def __init__(self, *, fail: bool = False, directory_mutation: bool = False) -> None:
        self.fail = fail
        self.directory_mutation = directory_mutation
        self.calls: list[dict[str, Any]] = []

    def publish(self, *, root: str, leaf: str, raw: bytes, run_uuid: str) -> Any:
        self.calls.append({"root": root, "leaf": leaf, "raw": raw, "run_uuid": run_uuid})
        if self.fail:
            raise preparer.PreparerError("injected_publish_failure", stage="publication")
        directory_identity = _directory("qualification:work_order_parent", root, protected=True)
        directory_identity.pop("role")
        directory_identity.pop("is_directory")
        directory_identity["size"] = 0
        directory_identity["attributes"] = 0x10
        if self.directory_mutation:
            directory_identity["file_id_hex"] = "f" * 32
        return preparer.PublicationObservation(
            final_path=ntpath.join(root, leaf),
            sha256=_sha(raw),
            bytes=len(raw),
            file_flush_count=2,
            directory_flush_count=1,
            directory_flush_succeeded=True,
            same_handle_readback=True,
            file_identity_stable_across_rename=True,
            replace_if_exists=False,
            file_identity={"final_path": ntpath.join(root, leaf)},
            directory_identity=directory_identity,
            create_attempt_count=1,
        )


def _request(**changes: Any) -> Any:
    value = preparer.PrepareRequest(
        global_run_id=GLOBAL_RUN_ID,
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        expected_commit=COMMIT,
        expected_tree=TREE,
        expected_preparer_sha256=_sha(SCRIPT.read_bytes()),
        expected_untracked_count=0,
        expected_untracked_bytes=0,
        expected_untracked_inventory_sha256=_untracked_digest([]),
        interpreter=preparer.ExecutablePin(r"C:\Python311\python.exe", "a" * 64),
        powershell=preparer.ExecutablePin(
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "b" * 64
        ),
        codex=preparer.ExecutablePin(r"C:\trusted\codex.exe", "c" * 64),
        command_processor=preparer.ExecutablePin(r"C:\Windows\System32\cmd.exe", "d" * 64),
    )
    return replace(value, **changes)


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    *,
    request: Any | None = None,
    repository_changes: dict[str, Any] | None = None,
    identity_mutation: str | None = None,
    lineage_mismatch: bool = False,
    publisher: FakePublisher | None = None,
    exists: Any = None,
) -> tuple[dict[str, Any], FakeRepository, FakeIdentities, FakeLineage, FakePublisher]:
    request = request or _request()
    identities = FakeIdentities(request, mutation=identity_mutation)
    repository = FakeRepository(request, **(repository_changes or {}))
    lineage = FakeLineage(identities, mismatch=lineage_mismatch)
    publisher = publisher or FakePublisher()
    expected_prefix = ntpath.join(
        gate.CANONICAL_PYCACHE_ROOT, f"{request.run_uuid}-{request.attempt_uuid}"
    )
    monkeypatch.setattr(sys, "pycache_prefix", expected_prefix)
    result = preparer.prepare_internal_non_authoritative_once(
        request,
        repository=repository,
        identities=identities,
        lineage=lineage,
        publisher=publisher,
        path_exists=(exists or (lambda _path: False)),
    )
    return result, repository, identities, lineage, publisher


def test_preparer_publishes_one_self_pinned_internal_no_go_work_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, repository, identities, lineage, publisher = _invoke(monkeypatch)
    assert result["status"] == "internal_non_authoritative"
    assert result["decision"] == "NO-GO"
    assert result["credit"] == "zero_credit"
    assert result["production_go"] is False
    assert result["same_token_hostile_admin_protected"] is False
    assert result["toolchain_runtime_closure_state"] == "unproven"
    assert result["reviewer_blockers"] == [
        "external_oob_work_order_authority_required",
        "preparer_prelaunch_trusted_pin_unproven",
        "python_runtime_transitive_closure_unproven",
        "same_token_hostile_admin_tamper_resistance_unproven",
    ]
    assert result["call_counts"] == {
        "repository_process_creation": 0,
        "qualification_process_creation": 0,
        "work_order_publication": 1,
        "automatic_retry": 0,
        "force_termination": 0,
        "success_marker": 0,
        "completion_marker": 0,
    }
    assert repository.calls == 1
    assert lineage.calls == 1
    assert len(publisher.calls) == 1
    raw = publisher.calls[0]["raw"]
    assert result["work_order_sha256"] == _sha(raw)
    value = json.loads(raw)
    assert set(value["file_bindings"]) == set(gate.FILE_BINDING_ROLES)
    assert value["file_bindings"]["preparer"]["sha256"] == _sha(SCRIPT.read_bytes())
    assert value["source_closure"]["roles"] == list(gate.SOURCE_CLOSURE_ROLES)
    assert value["file_bindings"]["interpreter"]["dacl_protected"] is False
    assert value["same_token_hostile_admin_protected"] is False
    assert value["toolchain_runtime_closure_state"] == "unproven"
    assert value["reviewer_blockers"] == result["reviewer_blockers"]
    assert value["preserved_untracked_inventory"]["count"] == 0
    assert (
        value["preserved_untracked_inventory"]["scope"]
        == "all_regular_files_not_in_index_including_git_ignored"
    )
    assert result["canonical_parent_preconditions"]["verified"] is True
    assert result["canonical_parent_preconditions"]["provisioning_performed_by_preparer"] is False
    expectation = gate.QualificationWorkOrderExpectation(
        work_order_sha256=_sha(raw),
        global_run_id=GLOBAL_RUN_ID,
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        commit=COMMIT,
        tree=TREE,
    )
    gate.verify_internal_qualification_work_order(raw, expected=expectation)
    assert identities.calls


@pytest.mark.parametrize(
    ("request_change", "repository_change", "identity_mutation", "lineage_mismatch", "match"),
    [
        ({"run_uuid": "not-a-uuid"}, {}, None, False, "uuid4_required"),
        ({}, {"clean": False}, None, False, "checkout_commit_tree_or_cleanliness_mismatch"),
        ({}, {"index_tree": "3" * 40}, None, False, "checkout_commit_tree_or_cleanliness_mismatch"),
        (
            {},
            {"tracked_files": (), "tracked_entry_count": 0, "tracked_bytes": 0},
            None,
            False,
            "not_tracked_before_import",
        ),
        ({}, {}, "preparer_sha", False, "preparer_pin_mismatch"),
        ({}, {}, "source_reparse", False, "reparse_present"),
        ({}, {}, "directory_reparse", False, "external_canonical_parent_provisioning_required"),
        (
            {},
            {},
            "canonical_parent_missing",
            False,
            "external_canonical_parent_provisioning_required",
        ),
        (
            {},
            {},
            "canonical_parent_unprotected",
            False,
            "external_canonical_parent_provisioning_required",
        ),
        ({}, {}, None, True, "lineage_or_token_mismatch"),
    ],
)
def test_preparer_rejects_identity_repository_and_lineage_mutations_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    request_change: dict[str, Any],
    repository_change: dict[str, Any],
    identity_mutation: str | None,
    lineage_mismatch: bool,
    match: str,
) -> None:
    request = _request(**request_change)
    publisher = FakePublisher()
    with pytest.raises(Exception, match=match):
        _invoke(
            monkeypatch,
            request=request,
            repository_changes=repository_change,
            identity_mutation=identity_mutation,
            lineage_mismatch=lineage_mismatch,
            publisher=publisher,
        )
    assert publisher.calls == []


def test_collision_and_publication_failure_have_no_retry_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collision_publisher = FakePublisher()
    with pytest.raises(preparer.PreparerError, match="identity_collision") as caught:
        _invoke(
            monkeypatch,
            publisher=collision_publisher,
            exists=lambda path: path.endswith(".json"),
        )
    assert caught.value.call_counts["qualification_process_creation"] == 0
    assert collision_publisher.calls == []

    broken = FakePublisher(fail=True)
    with pytest.raises(preparer.PreparerError, match="work_order_publication_failed") as caught:
        _invoke(monkeypatch, publisher=broken)
    assert len(broken.calls) == 1
    assert caught.value.call_counts["work_order_publication"] == 1
    assert caught.value.call_counts["automatic_retry"] == 0
    assert caught.value.call_counts["success_marker"] == 0
    assert caught.value.call_counts["completion_marker"] == 0

    swapped_parent = FakePublisher(directory_mutation=True)
    with pytest.raises(preparer.PreparerError, match="publication_parent_identity_mismatch"):
        _invoke(monkeypatch, publisher=swapped_parent)
    assert len(swapped_parent.calls) == 1


def test_missing_canonical_parent_stops_before_lineage_or_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    identities = FakeIdentities(request, mutation="canonical_parent_missing")
    repository = FakeRepository(request)
    lineage = FakeLineage(identities)
    publisher = FakePublisher()
    monkeypatch.setattr(
        sys,
        "pycache_prefix",
        ntpath.join(gate.CANONICAL_PYCACHE_ROOT, f"{RUN_UUID}-{ATTEMPT_UUID}"),
    )
    with pytest.raises(
        preparer.PreparerError,
        match="external_canonical_parent_provisioning_required",
    ) as caught:
        preparer.prepare_internal_non_authoritative_once(
            request,
            repository=repository,
            identities=identities,
            lineage=lineage,
            publisher=publisher,
            path_exists=lambda _path: False,
        )
    assert caught.value.stage == "canonical_parent_preflight"
    assert caught.value.call_counts["qualification_process_creation"] == 0
    assert caught.value.call_counts["work_order_publication"] == 0
    assert lineage.calls == 0
    assert publisher.calls == []


def test_tracked_source_drift_is_rejected_before_any_project_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    baseline = FakeRepository(request).observation
    rows = list(baseline.tracked_files)
    target_index = next(
        index
        for index, item in enumerate(rows)
        if item.relative_path.endswith("phase_b2_r7s7_qualification_work_order.py")
    )
    rows[target_index] = replace(rows[target_index], sha256="f" * 64)
    imports: list[str] = []
    monkeypatch.setattr(
        preparer,
        "_load_verified_project_modules",
        lambda: imports.append("project_import"),
    )
    with pytest.raises(preparer.PreparerError, match="content_changed_before_import"):
        _invoke(
            monkeypatch,
            request=request,
            repository_changes={"tracked_files": tuple(rows)},
        )
    assert imports == []


def test_bootstrap_and_source_are_process_free_redacted_and_no_fallback() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    prefix = source.split("PROJECT_ROOT =", 1)[0]
    assert '"isolated": 1' in prefix
    assert '"no_user_site": 1' in prefix
    assert '"no_site": 1' in prefix
    assert '"dont_write_bytecode": 1' in prefix
    assert "sys.pycache_prefix" in prefix
    pre_loader = source.split("def _load_verified_project_modules", 1)[0]
    assert "sys.path.insert" not in pre_loader
    assert "from evm" not in pre_loader
    assert 'import_module("evm' not in pre_loader
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "Popen" not in source
    assert "success_marker" in source and "completion_marker" in source
    assert '"production_go": False' in source
    assert "unlink(" not in source
    assert "os.replace(" not in source
    assert ".rename(" not in source


def test_production_admission_still_rejects_unprotected_dacl() -> None:
    source = Path(admission.__file__).read_text(encoding="utf-8")
    assert "value.dacl_present is not True or value.dacl_protected is not True" in source


def test_real_layout_uses_outer_git_root_and_project_prefixed_tracked_paths() -> None:
    request = _request()
    repository = FakeRepository(request)
    assert Path(repository.observation.checkout_root) == ROOT.parent
    assert all(
        item.relative_path.startswith(f"{ROOT.name}/")
        for item in repository.observation.tracked_files
    )


def test_outer_transport_does_not_put_work_order_raw_in_argv_or_environment() -> None:
    source = (ROOT / "scripts/dev/invoke_pre_r8_r7s7_windows_qualification.ps1").read_text(
        encoding="utf-8"
    )
    assert "rawBase64" not in source
    assert "expectationBase64" not in source
    assert "ToBase64String" not in source
    assert "base64" not in source.lower()
    assert "$workOrderHandle.Path" in source
    assert "$ExpectedWorkOrderSha256" in source
    assert "work_order_path.read_bytes()" in source
    assert "toolchain_runtime_closure_unproven" in source
    assert "python_runtime_transitive_closure_unproven" in source
    assert "'preparer'" in source


def test_uuid_replay_path_is_run_and_attempt_unique() -> None:
    first = _request()
    second = replace(first, attempt_uuid=str(uuid.uuid4()))
    assert preparer._expected_work_order_path(first) != preparer._expected_work_order_path(second)


def test_tracked_clean_filter_accepts_only_exact_windows_crlf_projection(
    tmp_path: Path,
) -> None:
    lf = b"first line\nsecond line\n"
    crlf = lf.replace(b"\n", b"\r\n")
    tracked = tmp_path / "tracked.txt"
    tracked.write_bytes(crlf)
    entry = preparer._IndexEntry(
        path_raw=b"tracked.txt",
        mode=0o100644,
        oid=preparer._git_object_id(b"blob", lf),
    )

    clean, total, observed = preparer._tracked_worktree_clean(
        tmp_path,
        (entry,),
        allow_windows_crlf_clean_filter=True,
    )
    assert clean is True
    assert total == len(crlf)
    assert observed[0].blob_oid == entry.oid.hex()
    assert observed[0].sha256 == _sha(crlf)
    assert observed[0].bytes == len(crlf)

    clean_without_filter, _, _ = preparer._tracked_worktree_clean(
        tmp_path,
        (entry,),
        allow_windows_crlf_clean_filter=False,
    )
    assert clean_without_filter is False

    # Same-size content drift is not hidden by the line-ending projection.
    tracked.write_bytes(crlf.replace(b"first", b"frost"))
    changed, _, _ = preparer._tracked_worktree_clean(
        tmp_path,
        (entry,),
        allow_windows_crlf_clean_filter=True,
    )
    assert changed is False


def test_tracked_clean_filter_rejects_binary_and_bare_cr_projection(tmp_path: Path) -> None:
    entry = preparer._IndexEntry(
        path_raw=b"tracked.bin",
        mode=0o100644,
        oid=preparer._git_object_id(b"blob", b"a\nb\0c\n"),
    )
    tracked = tmp_path / "tracked.bin"
    tracked.write_bytes(b"a\r\nb\0c\r\n")
    binary_clean, _, _ = preparer._tracked_worktree_clean(
        tmp_path,
        (entry,),
        allow_windows_crlf_clean_filter=True,
    )
    assert binary_clean is False

    bare_lf = b"a\nb\n"
    bare_entry = preparer._IndexEntry(
        path_raw=b"tracked.bin",
        mode=0o100644,
        oid=preparer._git_object_id(b"blob", bare_lf),
    )
    tracked.write_bytes(b"a\r\nb\r")
    bare_cr_clean, _, _ = preparer._tracked_worktree_clean(
        tmp_path,
        (bare_entry,),
        allow_windows_crlf_clean_filter=True,
    )
    assert bare_cr_clean is False


def test_exact_historical_untracked_inventory_is_preserved_and_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = [
        preparer.UntrackedFileObservation(
            relative_path=".r7s5-validation/run/a.json",
            sha256="a" * 64,
            bytes=7,
        ),
        preparer.UntrackedFileObservation(
            relative_path="enterprise-vision-mlops/.r7s5-ci-readback/b.xml",
            sha256="b" * 64,
            bytes=9,
        ),
    ]
    rows = [
        {"relative_path": item.relative_path, "sha256": item.sha256, "bytes": item.bytes}
        for item in files
    ]
    request = _request(
        expected_untracked_count=2,
        expected_untracked_bytes=16,
        expected_untracked_inventory_sha256=_untracked_digest(rows),
    )
    result, _, _, _, publisher = _invoke(
        monkeypatch,
        request=request,
        repository_changes={
            "untracked_count": 2,
            "untracked_bytes": 16,
            "untracked_files": tuple(files),
            "untracked_inventory_sha256": _untracked_digest(rows),
        },
    )
    raw = json.loads(publisher.calls[0]["raw"])
    assert raw["preserved_untracked_inventory"]["files"] == rows
    assert result["repository"]["untracked_count"] == 2

    changed = list(rows)
    changed[0] = dict(changed[0], sha256="c" * 64)
    with pytest.raises(preparer.PreparerError, match="cleanliness_mismatch"):
        _invoke(
            monkeypatch,
            request=request,
            repository_changes={
                "untracked_count": 2,
                "untracked_bytes": 16,
                "untracked_files": tuple(
                    preparer.UntrackedFileObservation(**item) for item in changed
                ),
                "untracked_inventory_sha256": _untracked_digest(changed),
            },
        )


def test_project_role_left_only_as_untracked_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_request = _request()
    baseline = FakeRepository(baseline_request).observation
    missing = next(
        item
        for item in baseline.tracked_files
        if item.relative_path.endswith("scripts/dev/prepare_pre_r8_r7s7_windows_qualification.py")
    )
    preserved = preparer.UntrackedFileObservation(
        relative_path=missing.relative_path,
        sha256=missing.sha256,
        bytes=missing.bytes,
    )
    rows = [
        {
            "relative_path": preserved.relative_path,
            "sha256": preserved.sha256,
            "bytes": preserved.bytes,
        }
    ]
    request = _request(
        expected_untracked_count=1,
        expected_untracked_bytes=preserved.bytes,
        expected_untracked_inventory_sha256=_untracked_digest(rows),
    )
    remaining = tuple(item for item in baseline.tracked_files if item != missing)
    with pytest.raises(preparer.PreparerError, match="preparer_not_tracked_before_import"):
        _invoke(
            monkeypatch,
            request=request,
            repository_changes={
                "tracked_files": remaining,
                "tracked_entry_count": len(remaining),
                "tracked_bytes": sum(item.bytes for item in remaining),
                "untracked_count": 1,
                "untracked_bytes": preserved.bytes,
                "untracked_files": (preserved,),
                "untracked_inventory_sha256": _untracked_digest(rows),
            },
        )


def test_untracked_inventory_detects_same_size_change_and_import_active_file(
    tmp_path: Path,
) -> None:
    project = tmp_path / "enterprise-vision-mlops"
    history = tmp_path / ".r7s5-validation" / "run"
    readback = project / ".r7s5-ci-readback"
    history.mkdir(parents=True)
    readback.mkdir(parents=True)
    first = history / "a.json"
    second = readback / "b.xml"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    files, total, active, digest = preparer._untracked_inventory(
        tmp_path,
        (),
        project_root=project,
    )
    assert [item.relative_path for item in files] == [
        ".r7s5-validation/run/a.json",
        "enterprise-vision-mlops/.r7s5-ci-readback/b.xml",
    ]
    assert total == 11
    assert active == 0
    assert len(digest) == 64

    first.write_bytes(b"frost")
    changed = preparer._untracked_inventory(tmp_path, (), project_root=project)
    assert changed[3] != digest

    attack = project / "scripts" / "dev" / "attack.py"
    attack.parent.mkdir(parents=True)
    attack.write_text("raise SystemExit(1)\n", encoding="utf-8")
    assert preparer._untracked_inventory(tmp_path, (), project_root=project)[2] == 1


def test_legacy_sourceless_module_is_importable_but_rejected_before_project_import(
    tmp_path: Path,
) -> None:
    project = tmp_path / "enterprise-vision-mlops"
    package = project / "src" / "evm"
    package.mkdir(parents=True)
    marker = tmp_path / "sourceless-side-effect.txt"
    source = package / "shadow.py"
    legacy = package / "shadow.pyc"
    source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    py_compile.compile(str(source), cfile=str(legacy), doraise=True)
    source.unlink()

    finder = importlib.machinery.FileFinder(
        str(package),
        (importlib.machinery.SourcelessFileLoader, importlib.machinery.BYTECODE_SUFFIXES),
    )
    spec = finder.find_spec("shadow")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert marker.read_text() == "ran"
    marker.unlink()

    relative = "enterprise-vision-mlops/src/evm/shadow.pyc"
    assert (
        preparer._is_import_active_untracked(
            relative,
            project_relative_root="enterprise-vision-mlops",
        )
        is True
    )
    with pytest.raises(RuntimeError, match="legacy_sourceless_module_present"):
        preparer._reject_bootstrap_source_import_hazards(project / "src")
    assert not marker.exists()


def test_top_level_source_shadow_is_rejected_before_sys_path_insertion(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    (source_root / "evm").mkdir(parents=True)
    shadow = source_root / "ctypes.py"
    shadow.write_text("raise AssertionError('must never execute')\n", encoding="utf-8")
    finder = importlib.machinery.FileFinder(
        str(source_root),
        (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
    )
    assert finder.find_spec("ctypes") is not None
    with pytest.raises(RuntimeError, match="noncanonical_top_level_source_present"):
        preparer._reject_bootstrap_source_import_hazards(source_root)


def _write_single_commit_pack(git_dir: Path, payload: bytes) -> str:
    oid = hashlib.sha1(b"commit " + str(len(payload)).encode() + b"\0" + payload).digest()
    remaining = len(payload) >> 4
    header = bytearray([(1 << 4) | (len(payload) & 0x0F)])
    if remaining:
        header[0] |= 0x80
    while remaining:
        byte = remaining & 0x7F
        remaining >>= 7
        if remaining:
            byte |= 0x80
        header.append(byte)
    packed_entry = bytes(header) + zlib.compress(payload)
    pack_without_trailer = b"PACK" + struct.pack(">II", 2, 1) + packed_entry
    pack_checksum = hashlib.sha1(pack_without_trailer).digest()
    pack_raw = pack_without_trailer + pack_checksum
    pack_dir = git_dir / "objects" / "pack"
    pack_dir.mkdir(parents=True)
    stem = pack_dir / f"pack-{pack_checksum.hex()}"
    stem.with_suffix(".pack").write_bytes(pack_raw)

    fanout = [0] * 256
    for index in range(oid[0], 256):
        fanout[index] = 1
    index_without_checksum = b"".join(
        (
            b"\xfftOc",
            struct.pack(">I", 2),
            struct.pack(">256I", *fanout),
            oid,
            struct.pack(">I", zlib.crc32(packed_entry) & 0xFFFFFFFF),
            struct.pack(">I", 12),
            pack_checksum,
        )
    )
    index_raw = index_without_checksum + hashlib.sha1(index_without_checksum).digest()
    stem.with_suffix(".idx").write_bytes(index_raw)
    return oid.hex()


def test_pure_git_reader_accepts_checksum_bound_packed_commit_object(tmp_path: Path) -> None:
    tree = "2" * 40
    payload = f"tree {tree}\nauthor test <t@example.invalid> 0 +0000\n\npacked\n".encode()
    oid = _write_single_commit_pack(tmp_path, payload)
    assert preparer._git_object(tmp_path, oid, b"commit") == payload
    assert preparer._commit_tree(tmp_path, oid) == tree

    pack = next((tmp_path / "objects" / "pack").glob("*.pack"))
    raw = bytearray(pack.read_bytes())
    raw[-1] ^= 1
    pack.write_bytes(raw)
    with pytest.raises(preparer.PreparerError, match="checksum_mismatch"):
        preparer._git_object(tmp_path, oid, b"commit")


def test_canonical_parent_contract_is_external_exact_and_never_self_provisioned() -> None:
    contract = preparer.canonical_parent_provisioning_contract()
    assert contract["external_provisioning_required"] is True
    assert contract["preparer_may_create_or_modify_paths"] is False
    assert contract["dacl_protected_required"] is True
    assert [item["path"] for item in contract["expected_directories"]] == [
        gate.CANONICAL_OUTPUT_ROOT,
        gate.CANONICAL_PYCACHE_ROOT,
        gate.CANONICAL_WORK_ORDER_ROOT,
    ]
    source = SCRIPT.read_text(encoding="utf-8")
    assert ".mkdir(" not in source
    assert "os.makedirs(" not in source
    assert "external_canonical_parent_provisioning_required" in source
    assert "require_protected_dacl=True" in source
