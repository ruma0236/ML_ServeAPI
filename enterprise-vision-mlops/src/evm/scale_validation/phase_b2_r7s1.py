"""Fail-closed contracts and append-only evidence for Phase B2 r7s1.

R7s1 is a restore-only reconciliation harness. It has no fresh Phase B2 API and
performs no Docker, Kubernetes, WSL, or process-control action.  This module
validates immutable work-order inputs and writes one create-exclusive evidence
set after a runner has supplied a report.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from evm.scale_validation.phase_b2_r7_process import (
    PROCESS_CONTAINMENT_CONTRACT as R7_PROCESS_CONTAINMENT_CONTRACT,
)


SCHEMA_VERSION = "evm.s8_v4.x1_phase_b2_r7s1_restore_work_order.v1"
WORK_ORDER_ID = "s8-v4-x1-phase-b2-r7s1-restore-only-validation"
LAUNCHER_EVIDENCE_SCHEMA = "s8-v4-x1-phase-b2-r7s1-launcher-evidence/v1"
PRE_R7_REVISION = "167cb0176cb76b67085e218e89030a832f0f8ff2"
PRESERVED_UNTRACKED_COUNT = 4_244
UNTRACKED_PATH_SET_ENCODING = "ordinal-sorted UTF-8 paths, each NUL-terminated"

RUNTIME_COMPONENTS = (
    "builder",
    "core",
    "process",
    "runner",
    "validator",
    "docker_compose",
)
HOST_TOOLCHAIN_ROLES = (
    "python",
    "docker",
    "docker_compose",
    "kubectl",
    "wsl",
    "powershell",
    "git",
)
DOCKER_COMPOSE_EXECUTABLE = Path(
    "C:/Program Files/Docker/Docker/resources/bin/docker-compose.exe"
).resolve()
CANONICAL_GIT_CONFIG_PATH = Path("C:/Users/mlops/EnterpriseMLOps_Project/.git/config").resolve()
CANONICAL_GIT_CONFIG_SHA256 = "aefce0bafe9863032f40ed1f62d91c339a321ea61303b77941ec7e36c30028fa"
CANONICAL_GIT_CONFIG_BYTES = 787
GIT_CONFIG_ALLOWED_KEY_NAMES = (
    "branch.codex/distributed-scale-validation-plan.merge",
    "branch.codex/distributed-scale-validation-plan.remote",
    "branch.codex/local-infra-mvp.merge",
    "branch.codex/local-infra-mvp.remote",
    "branch.codex/mac-mini-worker.merge",
    "branch.codex/mac-mini-worker.remote",
    "branch.codex/x1-resume-results-20260825-215716.merge",
    "branch.codex/x1-resume-results-20260825-215716.remote",
    "core.bare",
    "core.filemode",
    "core.ignorecase",
    "core.logallrefupdates",
    "core.repositoryformatversion",
    "core.symlinks",
    "extensions.worktreeconfig",
    "remote.origin.fetch",
    "remote.origin.url",
    "user.email",
    "user.name",
)
GIT_CONFIG_ORIGIN_IDENTITY = {
    "scheme": "https",
    "host": "github.com",
    "path_sha256": "bc3c8d5edcc5862799d21d259324fc8f9f2b8fc6c724821ccb131e8296beba6b",
}
GIT_REPOSITORY_CONFIG_POLICY = {
    "schema": "s8-v4-x1-phase-b2-r7s1-git-config-policy/v1",
    "allowed_key_names": list(GIT_CONFIG_ALLOWED_KEY_NAMES),
    "forbidden_key_classes": [
        "include",
        "includeif",
        "filter",
        "core.fsmonitor",
        "core.attributesfile",
        "credential",
        "url-rewrite",
        "ssh-command",
        "external-helper",
    ],
    "origin_identity": dict(GIT_CONFIG_ORIGIN_IDENTITY),
    "config_worktree_absent": True,
}
CANONICAL_GIT_ATTRIBUTES_PATH = Path(
    "C:/Users/mlops/EnterpriseMLOps_Project/enterprise-vision-mlops/.gitattributes"
).resolve()
# Deterministic Windows checkout projection produced by the canonical
# repository's core.autocrlf=true policy (20 LF-enforced rules), not the
# mixed-EOL bytes of an intermediate development clone. Production authority
# still requires a post-fast-forward canonical worktree read-back.
CANONICAL_GIT_ATTRIBUTES_SHA256 = "b88aa1f439520fb303392a13f0a0a07642c8a5449bd7c409597ebd791f6d4c28"
CANONICAL_GIT_ATTRIBUTES_BYTES = 873
CANONICAL_GIT_TOP_ATTRIBUTES_PATH = Path(
    "C:/Users/mlops/EnterpriseMLOps_Project/.gitattributes"
).resolve()
CANONICAL_GIT_INFO_ATTRIBUTES_PATH = Path(
    "C:/Users/mlops/EnterpriseMLOps_Project/.git/info/attributes"
).resolve()
GIT_ATTRIBUTES_PATTERN_SHA256 = (
    "e4bb14173d817b251f7aeb59c87cba83429c31a29a7d16fdd2e6a3c9b1e12db0",
    "76ed074a9305c04054cdebb9e9aad2d818052b07091de1f20cad0bbac34ffb52",
    "396b92906a5a6d2c6a0749130e9d16ffd80bdb3c053c08533f0c9776e7abe4df",
    "76880eb6ef85265f8ff1b841f1a2c28be98fa6d229cff570c9a71af9d0b614f2",
    "ec5d2ab89ac415fede59987ca0f73ebc537b316a89c11cc81a021e43257f3ad7",
    "f76b5543e080ef60847945994d07674571695b4f10fffa8fc0c721c28767846d",
    "4554e3ad9b1f453e2fbfb81ac244d499c9232f71671d8f14cf5ad13298545d63",
    "a0e09a0bd20e893dfd512fdf009dfad0937a4d2687d83ee789a912320cd2d623",
    "64999824d016021f7f629ff79b4d5930fb2a3956dda7b990e38a1e41aaaedc00",
    "751ce4b25fd592d4e0f86a8fb008f16c5705e59a73663d2a39405fc3a3030d39",
    "4585a571715524fa51c7212e37013ea45634dfa2bae08894fa3cc616fc694add",
    "2b60b4c1a1cd70e2f4ade33310be82c61d8a4503ae8d55074fc752bbc9486e11",
    "2ca964ae17fe6f2b7f47f16540a299c0f7b3380f796e7ac8493bfcee7893378a",
    "d33a66b3cae54c120aa43622305add50f90408cd5fe13c352a23842484f0463c",
    "21eb880d14a0ccc39dd9fc3798fbfb2d8e82101187e434e6020a575662c3c7d0",
    "67910a034a8bd148c4dd7ebf77290d683b472f41e0439930e9abafb5f3814687",
    "aee6486c93d6663b5bc80d06084d239fca28441a9d855c772a1aced1308e3298",
    "3626f6d223bdcfed5091b89663ef5361d01c467ee8b1eea504f5a9b79320a9ad",
    "36313b00defa02c2145da13d795d2d4201ed45a044b952084d5638806c8d429b",
    "d539d33f0ac4e88605ec0ced396b039f8743174ddbed63c54b9792542dc729f3",
)
GIT_REPOSITORY_ATTRIBUTES_POLICY = {
    "schema": "s8-v4-x1-phase-b2-r7s1-git-attributes-policy/v1",
    "rule_count": 20,
    "pattern_sha256": list(GIT_ATTRIBUTES_PATTERN_SHA256),
    "attribute_tokens": ["text", "eol=lf"],
    "forbidden_attributes": ["filter", "diff", "merge", "working-tree-encoding"],
    "git_top_level_attributes_absent": True,
    "git_info_attributes_absent": True,
    "system_attributes_disabled": True,
    "child_environment": {"GIT_ATTR_NOSYSTEM": "1"},
    "hash_object_policy": {
        "core_autocrlf": "true",
        "path_argument_required": True,
        "absolute_worktree_path_required": True,
    },
}
CANONICAL_DOCKER_CLIENT_CONFIG_PATH = Path("C:/Users/opop0/.docker/config.json").resolve()
CANONICAL_DOCKER_CLIENT_CONFIG_SHA256 = (
    "7b2ec346b548b5bdf0bcd95923e800fe50ac50f0b2678e874fc18124ac5b22b6"
)
CANONICAL_DOCKER_CLIENT_CONFIG_BYTES = 78
CANONICAL_DOCKER_CONTEXT_METADATA_PATH = Path(
    "C:/Users/opop0/.docker/contexts/meta/"
    "fe9c6bd7a66301f49ca9b6a70b217107cd1284598bfc254700c989b916da791e/meta.json"
).resolve()
CANONICAL_DOCKER_CONTEXT_METADATA_SHA256 = (
    "162ea41b361225a824608cf6c714d7710d69aa3c645bfbbf98104b4fce06cd09"
)
CANONICAL_DOCKER_CONTEXT_METADATA_BYTES = 318
CANONICAL_DOCKER_CONTEXT_TLS_PATH = Path(
    "C:/Users/opop0/.docker/contexts/tls/"
    "fe9c6bd7a66301f49ca9b6a70b217107cd1284598bfc254700c989b916da791e"
).resolve()
DOCKER_CONTEXT_ENDPOINT_IDENTITY = {
    "scheme": "npipe",
    "endpoint_sha256": "30341252ca9aa2b298da11cd8527fdfbf8ab30a2f3b5a3c871188c778b20af30",
    "skip_tls_verify": False,
}
DOCKER_CLIENT_CONFIG_POLICY = {
    "schema": "s8-v4-x1-phase-b2-r7s1-docker-client-config-policy/v1",
    "top_level_keys": ["auths", "credsStore", "currentContext"],
    "auth_entries": 0,
    "credential_store_present": True,
    "credential_store_value_exposed": False,
    "current_context": "desktop-linux",
    "endpoint_identity": dict(DOCKER_CONTEXT_ENDPOINT_IDENTITY),
    "tls_material_directory_absent": True,
    "registry_operations_allowed": False,
    "child_environment": {
        "scrub_prefixes": ["COMPOSE_", "DOCKER_"],
        "scrub_names": [
            "ALL_PROXY",
            "CURL_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
        ],
        "case_insensitive": True,
        "set_variables": {
            "DOCKER_CONFIG": str(CANONICAL_DOCKER_CLIENT_CONFIG_PATH.parent),
            "DOCKER_CONTEXT": "desktop-linux",
            "DOCKER_CLI_HINTS": "false",
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "COMPOSE_ANSI": "never",
            "COMPOSE_PROGRESS": "plain",
        },
    },
    "docker_global_arguments": [
        "--config",
        str(CANONICAL_DOCKER_CLIENT_CONFIG_PATH.parent),
        "--context",
        "desktop-linux",
    ],
    "standalone_compose_context_transport": "child_environment_only",
    "standalone_compose_required_argument_names": ["-p", "-f", "--project-directory"],
}
CANONICAL_KUBERNETES_CLIENT_CONFIG_PATH = Path("C:/Users/opop0/.kube/config").resolve()
CANONICAL_KUBERNETES_CLIENT_CONFIG_SHA256 = (
    "0d9a540954fb7b9b1bf016cffd399022d1d19f2bd0617a0562912611edf9d085"
)
CANONICAL_KUBERNETES_CLIENT_CONFIG_BYTES = 5_692
KUBERNETES_SERVER_IDENTITY = {
    "scheme": "https",
    "host": "kubernetes.docker.internal",
    "port": 6_443,
    "server_sha256": "d963afe1090a97c0b5c0fe1bc6fe3a44637e469418675ed817bf676970ebde84",
}
KUBERNETES_CLIENT_CONFIG_POLICY = {
    "schema": "s8-v4-x1-phase-b2-r7s1-kubernetes-client-config-policy/v1",
    "current_context": "docker-desktop",
    "object_counts": {"contexts": 1, "clusters": 1, "users": 1},
    "context_identity": {
        "name": "docker-desktop",
        "cluster": "docker-desktop",
        "user": "docker-desktop",
    },
    "cluster_identity": {
        "name": "docker-desktop",
        "server_identity": dict(KUBERNETES_SERVER_IDENTITY),
    },
    "user_identity": {"name": "docker-desktop"},
    "forbidden_fields_absent": [
        "exec",
        "auth-provider",
        "proxy-url",
        "token",
        "username",
        "password",
    ],
    "multiple_config_merge_forbidden": True,
    "embedded_material_presence": {
        "certificate_authority_data": True,
        "client_certificate_data": True,
        "client_key_data": True,
        "serialized_values": False,
    },
    "child_environment": {
        "scrub_prefixes": ["KUBE", "SSH_"],
        "scrub_names": [
            "ALL_PROXY",
            "CURL_CA_BUNDLE",
            "GIT_ASKPASS",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
            "SSH_ASKPASS",
        ],
        "scrub_suffixes": ["ASKPASS"],
        "case_insensitive": True,
        "set_variables": {"KUBECONFIG": str(CANONICAL_KUBERNETES_CLIENT_CONFIG_PATH)},
    },
    "required_global_arguments": [
        "--kubeconfig",
        str(CANONICAL_KUBERNETES_CLIENT_CONFIG_PATH),
        "--context",
        "docker-desktop",
        "--request-timeout=8s",
    ],
}
CANONICAL_STAGING_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
    "staging/s8-v4/x1-clock-phase-b2-r7s1-restore"
).resolve()
CANONICAL_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
    "private/s8-v4/x1-clock-phase-b2-r7s1-restore"
).resolve()
PARENT_CHECKPOINT_ROLES = (
    "r5_failure_seal",
    "r5_failure_index",
    "r6_compose_rca",
    "r6_failure_seal_amendment",
    "r6_final_index",
    "post_manual_on_readback",
    "post_manual_on_index",
    "r7_failure_seal",
    "r7_failure_index",
    "r7_post_seal_residual_amendment",
)
PARENT_CHECKPOINT_KINDS = {role: role for role in PARENT_CHECKPOINT_ROLES}
PARENT_CHECKPOINT_SCHEMAS = {
    "r5_failure_seal": "s8-v4-x1-phase-b2-r5-failure-seal/v1",
    "r5_failure_index": "s8-v4-x1-phase-b2-r5-failure-evidence-index/v1",
    "r6_compose_rca": "r6-compose-recovery-rca/v1",
    "r6_failure_seal_amendment": "r6-compose-recovery-failure-seal-amendment/v1",
    "r6_final_index": "r6-compose-recovery-private-failure-index-amendment/v1",
    "post_manual_on_readback": "r6-compose-recovery-final-runtime-readback-amendment/v1",
    "post_manual_on_index": "r6-compose-recovery-private-failure-index-amendment-2/v1",
    "r7_failure_seal": "s8-v4-x1-phase-b2-r7-remediation-failure-seal/v1",
    "r7_failure_index": "s8-v4-x1-phase-b2-r7-remediation-failure-index/v1",
    "r7_post_seal_residual_amendment": ("s8-v4-x1-phase-b2-r7-post-seal-residual-amendment/v1"),
}

RESTORE_LIFECYCLE_COUNTS = {
    "docker_off_probe": 0,
    "compose_stop": 0,
    "desktop_stop": 0,
    "wsl_shutdown": 0,
    "desktop_start": 0,
    "compose_start": 0,
}
RESTORE_COLLECTOR_COUNTS = {"windows_fresh_collector": 0, "wsl_fresh_collector": 0}
LAUNCHER_COUNTS = {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0}
DOWNSTREAM_COUNTS = {
    "full_stack_3180": 0,
    "q0": 0,
    "calibration_54": 0,
    "matrix_78": 0,
    "integrated_v4": 0,
    "etw": 0,
}

LONG_LIVED_SERVICES = (
    "airflow-postgres",
    "airflow-scheduler",
    "airflow-webserver",
    "api",
    "control-panel",
    "control-plane-postgres",
    "grafana",
    "minio",
    "mlflow",
    "otel-collector",
    "postgres",
    "prometheus",
    "task-queue-worker",
)
ONE_SHOT_SERVICES = ("airflow-init", "minio-create-buckets")
PROMETHEUS_JOBS = (
    "evm-api",
    "evm-b0-production",
    "evm-otel-collector",
    "evm-task-queue-worker",
    "prometheus",
)
EXPECTED_API_BASE_URL = "http://127.0.0.1:8000"
EXPECTED_PROMETHEUS_TARGETS_URL = "http://127.0.0.1:9090/api/v1/targets"
EXPECTED_B0 = {
    "uid": "cfdab424-dcc5-4d5f-a46f-ae7530441ef4",
    "uid_basis": "tracked canonical status evidence predating r4 and immutable deployment identity",
    "image": (
        "enterprise-vision-mlops-efficientnet-serving@"
        "sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a"
    ),
    "ready_url": "http://127.0.0.1:30800/ready",
    "predict_url": "http://127.0.0.1:30800/predict",
    "sample_image_uri": (
        "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
    ),
}
EXPECTED_GPU_LEASE_PATH = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json"
)
EXPECTED_X1_RESIDUE_PATHS = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
    "prometheus-targets/s8-v4-x1-triton.json",
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
    "prometheus-targets/s8-v4-x1-api.json",
)
DOCKER_CONTAINER_EXECUTION_SCOPE = {
    "schema": "s8-v4-x1-phase-b2-r7s1-docker-container-exec-tcb/v1",
    "windows_job_accounting": "docker_cli_and_windows_descendants_only",
    "docker_daemon_container_exec_tcb": True,
    "linux_container_descendants_job_accounted": False,
    "command_policy": "exact_read_only_psql_select_allowlist_no_psqlrc",
    "timeout_or_residual_followup_allowed": False,
}
PROCESS_CONTAINMENT_CONTRACT: dict[str, Any] = {
    **R7_PROCESS_CONTAINMENT_CONTRACT,
    "scope_boundaries": {
        "windows": {
            "scope": "windows_job_object",
            "accounting": "windows_root_child_grandchild_reparent_only",
            "wsl_linux_descendants_job_accounted": False,
            "container_linux_descendants_job_accounted": False,
        },
        "wsl": {
            "scope": "wsl_uuid_process_group",
            "windows_job_accounting": "wsl_launcher_only",
            "linux_descendants_job_accounted": False,
            "post_scan_required": True,
        },
        "docker_container_exec": dict(DOCKER_CONTAINER_EXECUTION_SCOPE),
    },
}
DATABASE_INSTANCES = {
    "control_plane": {
        "container_name": "evm-control-plane-postgres",
        "user": "evm_control_plane",
        "database": "evm_control_plane",
    },
    "mlflow": {"container_name": "evm-postgres", "user": "mlflow", "database": "mlflow"},
    "airflow": {
        "container_name": "evm-airflow-postgres",
        "user": "airflow",
        "database": "airflow",
    },
}
MLFLOW_MIGRATION_HEAD = "0584bdc529eb"
AIRFLOW_MIGRATION_HEAD = "5f2621c13b39"

JOB_SCOPE_CONTRACT = {
    "canonical_active_jobs": {
        "sources": ["kubernetes_job_status_active", "manifest_active_job_file_markers"],
        "required_count": 0,
    },
    "historical_observations": {
        "sources": [
            "control_plane_task_entity_statuses",
            "mlflow_running_rows",
            "kubernetes_terminal_failed_objects",
        ],
        "separate_from_canonical_active_jobs": True,
        "unknown_or_unproven_blocks_restore": True,
        "deletion_required": False,
    },
}
HISTORICAL_CLASSIFICATION_SOURCES = (
    "control_plane_task_entity_statuses",
    "mlflow_running_rows",
    "kubernetes_terminal_failed_objects",
)
HISTORICAL_QUERY_TEXTS = {
    "control_plane_task_entity_statuses": (
        "SELECT entity_id,state,"
        "to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),"
        "to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') "
        "FROM evm_control_plane.entities WHERE entity_kind='task_assignment' "
        "AND state IN ('queued','pending_confirmation','running') "
        "ORDER BY entity_id;"
    ),
    "mlflow_running_rows": (
        "SELECT run_uuid,status,lifecycle_stage,COALESCE(start_time::text,''),"
        "COALESCE(end_time::text,'') FROM runs WHERE status='RUNNING' ORDER BY run_uuid;"
    ),
    "kubernetes_terminal_failed_objects": (
        "kubectl get pods -A --field-selector=status.phase=Failed -o json"
    ),
}
HISTORICAL_QUERY_SHA256 = {
    source: hashlib.sha256(query.encode("utf-8")).hexdigest()
    for source, query in HISTORICAL_QUERY_TEXTS.items()
}
HISTORICAL_DECISION_AUTHORITY = "phase-b2-r7s1-independent-review"
EXTERNAL_DECISION_AUTHORITY = "phase-b2-r7s1-independent-terminal-fencing-review"
SNAPSHOT_SCHEMA = "s8-v4-x1-phase-b2-pre-r8-historical-snapshot/v1"
LINK_SCAN_SCHEMA = "s8-v4-x1-phase-b2-pre-r8-target-link-scan/v1"
TERMINAL_FENCING_DECISION_SCHEMA = "s8-v4-x1-phase-b2-r7s1-terminal-fencing-decision/v1"
SNAPSHOT_COMMAND_NAMES = (
    "control_plane_history",
    "control_plane_execution_links",
    "mlflow_activity",
    "queue_claims",
    "kubernetes_failed_pods",
    "kubernetes_jobs",
    "compose_project_containers",
    "windows_global_residuals",
    "wsl_global_residuals",
)
LINK_SCAN_COMMAND_NAMES = (
    "control_plane_run_links",
    "airflow_run_links",
    "docker_run_links",
    "kubernetes_run_links",
    "windows_run_links",
    "wsl_run_links",
)
SNAPSHOT_QUERY_SHA256 = {
    "control_plane_execution_links": (
        "504b93f0e2bc15b02aa36c902bc9b801ecedd11a66ba6df3f974824ad891f017"
    ),
    "control_plane_history": ("b9e7241c053be52a0f4ad6593a28d281006d603c87442dd97d39c005b50fab00"),
    "mlflow_activity": "5c9ee249d820484096aecc9580bc535dfb5c9c1753aac6588b3b7cd5149f5d24",
    "queue_claims": "7a3b76e15a5aeb100463ac08947e19a102e2e5370ffac51a0d1c6e32dd830c59",
}
LINK_SCAN_QUERY_SHA256 = {
    "airflow_run_links": "6be7f44465a80065d2f353cf751cf15ff45805704d81a6326b7c9da5e0894ee5",
    "control_plane_run_links": ("f0e5f09ef70300daacdd1f71c83916470798ed0300a2015df35b42eb35bad360"),
    "kubernetes_command": "484c862112f56fd18fc582894f1f9c76a812b7e9efe2a2d22eb5396c4a694541",
}
SNAPSHOT_REPOSITORY = r"C:\Users\mlops\EnterpriseMLOps_Project\enterprise-vision-mlops"
OBSERVATION_SOURCE_REVISION = "b9140adce0c9928a20c6c35ac29d42df7ac76d8c"
FAILED_POD_IDENTITY_FIELDS = (
    "uid",
    "namespace",
    "name",
    "reason",
    "reason_source",
    "owner_uid",
    "owner_kind",
    "owner_name",
    "owner_controller",
)
EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES = (
    (
        "6fe434dd-f19f-4566-bda2-a7ce0e481e76",
        "evm-production",
        "evm-b0-production-5bddd6f579-4hcrt",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "17401b9c-2c5f-4d84-a487-ca60036a6dea",
        "evm-production",
        "evm-b0-production-5bddd6f579-5dcw9",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "ccec0871-e8f5-4d27-bfab-0ffa41a94575",
        "evm-production",
        "evm-b0-production-5bddd6f579-7tqtv",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "fe5512f3-a45b-4a3d-a343-5feffaebd0b5",
        "evm-production",
        "evm-b0-production-5bddd6f579-88s2m",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "67960c70-6be4-4dd8-b2fd-fbf2636c5c85",
        "evm-production",
        "evm-b0-production-5bddd6f579-9hlfb",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "d316c712-cf82-4691-a5c1-938b947249ea",
        "evm-production",
        "evm-b0-production-5bddd6f579-dlmrg",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "68bf727e-8370-475b-8e59-d04917375876",
        "evm-production",
        "evm-b0-production-5bddd6f579-m5ww6",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "aad37e15-083a-4b33-9173-5c2fd307ddf8",
        "evm-production",
        "evm-b0-production-5bddd6f579-nf928",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "1af65ae6-b3fb-46d1-a4a6-badda566c5ee",
        "evm-production",
        "evm-b0-production-5bddd6f579-pl7n5",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "843b9acd-3b72-4e93-9809-34a1d6904dbe",
        "evm-production",
        "evm-b0-production-5bddd6f579-vz5mq",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "5b156cff-5510-4540-a6c7-7d5b46cfd8f6",
        "evm-production",
        "evm-b0-production-5bddd6f579-z6pxr",
        "UnexpectedAdmissionError",
        "pod.status.reason",
        "bc5f4660-cb76-4571-acdf-688d5b536893",
        "ReplicaSet",
        "evm-b0-production-5bddd6f579",
        True,
    ),
    (
        "8a4613ed-e3b2-4442-9c4e-f1ca466830c2",
        "evm-training",
        "evm-lifecycle-train-426f8b4a1440-pw9sq",
        "BackoffLimitExceeded",
        "owner_job.status.conditions[type=Failed].reason",
        "75ea1250-df6c-4dd6-acd4-2666016179a4",
        "Job",
        "evm-lifecycle-train-426f8b4a1440",
        True,
    ),
    (
        "6b830f62-5829-4978-bddd-e779026cfc78",
        "evm-training",
        "evm-lifecycle-train-87f699682486-w2bh5",
        "BackoffLimitExceeded",
        "owner_job.status.conditions[type=Failed].reason",
        "84453597-8a0c-4d6e-8559-8c0000b93aa2",
        "Job",
        "evm-lifecycle-train-87f699682486",
        True,
    ),
    (
        "73c95f56-d2c1-4349-a3c1-58f3ea4b6c6a",
        "evm-training",
        "evm-lifecycle-train-9144c8ab3492-swj8b",
        "BackoffLimitExceeded",
        "owner_job.status.conditions[type=Failed].reason",
        "e3c30272-a676-427c-81b3-913299300741",
        "Job",
        "evm-lifecycle-train-9144c8ab3492",
        True,
    ),
)
SNAPSHOT_ARGV_SHA256 = {
    "control_plane_history": "aadaf16b141e4c48cd2dfc4ba48ed079dfe1c3d84530302da48804f6cc7ce0b3",
    "control_plane_execution_links": (
        "c6f620045179654d61e65ceaef1db4fbe00474735eb78939831af8ec4d41201a"
    ),
    "mlflow_activity": "4b2967773fa7f25049cc8fa09822cfedacdcc7d85197a24651a3cf6964d09089",
    "queue_claims": "dbf93d7a5bd972f7a24f71e542782b2d4905b0c64e32429b15a387e83f0d96fc",
    "kubernetes_failed_pods": ("886b6396d0b720c069597fcd44dff4b00cae35b9985939bf58fc6f555ff3ca9f"),
    "kubernetes_jobs": "aa858147fddd2d6caf84adf12ecb78ccaf698339404efca96de7404dc4cbf32c",
    "compose_project_containers": (
        "929f00786e376b040508d361f99103f3f04b67be94a5470ec8fea6a861c526fc"
    ),
    "windows_global_residuals": (
        "ae303384628944b7a2375a55e89c1a0a66022c3d6f1fa7214db6c936de87258b"
    ),
    "wsl_global_residuals": ("8cfe878aa24f4bf44ca500561227d3432133a9c59335ba95925caaa15c0e5376"),
}
LINK_SCAN_ARGV_SHA256 = {
    "control_plane_run_links": ("f00b8e1955da9405297c9cbaec4d5eea4dc7381b81f87640808857382490ec2e"),
    "airflow_run_links": "678b10d8cf565b461b82f6e4ab16146c1c57f5e7b077c8390d73144db1587fb8",
    "docker_run_links": "25bd5d8ad3f320dc7dd7c736e3ba5016d405deef8a9b1c54b476b246fb4b3efc",
    "kubernetes_run_links": ("cd08689d610c2a243877a58d65994127e1e1e24e620b7f183afe282e467aab67"),
    "windows_run_links": "5df9a335cccb0e21e3fc3a9f6003efdf0c8eb297cd70e3f16f4bbb9f6fed3fcc",
    "wsl_run_links": "73e4343caa78194b28310e4026c5e05b9272e325b7e4c8ef743499134ed38d79",
}

FULL_SHA1 = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
MIGRATION_VERSION = re.compile(r"^[0-9]{3}_[a-z0-9_]+$")


class PhaseB2R7Error(RuntimeError):
    """Base exception for an r7 fail-closed decision."""


class R7ContractError(PhaseB2R7Error):
    """Raised when executable or observed state differs from the r7 contract."""


class R7EvidenceExistsError(PhaseB2R7Error):
    """Raised when create-exclusive evidence would overwrite a path."""


class R7SuccessInvariantError(PhaseB2R7Error):
    """Raised when restore-only evidence is requested before all gates pass."""


class R7EmergencySealError(PhaseB2R7Error):
    """Raised when the one-shot upper emergency seal cannot be published."""


@dataclass(frozen=True)
class TimeoutContract:
    """Exact nested-command, wrapper, restore, residual, and drain budgets."""

    kubectl_timeout_seconds: float = 8.0
    wrapper_timeout_seconds: float = 15.0
    restore_deadline_seconds: float = 600.0
    residual_repoll_seconds: float = 120.0
    stream_drain_seconds: float = 5.0

    FIELD_NAMES = (
        "kubectl_timeout_seconds",
        "wrapper_timeout_seconds",
        "restore_deadline_seconds",
        "residual_repoll_seconds",
        "stream_drain_seconds",
    )

    def validate(self) -> "TimeoutContract":
        for name in self.FIELD_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise R7ContractError(f"{name}_numeric_required")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise R7ContractError(f"{name}_finite_positive_required")
        if not (
            self.kubectl_timeout_seconds
            < self.wrapper_timeout_seconds
            < self.restore_deadline_seconds
        ):
            raise R7ContractError("timeout_order_requires_kubectl_lt_wrapper_lt_restore_deadline")
        if self.residual_repoll_seconds != 120:
            raise R7ContractError("residual_repoll_must_equal_120_seconds")
        if self.stream_drain_seconds >= self.wrapper_timeout_seconds:
            raise R7ContractError("stream_drain_must_be_less_than_wrapper")
        return self

    def to_dict(self) -> dict[str, float]:
        self.validate()
        return {name: float(getattr(self, name)) for name in self.FIELD_NAMES}

    @classmethod
    def from_mapping(cls, value: Any) -> "TimeoutContract":
        source = _mapping(value, "timeout_contract")
        if set(source) != set(cls.FIELD_NAMES):
            raise R7ContractError("timeout_contract_fields_mismatch")
        if any(isinstance(source[name], bool) for name in cls.FIELD_NAMES):
            raise R7ContractError("timeout_contract_boolean_forbidden")
        try:
            return cls(**{name: float(source[name]) for name in cls.FIELD_NAMES}).validate()
        except (TypeError, ValueError) as exc:
            raise R7ContractError("timeout_contract_numeric_required") from exc


@dataclass
class RestoreDeadline:
    total_seconds: float
    clock: Callable[[], float] = time.monotonic
    started_monotonic: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.total_seconds)) or float(self.total_seconds) <= 0:
            raise R7ContractError("restore_deadline_finite_positive_required")
        if self.started_monotonic is None:
            self.started_monotonic = float(self.clock())

    @property
    def remaining_seconds(self) -> float:
        assert self.started_monotonic is not None
        return max(
            0.0,
            self.started_monotonic + float(self.total_seconds) - float(self.clock()),
        )

    def can_launch(self, required_seconds: float) -> bool:
        if not math.isfinite(float(required_seconds)) or float(required_seconds) <= 0:
            raise R7ContractError("probe_required_seconds_finite_positive_required")
        return self.remaining_seconds >= float(required_seconds)

    def assert_can_launch(self, required_seconds: float) -> None:
        if not self.can_launch(required_seconds):
            raise R7ContractError(
                "restore_budget_prevents_new_probe:"
                f"remaining={self.remaining_seconds:.6f}:required={float(required_seconds):.6f}"
            )


@dataclass(frozen=True)
class RestoreCheckpoint:
    source: str
    historical_call_counts: Mapping[str, int]
    previous_attempt_failed: bool = True

    def permits(self, operation: str) -> bool:
        blocked = {
            *RESTORE_LIFECYCLE_COUNTS,
            *RESTORE_COLLECTOR_COUNTS,
            *DOWNSTREAM_COUNTS,
        }
        return operation not in blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "historical_call_counts": dict(self.historical_call_counts),
            "previous_attempt_failed": self.previous_attempt_failed,
            "restore_only_blocked_calls": list(RESTORE_LIFECYCLE_COUNTS),
        }


class RestoreStage(str, Enum):
    DOCKER_ENGINE = "docker_engine"
    COMPOSE = "compose"
    KUBERNETES_API = "kubernetes_api"
    NODE_DEVICE_PLUGIN_GPU = "node_device_plugin_gpu"
    B0_IDENTITY_CUDA = "b0_exact_identity_actual_cuda"
    PROMETHEUS = "prometheus"
    API_RELEASE_IDENTITY = "api_release_identity"
    QUEUE_JOBS_LEASE_RESIDUE = "queue_jobs_lease_residue"


RESTORE_STAGE_ORDER = tuple(RestoreStage)
R7_REQUIRED_INVARIANTS = (
    "docker_engine",
    "compose_healthy",
    "kubernetes_livez",
    "kubernetes_readyz",
    "node_ready_1_of_1",
    "device_plugin_ready_1_of_1",
    "gpu_capacity_1",
    "gpu_allocatable_1",
    "b0_exact_uid",
    "b0_exact_image",
    "b0_replica_1_of_1",
    "b0_actual_cuda",
    "prometheus_5_of_5",
    "api_health_200",
    "api_ready_200",
    "api_revision_exact",
    "api_runtime_revision_matches",
    "queue_active_zero",
    "queue_leased_zero",
    "queue_outcome_unknown_zero",
    "active_jobs_zero",
    "active_claims_zero",
    "gpu_lease_zero",
    "x1_residue_zero",
    "compose_exact_13_running",
    "compose_healthchecks_healthy",
    "compose_container_identity_stable",
    "compose_restart_delta_zero",
    "compose_stability_duration_met",
    "compose_one_shots_classified",
    "postgres_3_of_3_connected",
    "postgres_3_of_3_not_in_recovery",
    "control_plane_migrations_exact",
    "mlflow_migration_head_exact",
    "airflow_migration_head_exact",
    "api_container_image_exact",
    "worker_container_image_exact",
    "api_image_revision_exact",
    "api_image_attestation_exact",
    "canonical_active_scope_exact",
    "historical_control_plane_tasks_classified",
    "historical_mlflow_running_classified",
    "historical_failed_pods_classified",
    "windows_global_residual_zero",
    "wsl_global_residual_zero",
)


@dataclass(frozen=True)
class ProbeResult:
    passed: bool
    retryable: bool = False
    last_error: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    residual_pids: tuple[int, ...] = ()
    manual_intervention_required: bool = False
    invariants: Mapping[str, bool] = field(default_factory=dict)

    @classmethod
    def normalize(cls, raw: Any) -> "ProbeResult":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, bool):
            return cls(passed=raw, last_error=None if raw else "probe_false")
        if not isinstance(raw, Mapping):
            raise TypeError(f"probe_result_mapping_required:{type(raw).__name__}")
        invariants_raw = raw.get("invariants", {})
        if not isinstance(invariants_raw, Mapping):
            raise TypeError("probe_invariants_mapping_required")
        invariants = {str(name): value is True for name, value in invariants_raw.items()}
        passed = raw.get("passed", raw.get("ok", False)) is True and all(invariants.values())
        error = raw.get("last_error", raw.get("error"))
        return cls(
            passed=passed,
            # Preserve the observation for evidence, but the r7 harness never
            # interprets it as authority to launch a second probe.
            retryable=raw.get("retryable") is True,
            last_error=None if error is None else str(error),
            details={
                str(key): value
                for key, value in raw.items()
                if key
                not in {
                    "passed",
                    "ok",
                    "retryable",
                    "last_error",
                    "error",
                    "residual_pids",
                    "manual_intervention_required",
                    "invariants",
                }
            },
            residual_pids=tuple(sorted({int(pid) for pid in raw.get("residual_pids", ())})),
            manual_intervention_required=raw.get("manual_intervention_required") is True,
            invariants=invariants,
        )


@dataclass(frozen=True)
class RestoreReport:
    mode: str
    started_at: str
    ended_at: str
    duration_seconds: float
    expected_revision: str | None
    passed: bool
    manual_intervention_required: bool
    deadline_exceeded: bool
    last_error: str | None
    stages: list[Any]
    call_counts: Mapping[str, int]
    residual_pids: tuple[int, ...]
    checkpoint: Mapping[str, Any]
    success_invariants: Mapping[str, bool]
    required_invariants: tuple[str, ...] = ()
    decision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        stage_values = [
            stage.to_dict() if hasattr(stage, "to_dict") else dict(stage) for stage in self.stages
        ]
        return {
            "schema": "s8-v4-x1-phase-b2-r7s1-restore-report/v1",
            "mode": self.mode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "expected_revision": self.expected_revision,
            "passed": self.passed,
            "overall_pass": self.passed,
            "manual_intervention_required": self.manual_intervention_required,
            "deadline_exceeded": self.deadline_exceeded,
            "last_error": self.last_error,
            "stages": stage_values,
            "call_counts": dict(self.call_counts),
            "residual_pids": list(self.residual_pids),
            "checkpoint": dict(self.checkpoint),
            "success_invariants": dict(self.success_invariants),
            "required_invariants": list(self.required_invariants),
            "decision": self.decision,
        }


class ReconcileRestoreHarness:
    """Single-attempt, read-only r7 restore reconciliation state machine."""

    def __init__(
        self,
        *,
        contract: TimeoutContract | None = None,
        probes: Mapping[str | RestoreStage, Callable[[RestoreDeadline], Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str] | None = None,
        expected_revision: str | None = None,
        required_invariants: Sequence[str] | None = None,
        max_probe_attempts: int = 1,
    ) -> None:
        self.contract = (contract or TimeoutContract()).validate()
        self.probes = {
            key.value if isinstance(key, RestoreStage) else str(key): probe
            for key, probe in (probes or {}).items()
        }
        self.clock = clock
        self.utc_clock = utc_clock or utc_now
        self.expected_revision = expected_revision
        normalized_invariants = (
            R7_REQUIRED_INVARIANTS
            if required_invariants is None
            else tuple(str(item) for item in required_invariants)
        )
        if normalized_invariants != R7_REQUIRED_INVARIANTS:
            raise R7ContractError("r7_required_invariant_set_mismatch")
        self.required_invariants = R7_REQUIRED_INVARIANTS
        if max_probe_attempts != 1 or isinstance(max_probe_attempts, bool):
            raise R7ContractError("r7_probe_max_attempts_must_equal_1")
        self.max_probe_attempts = 1

    def run_restore_only(self, checkpoint: RestoreCheckpoint) -> RestoreReport:
        if not isinstance(checkpoint, RestoreCheckpoint):
            raise TypeError("restore_checkpoint_required")
        started_at = self.utc_clock()
        started = float(self.clock())
        deadline = RestoreDeadline(
            self.contract.restore_deadline_seconds,
            clock=self.clock,
            started_monotonic=started,
        )
        stages: list[dict[str, Any]] = []
        invariants = {name: False for name in self.required_invariants}
        invariants.update({stage.value: False for stage in RESTORE_STAGE_ORDER})
        residual_pids: set[int] = set()
        last_error: str | None = None
        unsafe_latch = False

        for stage in RESTORE_STAGE_ORDER:
            stage_started_at = self.utc_clock()
            stage_started = float(self.clock())
            probe = self.probes.get(stage.value)
            attempts = 0
            if probe is None:
                result = ProbeResult(passed=False, last_error=f"probe_missing:{stage.value}")
            elif deadline.remaining_seconds <= 0:
                result = ProbeResult(
                    passed=False,
                    last_error=f"restore_deadline_exhausted_before_probe:{stage.value}",
                    manual_intervention_required=True,
                )
            else:
                try:
                    attempts = 1
                    result = ProbeResult.normalize(probe(deadline))
                except Exception as exc:
                    result = ProbeResult(
                        passed=False,
                        last_error=f"probe_exception:{stage.value}:{type(exc).__name__}:{exc}",
                        manual_intervention_required=True,
                    )
            stage_ended = float(self.clock())
            invariants[stage.value] = result.passed
            invariants.update(result.invariants)
            residual_pids.update(result.residual_pids)
            if not result.passed and last_error is None:
                last_error = result.last_error or f"restore_stage_failed:{stage.value}"
            stages.append(
                {
                    "stage": stage.value,
                    "started_at": stage_started_at,
                    "ended_at": self.utc_clock(),
                    "duration_seconds": max(0.0, stage_ended - stage_started),
                    "attempts": attempts,
                    "max_attempts": 1,
                    "passed": result.passed,
                    "retryable_ignored": result.retryable,
                    "last_error": result.last_error,
                    "manual_intervention_required": result.manual_intervention_required,
                    "residual_pids": list(result.residual_pids),
                    "invariants": dict(result.invariants),
                    "details": dict(result.details),
                    "deadline_remaining_seconds": deadline.remaining_seconds,
                }
            )
            unsafe_latch = bool(
                result.manual_intervention_required
                or result.residual_pids
                or deadline.remaining_seconds <= 0
            )
            if unsafe_latch:
                break

        required_ok = all(invariants.get(name) is True for name in self.required_invariants)
        all_stages = len(stages) == len(RESTORE_STAGE_ORDER) and all(
            bool(stage["passed"]) for stage in stages
        )
        passed = bool(
            all_stages
            and required_ok
            and not unsafe_latch
            and not residual_pids
            and last_error is None
        )
        if not passed and last_error is None:
            last_error = "restore_invariants_incomplete"
        ended = float(self.clock())
        return RestoreReport(
            mode="restore-only",
            started_at=started_at,
            ended_at=self.utc_clock(),
            duration_seconds=max(0.0, ended - started),
            expected_revision=self.expected_revision,
            passed=passed,
            manual_intervention_required=not passed,
            deadline_exceeded=deadline.remaining_seconds <= 0,
            last_error=last_error,
            stages=stages,
            call_counts=dict(RESTORE_LIFECYCLE_COUNTS),
            residual_pids=tuple(sorted(residual_pids)),
            checkpoint=checkpoint.to_dict(),
            success_invariants=invariants,
            required_invariants=self.required_invariants,
            decision="restore_only_pass" if passed else "manual_intervention_required",
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_snapshot(path: Path) -> tuple[Any, str]:
    """Parse and hash one immutable byte snapshot from ``path``.

    Reading and hashing through separate opens would permit a path replacement
    between the SHA check and JSON parsing.  Callers bind both decisions to the
    same bytes instead.
    """

    raw = Path(path).read_bytes()
    measured_sha256 = hashlib.sha256(raw).hexdigest()
    return json.loads(raw.decode("utf-8-sig")), measured_sha256


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R7ContractError(f"{label}_mapping_required")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise R7ContractError(f"{label}_sequence_required")
    return value


def _nonempty(value: Any, label: str) -> str:
    normalized = str(value)
    if not normalized.strip():
        raise R7ContractError(f"{label}_nonempty_required")
    return normalized


def _full_sha1(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if FULL_SHA1.fullmatch(normalized) is None:
        raise R7ContractError(f"{label}_full_sha1_required")
    return normalized


def _full_sha256(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if FULL_SHA256.fullmatch(normalized) is None:
        raise R7ContractError(f"{label}_full_sha256_required")
    return normalized


def _sha256_id(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if SHA256_ID.fullmatch(normalized) is None:
        raise R7ContractError(f"{label}_sha256_id_required")
    return normalized


def _uuid(value: Any, label: str) -> str:
    normalized = str(value).lower()
    try:
        parsed = uuid.UUID(normalized)
    except (ValueError, AttributeError) as exc:
        raise R7ContractError(f"{label}_uuid_required") from exc
    if str(parsed) != normalized:
        raise R7ContractError(f"{label}_canonical_uuid_required")
    return normalized


def _exact_counts(value: Any, expected: Mapping[str, int], label: str) -> dict[str, int]:
    source = _mapping(value, label)
    try:
        actual = {str(key): int(raw) for key, raw in source.items()}
    except (TypeError, ValueError) as exc:
        raise R7ContractError(f"{label}_integer_counts_required") from exc
    if any(isinstance(raw, bool) for raw in source.values()) or actual != dict(expected):
        raise R7ContractError(f"{label}_exact_counts_required:{actual}")
    return actual


def _resolved_outside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return True
    return False


def git_worktree_blob_oid(repository_root: Path, path: Path) -> str:
    """Compute a Git blob OID in-process without spawning an uncontained child.

    The pinned launcher/runner separately proves the canonical HEAD/tree and clean
    tracked state with the pinned Git executable under Job Object containment.
    This function only binds those pins to the exact bytes read by this process.
    """

    git_root = Path(repository_root).resolve()
    candidate = Path(path).resolve()
    try:
        relative = candidate.relative_to(git_root).as_posix()
    except ValueError as exc:
        raise R7ContractError("runtime_path_outside_git_repository") from exc
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise R7ContractError(f"runtime_blob_read_failed:{relative}") from exc
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class LifecycleTimeoutContract:
    compose_internal_seconds: float = 120.0
    compose_wrapper_seconds: float = 150.0
    desktop_internal_seconds: float = 300.0
    desktop_wrapper_seconds: float = 330.0
    sampler_internal_seconds: float = 180.0
    sampler_wrapper_seconds: float = 210.0
    attempt_deadline_seconds: float = 1200.0

    FIELD_NAMES = (
        "compose_internal_seconds",
        "compose_wrapper_seconds",
        "desktop_internal_seconds",
        "desktop_wrapper_seconds",
        "sampler_internal_seconds",
        "sampler_wrapper_seconds",
        "attempt_deadline_seconds",
    )

    def validate(self) -> "LifecycleTimeoutContract":
        for name in self.FIELD_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise R7ContractError(f"{name}_numeric_required")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise R7ContractError(f"{name}_finite_positive_required")
        if not self.compose_internal_seconds < self.compose_wrapper_seconds:
            raise R7ContractError("compose_internal_must_be_less_than_wrapper")
        if not self.desktop_internal_seconds < self.desktop_wrapper_seconds:
            raise R7ContractError("desktop_internal_must_be_less_than_wrapper")
        if not self.sampler_internal_seconds < self.sampler_wrapper_seconds:
            raise R7ContractError("sampler_internal_must_be_less_than_wrapper")
        if (
            max(
                self.compose_wrapper_seconds,
                self.desktop_wrapper_seconds,
                self.sampler_wrapper_seconds,
            )
            >= self.attempt_deadline_seconds
        ):
            raise R7ContractError("lifecycle_wrapper_must_be_less_than_attempt_deadline")
        return self

    def to_dict(self) -> dict[str, float]:
        self.validate()
        return {name: float(getattr(self, name)) for name in self.FIELD_NAMES}

    @classmethod
    def from_mapping(cls, value: Any) -> "LifecycleTimeoutContract":
        source = _mapping(value, "lifecycle_timeout_contract")
        if set(source) != set(cls.FIELD_NAMES):
            raise R7ContractError("lifecycle_timeout_contract_fields_mismatch")
        if any(isinstance(source[name], bool) for name in cls.FIELD_NAMES):
            raise R7ContractError("lifecycle_timeout_contract_boolean_forbidden")
        try:
            return cls(**{name: float(source[name]) for name in cls.FIELD_NAMES}).validate()
        except (TypeError, ValueError) as exc:
            raise R7ContractError("lifecycle_timeout_contract_numeric_required") from exc


def validate_runtime_pins(
    manifest: Mapping[str, Any], repository_root: Path
) -> dict[str, dict[str, Any]]:
    runtime = _mapping(manifest.get("runtime"), "runtime")
    if set(runtime) != set(RUNTIME_COMPONENTS):
        raise R7ContractError("runtime_component_role_set_mismatch")
    root = Path(repository_root).resolve()
    measured: dict[str, dict[str, Any]] = {}
    paths: set[Path] = set()
    for name in RUNTIME_COMPONENTS:
        component = _mapping(runtime[name], f"runtime_{name}")
        if set(component) != {
            "path",
            "sha256",
            "worktree_blob_oid",
            "head_blob_oid",
            "bytes",
        }:
            raise R7ContractError(f"runtime_{name}_fields_mismatch")
        path = Path(_nonempty(component["path"], f"runtime_{name}_path")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise R7ContractError(f"runtime_{name}_path_outside_repository") from exc
        if path in paths:
            raise R7ContractError("runtime_component_paths_must_be_distinct")
        paths.add(path)
        if not path.is_file():
            raise R7ContractError(f"runtime_{name}_file_missing:{path}")
        expected_sha = _full_sha256(component["sha256"], f"runtime_{name}")
        expected_worktree_blob = _full_sha1(
            component["worktree_blob_oid"], f"runtime_{name}_worktree_blob"
        )
        expected_head_blob = _full_sha1(component["head_blob_oid"], f"runtime_{name}_head_blob")
        if (
            isinstance(component["bytes"], bool)
            or not isinstance(component["bytes"], int)
            or component["bytes"] < 1
        ):
            raise R7ContractError(f"runtime_{name}_positive_bytes_required")
        actual_sha = sha256_file(path)
        actual_blob = git_worktree_blob_oid(root, path)
        actual_bytes = path.stat().st_size
        if actual_sha != expected_sha:
            raise R7ContractError(f"runtime_{name}_sha256_mismatch")
        if actual_blob != expected_worktree_blob:
            raise R7ContractError(f"runtime_{name}_worktree_blob_oid_mismatch")
        if actual_bytes != component["bytes"]:
            raise R7ContractError(f"runtime_{name}_bytes_mismatch")
        measured[name] = {
            "path": str(path),
            "sha256": actual_sha,
            "worktree_blob_oid": actual_blob,
            "head_blob_oid": expected_head_blob,
            "bytes": actual_bytes,
        }
    return measured


def _absolute_normalized_path(value: Any, label: str) -> Path:
    text = _nonempty(value, label)
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts:
        raise R7ContractError(f"{label}_absolute_normalized_path_required")
    resolved = path.resolve()
    if str(resolved).casefold() != text.casefold():
        raise R7ContractError(f"{label}_absolute_normalized_path_required")
    return resolved


def _signature_contract(value: Any, label: str) -> dict[str, str]:
    signature = _mapping(value, label)
    if set(signature) != {"status", "subject", "thumbprint"}:
        raise R7ContractError(f"{label}_fields_mismatch")
    if signature["status"] != "valid":
        raise R7ContractError(f"{label}_valid_signature_required")
    subject = _nonempty(signature["subject"], f"{label}_subject")
    thumbprint = _nonempty(signature["thumbprint"], f"{label}_thumbprint").lower()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", thumbprint) is None:
        raise R7ContractError(f"{label}_thumbprint_invalid")
    return {"status": "valid", "subject": subject, "thumbprint": thumbprint}


def _host_binary_pin(value: Any, label: str, *, verify_file: bool) -> dict[str, Any]:
    pin = _mapping(value, label)
    if set(pin) != {"path", "sha256", "bytes", "version", "signature"}:
        raise R7ContractError(f"{label}_fields_mismatch")
    path = _absolute_normalized_path(pin["path"], f"{label}_path")
    sha = _full_sha256(pin["sha256"], label)
    size = pin["bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise R7ContractError(f"{label}_positive_bytes_required")
    version = _nonempty(pin["version"], f"{label}_version")
    signature = _signature_contract(pin["signature"], f"{label}_signature")
    if verify_file:
        if not path.is_file():
            raise R7ContractError(f"{label}_file_missing:{path}")
        if path.stat().st_size != size or sha256_file(path) != sha:
            raise R7ContractError(f"{label}_measured_identity_mismatch")
    return {
        "path": str(path),
        "sha256": sha,
        "bytes": size,
        "version": version,
        "signature": signature,
    }


def _runtime_binary_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    if set(pin) != {"realpath", "sha256", "bytes", "version"}:
        raise R7ContractError(f"{label}_fields_mismatch")
    realpath = _nonempty(pin["realpath"], f"{label}_realpath")
    if not realpath.startswith("/") or "/../" in realpath:
        raise R7ContractError(f"{label}_absolute_realpath_required")
    size = pin["bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise R7ContractError(f"{label}_positive_bytes_required")
    return {
        "realpath": realpath,
        "sha256": _full_sha256(pin["sha256"], label),
        "bytes": size,
        "version": _nonempty(pin["version"], f"{label}_version"),
    }


def validate_toolchain_contract(value: Any, *, verify_files: bool) -> dict[str, Any]:
    toolchain = _mapping(value, "toolchain")
    required = {
        *HOST_TOOLCHAIN_ROLES,
        "docker_client_config",
        "git_repository_attributes",
        "git_repository_config",
        "kubernetes_client_config",
        "python_distribution",
        "git_distribution",
        "windows_tcb",
        "wsl_runtime",
        "container_psql",
    }
    if set(toolchain) != required:
        raise R7ContractError("toolchain_role_set_mismatch")
    normalized: dict[str, Any] = {
        role: _host_binary_pin(toolchain[role], f"toolchain_{role}", verify_file=verify_files)
        for role in HOST_TOOLCHAIN_ROLES
    }
    if (
        str(Path(normalized["docker_compose"]["path"]).resolve()).casefold()
        != str(DOCKER_COMPOSE_EXECUTABLE).casefold()
    ):
        raise R7ContractError("toolchain_docker_compose_path_mismatch")
    git_config = _mapping(toolchain["git_repository_config"], "toolchain_git_repository_config")
    if set(git_config) != {"path", "sha256", "bytes", "policy", "readback"}:
        raise R7ContractError("toolchain_git_repository_config_fields_mismatch")
    git_config_path = _absolute_normalized_path(
        git_config["path"], "toolchain_git_repository_config_path"
    )
    if str(git_config_path).casefold() != str(CANONICAL_GIT_CONFIG_PATH).casefold():
        raise R7ContractError("toolchain_git_repository_config_path_mismatch")
    git_config_sha256 = _full_sha256(git_config["sha256"], "toolchain_git_repository_config")
    if git_config_sha256 != CANONICAL_GIT_CONFIG_SHA256:
        raise R7ContractError("toolchain_git_repository_config_sha256_mismatch")
    git_config_bytes = git_config["bytes"]
    if (
        isinstance(git_config_bytes, bool)
        or not isinstance(git_config_bytes, int)
        or git_config_bytes != CANONICAL_GIT_CONFIG_BYTES
    ):
        raise R7ContractError("toolchain_git_repository_config_bytes_mismatch")
    git_config_policy = _mapping(git_config["policy"], "toolchain_git_repository_config_policy")
    if dict(git_config_policy) != GIT_REPOSITORY_CONFIG_POLICY:
        raise R7ContractError("toolchain_git_repository_config_policy_mismatch")
    normalized["git_repository_config"] = {
        "path": str(git_config_path),
        "sha256": git_config_sha256,
        "bytes": git_config_bytes,
        "policy": dict(GIT_REPOSITORY_CONFIG_POLICY),
        "readback": _artifact_pin(
            git_config["readback"],
            "toolchain_git_repository_config_readback",
            "s8-v4-x1-phase-b2-r7s1-git-repository-config-readback/v1",
        ),
    }
    if verify_files:
        _reject_reparse_ancestor_chain(git_config_path, "toolchain_git_repository_config")
        if not git_config_path.is_file():
            raise R7ContractError("toolchain_git_repository_config_file_missing")
        if (
            git_config_path.stat().st_size != CANONICAL_GIT_CONFIG_BYTES
            or sha256_file(git_config_path) != CANONICAL_GIT_CONFIG_SHA256
        ):
            raise R7ContractError("toolchain_git_repository_config_measured_identity_mismatch")
        config_worktree_path = git_config_path.with_name("config.worktree")
        _reject_reparse_ancestor_chain(
            config_worktree_path, "toolchain_git_repository_config_worktree"
        )
        if config_worktree_path.exists():
            raise R7ContractError("toolchain_git_repository_config_worktree_must_be_absent")
    git_attributes = _mapping(
        toolchain["git_repository_attributes"], "toolchain_git_repository_attributes"
    )
    if set(git_attributes) != {"path", "sha256", "bytes", "policy", "readback"}:
        raise R7ContractError("toolchain_git_repository_attributes_fields_mismatch")
    git_attributes_path = _absolute_normalized_path(
        git_attributes["path"], "toolchain_git_repository_attributes_path"
    )
    if str(git_attributes_path).casefold() != str(CANONICAL_GIT_ATTRIBUTES_PATH).casefold():
        raise R7ContractError("toolchain_git_repository_attributes_path_mismatch")
    git_attributes_sha256 = _full_sha256(
        git_attributes["sha256"], "toolchain_git_repository_attributes"
    )
    if git_attributes_sha256 != CANONICAL_GIT_ATTRIBUTES_SHA256:
        raise R7ContractError("toolchain_git_repository_attributes_sha256_mismatch")
    git_attributes_bytes = git_attributes["bytes"]
    if (
        isinstance(git_attributes_bytes, bool)
        or not isinstance(git_attributes_bytes, int)
        or git_attributes_bytes != CANONICAL_GIT_ATTRIBUTES_BYTES
    ):
        raise R7ContractError("toolchain_git_repository_attributes_bytes_mismatch")
    git_attributes_policy = _mapping(
        git_attributes["policy"], "toolchain_git_repository_attributes_policy"
    )
    if dict(git_attributes_policy) != GIT_REPOSITORY_ATTRIBUTES_POLICY:
        raise R7ContractError("toolchain_git_repository_attributes_policy_mismatch")
    normalized["git_repository_attributes"] = {
        "path": str(git_attributes_path),
        "sha256": git_attributes_sha256,
        "bytes": git_attributes_bytes,
        "policy": dict(GIT_REPOSITORY_ATTRIBUTES_POLICY),
        "readback": _artifact_pin(
            git_attributes["readback"],
            "toolchain_git_repository_attributes_readback",
            "s8-v4-x1-phase-b2-r7s1-git-repository-attributes-readback/v1",
        ),
    }
    if verify_files:
        _reject_reparse_ancestor_chain(git_attributes_path, "toolchain_git_repository_attributes")
        if (
            not git_attributes_path.is_file()
            or git_attributes_path.stat().st_size != CANONICAL_GIT_ATTRIBUTES_BYTES
            or sha256_file(git_attributes_path) != CANONICAL_GIT_ATTRIBUTES_SHA256
        ):
            raise R7ContractError("toolchain_git_repository_attributes_measured_identity_mismatch")
        for label, absent_path in (
            ("git_top_level_attributes", CANONICAL_GIT_TOP_ATTRIBUTES_PATH),
            ("git_info_attributes", CANONICAL_GIT_INFO_ATTRIBUTES_PATH),
        ):
            _reject_reparse_ancestor_chain(absent_path, f"toolchain_{label}")
            if absent_path.exists():
                raise R7ContractError(f"toolchain_{label}_must_be_absent")
    docker_config = _mapping(toolchain["docker_client_config"], "toolchain_docker_client_config")
    if set(docker_config) != {
        "path",
        "sha256",
        "bytes",
        "context_metadata",
        "policy",
        "readback",
    }:
        raise R7ContractError("toolchain_docker_client_config_fields_mismatch")
    docker_config_path = _absolute_normalized_path(
        docker_config["path"], "toolchain_docker_client_config_path"
    )
    if str(docker_config_path).casefold() != str(CANONICAL_DOCKER_CLIENT_CONFIG_PATH).casefold():
        raise R7ContractError("toolchain_docker_client_config_path_mismatch")
    docker_config_sha256 = _full_sha256(docker_config["sha256"], "toolchain_docker_client_config")
    if docker_config_sha256 != CANONICAL_DOCKER_CLIENT_CONFIG_SHA256:
        raise R7ContractError("toolchain_docker_client_config_sha256_mismatch")
    docker_config_bytes = docker_config["bytes"]
    if (
        isinstance(docker_config_bytes, bool)
        or not isinstance(docker_config_bytes, int)
        or docker_config_bytes != CANONICAL_DOCKER_CLIENT_CONFIG_BYTES
    ):
        raise R7ContractError("toolchain_docker_client_config_bytes_mismatch")
    context_metadata = _mapping(
        docker_config["context_metadata"],
        "toolchain_docker_client_config_context_metadata",
    )
    if set(context_metadata) != {"path", "sha256", "bytes"}:
        raise R7ContractError("toolchain_docker_context_metadata_fields_mismatch")
    context_metadata_path = _absolute_normalized_path(
        context_metadata["path"], "toolchain_docker_context_metadata_path"
    )
    if (
        str(context_metadata_path).casefold()
        != str(CANONICAL_DOCKER_CONTEXT_METADATA_PATH).casefold()
    ):
        raise R7ContractError("toolchain_docker_context_metadata_path_mismatch")
    if (
        _full_sha256(context_metadata["sha256"], "toolchain_docker_context_metadata")
        != CANONICAL_DOCKER_CONTEXT_METADATA_SHA256
        or isinstance(context_metadata["bytes"], bool)
        or context_metadata["bytes"] != CANONICAL_DOCKER_CONTEXT_METADATA_BYTES
    ):
        raise R7ContractError("toolchain_docker_context_metadata_identity_mismatch")
    docker_policy = _mapping(docker_config["policy"], "toolchain_docker_client_config_policy")
    if dict(docker_policy) != DOCKER_CLIENT_CONFIG_POLICY:
        raise R7ContractError("toolchain_docker_client_config_policy_mismatch")
    normalized["docker_client_config"] = {
        "path": str(docker_config_path),
        "sha256": docker_config_sha256,
        "bytes": docker_config_bytes,
        "context_metadata": {
            "path": str(context_metadata_path),
            "sha256": CANONICAL_DOCKER_CONTEXT_METADATA_SHA256,
            "bytes": CANONICAL_DOCKER_CONTEXT_METADATA_BYTES,
        },
        "policy": dict(DOCKER_CLIENT_CONFIG_POLICY),
        "readback": _artifact_pin(
            docker_config["readback"],
            "toolchain_docker_client_config_readback",
            "s8-v4-x1-phase-b2-r7s1-docker-client-config-readback/v1",
        ),
    }
    kubernetes_config = _mapping(
        toolchain["kubernetes_client_config"], "toolchain_kubernetes_client_config"
    )
    if set(kubernetes_config) != {"path", "sha256", "bytes", "policy", "readback"}:
        raise R7ContractError("toolchain_kubernetes_client_config_fields_mismatch")
    kubernetes_config_path = _absolute_normalized_path(
        kubernetes_config["path"], "toolchain_kubernetes_client_config_path"
    )
    if (
        str(kubernetes_config_path).casefold()
        != str(CANONICAL_KUBERNETES_CLIENT_CONFIG_PATH).casefold()
    ):
        raise R7ContractError("toolchain_kubernetes_client_config_path_mismatch")
    kubernetes_config_sha256 = _full_sha256(
        kubernetes_config["sha256"], "toolchain_kubernetes_client_config"
    )
    if kubernetes_config_sha256 != CANONICAL_KUBERNETES_CLIENT_CONFIG_SHA256:
        raise R7ContractError("toolchain_kubernetes_client_config_sha256_mismatch")
    kubernetes_config_bytes = kubernetes_config["bytes"]
    if (
        isinstance(kubernetes_config_bytes, bool)
        or not isinstance(kubernetes_config_bytes, int)
        or kubernetes_config_bytes != CANONICAL_KUBERNETES_CLIENT_CONFIG_BYTES
    ):
        raise R7ContractError("toolchain_kubernetes_client_config_bytes_mismatch")
    kubernetes_policy = _mapping(
        kubernetes_config["policy"], "toolchain_kubernetes_client_config_policy"
    )
    if dict(kubernetes_policy) != KUBERNETES_CLIENT_CONFIG_POLICY:
        raise R7ContractError("toolchain_kubernetes_client_config_policy_mismatch")
    normalized["kubernetes_client_config"] = {
        "path": str(kubernetes_config_path),
        "sha256": kubernetes_config_sha256,
        "bytes": kubernetes_config_bytes,
        "policy": dict(KUBERNETES_CLIENT_CONFIG_POLICY),
        "readback": _artifact_pin(
            kubernetes_config["readback"],
            "toolchain_kubernetes_client_config_readback",
            "s8-v4-x1-phase-b2-r7s1-kubernetes-client-config-readback/v1",
        ),
    }
    if verify_files:
        for label, path, expected_sha, expected_bytes in (
            (
                "docker_client_config",
                docker_config_path,
                CANONICAL_DOCKER_CLIENT_CONFIG_SHA256,
                CANONICAL_DOCKER_CLIENT_CONFIG_BYTES,
            ),
            (
                "docker_context_metadata",
                context_metadata_path,
                CANONICAL_DOCKER_CONTEXT_METADATA_SHA256,
                CANONICAL_DOCKER_CONTEXT_METADATA_BYTES,
            ),
            (
                "kubernetes_client_config",
                kubernetes_config_path,
                CANONICAL_KUBERNETES_CLIENT_CONFIG_SHA256,
                CANONICAL_KUBERNETES_CLIENT_CONFIG_BYTES,
            ),
        ):
            _reject_reparse_ancestor_chain(path, f"toolchain_{label}")
            if (
                not path.is_file()
                or path.stat().st_size != expected_bytes
                or sha256_file(path) != expected_sha
            ):
                raise R7ContractError(f"toolchain_{label}_measured_identity_mismatch")
        _reject_reparse_ancestor_chain(
            CANONICAL_DOCKER_CONTEXT_TLS_PATH,
            "toolchain_docker_context_tls_material_directory",
        )
        if CANONICAL_DOCKER_CONTEXT_TLS_PATH.exists():
            raise R7ContractError("toolchain_docker_context_tls_material_directory_must_be_absent")
    python_distribution = _mapping(
        toolchain["python_distribution"], "toolchain_python_distribution"
    )
    if set(python_distribution) != {
        "implementation",
        "name",
        "version",
        "base_prefix",
        "distribution_tree_sha256",
        "file_count",
        "tree_encoding",
        "included_roots",
        "excluded_roots",
        "evidence",
    }:
        raise R7ContractError("toolchain_python_distribution_fields_mismatch")
    normalized["python_distribution"] = {
        "implementation": _nonempty(
            python_distribution["implementation"],
            "toolchain_python_distribution_implementation",
        ),
        "name": _nonempty(python_distribution["name"], "toolchain_python_distribution_name"),
        "version": _nonempty(
            python_distribution["version"], "toolchain_python_distribution_version"
        ),
        "base_prefix": str(
            _absolute_normalized_path(
                python_distribution["base_prefix"],
                "toolchain_python_distribution_base_prefix",
            )
        ),
        "distribution_tree_sha256": _full_sha256(
            python_distribution["distribution_tree_sha256"],
            "toolchain_python_distribution_tree",
        ),
        "file_count": python_distribution["file_count"],
        "tree_encoding": _nonempty(
            python_distribution["tree_encoding"],
            "toolchain_python_distribution_tree_encoding",
        ),
        "included_roots": list(
            _sequence(
                python_distribution["included_roots"],
                "toolchain_python_distribution_included_roots",
            )
        ),
        "excluded_roots": list(
            _sequence(
                python_distribution["excluded_roots"],
                "toolchain_python_distribution_excluded_roots",
            )
        ),
        "evidence": _artifact_pin(
            python_distribution["evidence"],
            "toolchain_python_distribution_evidence",
            "s8-v4-x1-phase-b2-r7s1-python-distribution-readback/v1",
        ),
    }
    if (
        isinstance(normalized["python_distribution"]["file_count"], bool)
        or not isinstance(normalized["python_distribution"]["file_count"], int)
        or normalized["python_distribution"]["file_count"] < 1
    ):
        raise R7ContractError("toolchain_python_distribution_file_count_invalid")
    expected_included = ["*.exe", "*.dll", "python*.zip", "DLLs/**", "Lib/**"]
    expected_excluded = [
        "Lib/site-packages/**",
        "**/__pycache__/**",
        "**/*.pyc",
        "**/*.pyo",
    ]
    if (
        normalized["python_distribution"]["included_roots"] != expected_included
        or normalized["python_distribution"]["excluded_roots"] != expected_excluded
    ):
        raise R7ContractError("toolchain_python_distribution_scope_mismatch")
    if normalized["python_distribution"]["tree_encoding"] != (
        "ordinal-relative-posix-utf8-nul-size-nul-sha256-nul;"
        "include=*.exe,*.dll,python*.zip,DLLs/**,Lib/**;"
        "exclude=Lib/site-packages/**,**/__pycache__/**,**/*.pyc,**/*.pyo"
    ):
        raise R7ContractError("toolchain_python_distribution_tree_encoding_mismatch")
    git_distribution = _mapping(toolchain["git_distribution"], "toolchain_git_distribution")
    if set(git_distribution) != {
        "root",
        "distribution_tree_sha256",
        "file_count",
        "tree_encoding",
        "evidence",
    }:
        raise R7ContractError("toolchain_git_distribution_fields_mismatch")
    git_root = _absolute_normalized_path(
        git_distribution["root"], "toolchain_git_distribution_root"
    )
    if str(git_root).casefold() != str(Path("C:/Program Files/Git").resolve()).casefold():
        raise R7ContractError("toolchain_git_distribution_root_mismatch")
    git_file_count = git_distribution["file_count"]
    if (
        isinstance(git_file_count, bool)
        or not isinstance(git_file_count, int)
        or git_file_count < 1
    ):
        raise R7ContractError("toolchain_git_distribution_file_count_invalid")
    git_tree_encoding = _nonempty(
        git_distribution["tree_encoding"], "toolchain_git_distribution_tree_encoding"
    )
    if git_tree_encoding != (
        "ordinal-relative-posix-utf8-nul-size-nul-sha256-nul;all-regular-files;reparse=reject"
    ):
        raise R7ContractError("toolchain_git_distribution_tree_encoding_mismatch")
    normalized["git_distribution"] = {
        "root": str(git_root),
        "distribution_tree_sha256": _full_sha256(
            git_distribution["distribution_tree_sha256"],
            "toolchain_git_distribution_tree",
        ),
        "file_count": git_file_count,
        "tree_encoding": git_tree_encoding,
        "evidence": _artifact_pin(
            git_distribution["evidence"],
            "toolchain_git_distribution_evidence",
            "s8-v4-x1-phase-b2-r7s1-git-distribution-readback/v1",
        ),
    }
    windows = _mapping(toolchain["windows_tcb"], "toolchain_windows_tcb")
    if set(windows) != {"build", "system32_path", "kernel", "evidence"}:
        raise R7ContractError("toolchain_windows_tcb_fields_mismatch")
    normalized["windows_tcb"] = {
        "build": _nonempty(windows["build"], "toolchain_windows_build"),
        "system32_path": str(
            _absolute_normalized_path(windows["system32_path"], "toolchain_windows_system32_path")
        ),
        "kernel": _host_binary_pin(
            windows["kernel"], "toolchain_windows_kernel", verify_file=verify_files
        ),
        "evidence": _artifact_pin(
            windows["evidence"],
            "toolchain_windows_tcb_evidence",
            "s8-v4-x1-phase-b2-r7s1-windows-tcb-readback/v1",
        ),
    }
    wsl = _mapping(toolchain["wsl_runtime"], "toolchain_wsl_runtime")
    if set(wsl) != {
        "distro",
        "kernel_release",
        "rootfs_identity",
        "python3",
        "readback",
    }:
        raise R7ContractError("toolchain_wsl_runtime_fields_mismatch")
    normalized["wsl_runtime"] = {
        "distro": _nonempty(wsl["distro"], "toolchain_wsl_distro"),
        "kernel_release": _nonempty(wsl["kernel_release"], "toolchain_wsl_kernel_release"),
        "rootfs_identity": _nonempty(wsl["rootfs_identity"], "toolchain_wsl_rootfs_identity"),
        "python3": _runtime_binary_pin(wsl["python3"], "toolchain_wsl_python3"),
        "readback": _artifact_pin(
            wsl["readback"],
            "toolchain_wsl_readback",
            "s8-v4-x1-phase-b2-r7s1-wsl-runtime-readback/v1",
        ),
    }
    psql = _mapping(toolchain["container_psql"], "toolchain_container_psql")
    if set(psql) != {
        "container_name",
        "image_digest",
        "realpath",
        "sha256",
        "bytes",
        "version",
        "execution_scope",
        "readback",
    }:
        raise R7ContractError("toolchain_container_psql_fields_mismatch")
    execution_scope = _mapping(psql["execution_scope"], "toolchain_container_psql_execution_scope")
    if dict(execution_scope) != DOCKER_CONTAINER_EXECUTION_SCOPE:
        raise R7ContractError("toolchain_container_psql_execution_scope_mismatch")
    normalized["container_psql"] = {
        "container_name": _nonempty(psql["container_name"], "toolchain_container_psql_name"),
        "image_digest": _sha256_id(psql["image_digest"], "toolchain_container_psql_image"),
        **_runtime_binary_pin(
            {key: psql[key] for key in ("realpath", "sha256", "bytes", "version")},
            "toolchain_container_psql_binary",
        ),
        "execution_scope": dict(DOCKER_CONTAINER_EXECUTION_SCOPE),
        "readback": _artifact_pin(
            psql["readback"],
            "toolchain_container_psql_readback",
            "s8-v4-x1-phase-b2-r7s1-container-psql-readback/v1",
        ),
    }
    if verify_files:
        for label, pin in (
            ("docker_client_config", normalized["docker_client_config"]["readback"]),
            ("python_distribution", normalized["python_distribution"]["evidence"]),
            ("git_distribution", normalized["git_distribution"]["evidence"]),
            (
                "git_repository_attributes",
                normalized["git_repository_attributes"]["readback"],
            ),
            ("git_repository_config", normalized["git_repository_config"]["readback"]),
            (
                "kubernetes_client_config",
                normalized["kubernetes_client_config"]["readback"],
            ),
            ("windows_tcb", normalized["windows_tcb"]["evidence"]),
            ("wsl_runtime", normalized["wsl_runtime"]["readback"]),
            ("container_psql", normalized["container_psql"]["readback"]),
        ):
            payload = _read_pinned_json(pin, f"toolchain_{label}_readback")
            if payload.get("schema") != pin["schema"] or payload.get("status") != "verified":
                raise R7ContractError(f"toolchain_{label}_readback_unknown_or_unverified")
            if label == "docker_client_config":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "path",
                    "sha256",
                    "bytes",
                    "top_level_keys",
                    "auth_entries",
                    "credential_store_present",
                    "credential_store_value_exposed",
                    "current_context",
                    "context_metadata",
                    "endpoint_identity",
                    "tls_material_directory_absent",
                    "policy_sha256",
                }:
                    raise R7ContractError("toolchain_docker_client_config_readback_fields_mismatch")
                _utc_timestamp(
                    payload["captured_at"],
                    "toolchain_docker_client_config_readback_captured_at",
                )
                expected_policy_sha256 = hashlib.sha256(
                    canonical_json_bytes(DOCKER_CLIENT_CONFIG_POLICY)
                ).hexdigest()
                if (
                    payload["path"] != normalized["docker_client_config"]["path"]
                    or payload["sha256"] != normalized["docker_client_config"]["sha256"]
                    or payload["bytes"] != normalized["docker_client_config"]["bytes"]
                    or payload["top_level_keys"] != DOCKER_CLIENT_CONFIG_POLICY["top_level_keys"]
                    or payload["auth_entries"] != 0
                    or payload["credential_store_present"] is not True
                    or payload["credential_store_value_exposed"] is not False
                    or payload["current_context"] != "desktop-linux"
                    or payload["context_metadata"]
                    != normalized["docker_client_config"]["context_metadata"]
                    or payload["endpoint_identity"] != DOCKER_CONTEXT_ENDPOINT_IDENTITY
                    or payload["tls_material_directory_absent"] is not True
                    or payload["policy_sha256"] != expected_policy_sha256
                ):
                    raise R7ContractError(
                        "toolchain_docker_client_config_readback_projection_mismatch"
                    )
            elif label == "python_distribution":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "implementation",
                    "name",
                    "version",
                    "base_prefix",
                    "distribution_tree_sha256",
                    "file_count",
                    "tree_encoding",
                    "included_roots",
                    "excluded_roots",
                }:
                    raise R7ContractError("toolchain_python_distribution_readback_fields_mismatch")
                _utc_timestamp(
                    payload["captured_at"],
                    "toolchain_python_distribution_readback_captured_at",
                )
                projection = {
                    key: normalized["python_distribution"][key]
                    for key in (
                        "implementation",
                        "name",
                        "version",
                        "base_prefix",
                        "distribution_tree_sha256",
                        "file_count",
                        "tree_encoding",
                        "included_roots",
                        "excluded_roots",
                    )
                }
                if {key: payload[key] for key in projection} != projection:
                    raise R7ContractError(
                        "toolchain_python_distribution_readback_projection_mismatch"
                    )
            elif label == "git_distribution":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "root",
                    "distribution_tree_sha256",
                    "file_count",
                    "tree_encoding",
                    "volume_identity",
                    "filesystem_identity",
                    "reparse_entries",
                }:
                    raise R7ContractError("toolchain_git_distribution_readback_fields_mismatch")
                _utc_timestamp(
                    payload["captured_at"], "toolchain_git_distribution_readback_captured_at"
                )
                for field in ("root", "distribution_tree_sha256", "file_count", "tree_encoding"):
                    if payload[field] != normalized["git_distribution"][field]:
                        raise R7ContractError(
                            f"toolchain_git_distribution_readback_{field}_mismatch"
                        )
                _nonempty(
                    payload["volume_identity"],
                    "toolchain_git_distribution_readback_volume_identity",
                )
                _nonempty(
                    payload["filesystem_identity"],
                    "toolchain_git_distribution_readback_filesystem_identity",
                )
                if payload["reparse_entries"] != 0 or isinstance(payload["reparse_entries"], bool):
                    raise R7ContractError(
                        "toolchain_git_distribution_readback_reparse_entries_must_equal_zero"
                    )
            elif label == "git_repository_attributes":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "path",
                    "sha256",
                    "bytes",
                    "rule_count",
                    "pattern_sha256",
                    "attribute_tokens",
                    "forbidden_attributes_absent",
                    "git_top_level_attributes_absent",
                    "git_info_attributes_absent",
                    "system_attributes_disabled",
                    "policy_sha256",
                }:
                    raise R7ContractError(
                        "toolchain_git_repository_attributes_readback_fields_mismatch"
                    )
                _utc_timestamp(
                    payload["captured_at"],
                    "toolchain_git_repository_attributes_readback_captured_at",
                )
                expected_policy_sha256 = hashlib.sha256(
                    canonical_json_bytes(GIT_REPOSITORY_ATTRIBUTES_POLICY)
                ).hexdigest()
                if (
                    payload["path"] != normalized["git_repository_attributes"]["path"]
                    or payload["sha256"] != normalized["git_repository_attributes"]["sha256"]
                    or payload["bytes"] != normalized["git_repository_attributes"]["bytes"]
                    or payload["rule_count"] != 20
                    or payload["pattern_sha256"] != list(GIT_ATTRIBUTES_PATTERN_SHA256)
                    or payload["attribute_tokens"] != ["text", "eol=lf"]
                    or payload["forbidden_attributes_absent"] is not True
                    or payload["git_top_level_attributes_absent"] is not True
                    or payload["git_info_attributes_absent"] is not True
                    or payload["system_attributes_disabled"] is not True
                    or payload["policy_sha256"] != expected_policy_sha256
                ):
                    raise R7ContractError(
                        "toolchain_git_repository_attributes_readback_projection_mismatch"
                    )
            elif label == "git_repository_config":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "path",
                    "sha256",
                    "bytes",
                    "key_names",
                    "origin_identity",
                    "config_worktree_absent",
                    "policy_sha256",
                }:
                    raise R7ContractError(
                        "toolchain_git_repository_config_readback_fields_mismatch"
                    )
                _utc_timestamp(
                    payload["captured_at"],
                    "toolchain_git_repository_config_readback_captured_at",
                )
                expected_policy_sha256 = hashlib.sha256(
                    canonical_json_bytes(GIT_REPOSITORY_CONFIG_POLICY)
                ).hexdigest()
                if (
                    payload["path"] != normalized["git_repository_config"]["path"]
                    or payload["sha256"] != normalized["git_repository_config"]["sha256"]
                    or payload["bytes"] != normalized["git_repository_config"]["bytes"]
                    or payload["key_names"] != list(GIT_CONFIG_ALLOWED_KEY_NAMES)
                    or payload["origin_identity"] != GIT_CONFIG_ORIGIN_IDENTITY
                    or payload["config_worktree_absent"] is not True
                    or payload["policy_sha256"] != expected_policy_sha256
                ):
                    raise R7ContractError(
                        "toolchain_git_repository_config_readback_projection_mismatch"
                    )
            elif label == "kubernetes_client_config":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "path",
                    "sha256",
                    "bytes",
                    "current_context",
                    "object_counts",
                    "context_identity",
                    "cluster_identity",
                    "user_identity",
                    "forbidden_fields_absent",
                    "multiple_config_merge_forbidden",
                    "embedded_material_presence",
                    "policy_sha256",
                }:
                    raise R7ContractError(
                        "toolchain_kubernetes_client_config_readback_fields_mismatch"
                    )
                _utc_timestamp(
                    payload["captured_at"],
                    "toolchain_kubernetes_client_config_readback_captured_at",
                )
                expected_policy_sha256 = hashlib.sha256(
                    canonical_json_bytes(KUBERNETES_CLIENT_CONFIG_POLICY)
                ).hexdigest()
                if (
                    payload["path"] != normalized["kubernetes_client_config"]["path"]
                    or payload["sha256"] != normalized["kubernetes_client_config"]["sha256"]
                    or payload["bytes"] != normalized["kubernetes_client_config"]["bytes"]
                    or payload["current_context"] != "docker-desktop"
                    or payload["object_counts"] != KUBERNETES_CLIENT_CONFIG_POLICY["object_counts"]
                    or payload["context_identity"]
                    != KUBERNETES_CLIENT_CONFIG_POLICY["context_identity"]
                    or payload["cluster_identity"]
                    != KUBERNETES_CLIENT_CONFIG_POLICY["cluster_identity"]
                    or payload["user_identity"] != KUBERNETES_CLIENT_CONFIG_POLICY["user_identity"]
                    or payload["forbidden_fields_absent"]
                    != KUBERNETES_CLIENT_CONFIG_POLICY["forbidden_fields_absent"]
                    or payload["multiple_config_merge_forbidden"] is not True
                    or payload["embedded_material_presence"]
                    != KUBERNETES_CLIENT_CONFIG_POLICY["embedded_material_presence"]
                    or payload["policy_sha256"] != expected_policy_sha256
                ):
                    raise R7ContractError(
                        "toolchain_kubernetes_client_config_readback_projection_mismatch"
                    )
            elif label == "windows_tcb":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "build",
                    "system32_path",
                    "kernel",
                }:
                    raise R7ContractError("toolchain_windows_tcb_readback_fields_mismatch")
                _utc_timestamp(payload["captured_at"], "toolchain_windows_tcb_readback_captured_at")
                if (
                    payload["build"] != normalized["windows_tcb"]["build"]
                    or payload["system32_path"] != normalized["windows_tcb"]["system32_path"]
                    or payload["kernel"] != normalized["windows_tcb"]["kernel"]
                ):
                    raise R7ContractError("toolchain_windows_tcb_readback_projection_mismatch")
            elif label == "wsl_runtime":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "distro",
                    "kernel_release",
                    "rootfs_identity",
                    "python3",
                }:
                    raise R7ContractError("toolchain_wsl_runtime_readback_fields_mismatch")
                _utc_timestamp(payload["captured_at"], "toolchain_wsl_runtime_readback_captured_at")
                if {
                    key: payload[key]
                    for key in ("distro", "kernel_release", "rootfs_identity", "python3")
                } != {
                    key: normalized["wsl_runtime"][key]
                    for key in ("distro", "kernel_release", "rootfs_identity", "python3")
                }:
                    raise R7ContractError("toolchain_wsl_runtime_readback_projection_mismatch")
            elif label == "container_psql":
                if set(payload) != {
                    "schema",
                    "status",
                    "captured_at",
                    "container_name",
                    "image_digest",
                    "realpath",
                    "sha256",
                    "bytes",
                    "version",
                    "execution_scope",
                }:
                    raise R7ContractError("toolchain_container_psql_readback_fields_mismatch")
                _utc_timestamp(
                    payload["captured_at"], "toolchain_container_psql_readback_captured_at"
                )
                if {
                    key: payload[key]
                    for key in (
                        "container_name",
                        "image_digest",
                        "realpath",
                        "sha256",
                        "bytes",
                        "version",
                        "execution_scope",
                    )
                } != {
                    key: normalized["container_psql"][key]
                    for key in (
                        "container_name",
                        "image_digest",
                        "realpath",
                        "sha256",
                        "bytes",
                        "version",
                        "execution_scope",
                    )
                }:
                    raise R7ContractError("toolchain_container_psql_readback_projection_mismatch")
    return normalized


def _validate_parent_entries(
    value: Any,
    *,
    bundle_directory: Path | None = None,
    output_directory: Path | None = None,
) -> dict[str, dict[str, Any]]:
    entries = _sequence(value, "parent_checkpoints")
    if len(entries) != len(PARENT_CHECKPOINT_ROLES):
        raise R7ContractError("parent_checkpoint_count_mismatch")
    normalized: dict[str, dict[str, Any]] = {}
    paths: set[Path] = set()
    for raw in entries:
        entry = _mapping(raw, "parent_checkpoint")
        required = {
            "role",
            "kind",
            "path",
            "sha256",
            "schema",
            "run_id",
            "immutable",
            "must_not_execute",
        }
        if set(entry) != required:
            raise R7ContractError("parent_checkpoint_fields_mismatch")
        role = str(entry["role"])
        if role not in PARENT_CHECKPOINT_KINDS or role in normalized:
            raise R7ContractError("parent_checkpoint_role_set_mismatch")
        if entry["kind"] != PARENT_CHECKPOINT_KINDS[role]:
            raise R7ContractError(f"parent_checkpoint_kind_mismatch:{role}")
        if entry["schema"] != PARENT_CHECKPOINT_SCHEMAS[role]:
            raise R7ContractError(f"parent_checkpoint_schema_mismatch:{role}")
        if entry["immutable"] is not True or entry["must_not_execute"] is not True:
            raise R7ContractError(f"parent_checkpoint_immutable_no_execute_required:{role}")
        path = Path(_nonempty(entry["path"], f"parent_checkpoint_{role}_path")).resolve()
        if path in paths:
            raise R7ContractError("parent_checkpoint_paths_must_be_distinct")
        paths.add(path)
        if bundle_directory is not None and not _resolved_outside(path, bundle_directory):
            raise R7ContractError(f"parent_checkpoint_inside_bundle:{role}")
        if output_directory is not None and not _resolved_outside(path, output_directory):
            raise R7ContractError(f"parent_checkpoint_inside_output:{role}")
        normalized[role] = {
            "role": role,
            "kind": str(entry["kind"]),
            "path": str(path),
            "sha256": _full_sha256(entry["sha256"], f"parent_checkpoint_{role}"),
            "schema": PARENT_CHECKPOINT_SCHEMAS[role],
            "run_id": _nonempty(entry["run_id"], f"parent_checkpoint_{role}_run_id"),
            "immutable": True,
            "must_not_execute": True,
        }
    if set(normalized) != set(PARENT_CHECKPOINT_ROLES):
        raise R7ContractError("parent_checkpoint_role_set_mismatch")
    if tuple(normalized) != PARENT_CHECKPOINT_ROLES:
        raise R7ContractError("parent_checkpoint_order_mismatch")
    return normalized


def parent_map_sha256(parents: Mapping[str, Mapping[str, Any]]) -> str:
    if tuple(parents) != PARENT_CHECKPOINT_ROLES:
        raise R7ContractError("parent_map_role_order_mismatch")
    projected = {
        role: {
            "path": str(parents[role]["path"]),
            "sha256": str(parents[role]["sha256"]),
            "schema": str(parents[role]["schema"]),
            "run_id": str(parents[role]["run_id"]),
        }
        for role in PARENT_CHECKPOINT_ROLES
    }
    return hashlib.sha256(canonical_json_bytes(projected)).hexdigest()


def _validate_compose(value: Any) -> dict[str, Any]:
    compose = _mapping(value, "expected_state_compose")
    required = {
        "project_name",
        "config_path",
        "config_sha256",
        "long_lived_services",
        "one_shot_services",
        "service_pins",
        "stability",
    }
    if set(compose) != required:
        raise R7ContractError("expected_state_compose_fields_mismatch")
    _nonempty(compose["project_name"], "compose_project_name")
    _nonempty(compose["config_path"], "compose_config_path")
    _full_sha256(compose["config_sha256"], "compose_config")
    if tuple(_sequence(compose["long_lived_services"], "long_lived_services")) != (
        LONG_LIVED_SERVICES
    ):
        raise R7ContractError("compose_long_lived_services_mismatch")
    if tuple(_sequence(compose["one_shot_services"], "one_shot_services")) != ONE_SHOT_SERVICES:
        raise R7ContractError("compose_one_shot_services_mismatch")
    pins = _mapping(compose["service_pins"], "compose_service_pins")
    if set(pins) != set(LONG_LIVED_SERVICES):
        raise R7ContractError("compose_service_pin_role_set_mismatch")
    container_ids: set[str] = set()
    for service in LONG_LIVED_SERVICES:
        pin = _mapping(pins[service], f"compose_service_pin_{service}")
        if set(pin) != {"container_name", "container_id", "image_id", "healthcheck_expected"}:
            raise R7ContractError(f"compose_service_pin_fields_mismatch:{service}")
        _nonempty(pin["container_name"], f"compose_{service}_container_name")
        container_id = _full_sha256(pin["container_id"], f"compose_{service}_container_id")
        if container_id in container_ids:
            raise R7ContractError("compose_container_ids_must_be_distinct")
        container_ids.add(container_id)
        _sha256_id(pin["image_id"], f"compose_{service}_image_id")
        if not isinstance(pin["healthcheck_expected"], bool):
            raise R7ContractError(f"compose_{service}_healthcheck_boolean_required")
    stability = _mapping(compose["stability"], "compose_stability")
    expected_stability = {
        "duration_seconds": 300,
        "interval_seconds": 5,
        "samples": 61,
        "restart_delta": 0,
    }
    if dict(stability) != expected_stability:
        raise R7ContractError("compose_stability_contract_mismatch")
    return dict(compose)


def _validate_api(
    value: Any, revision: str, tree: str, compose: Mapping[str, Any]
) -> dict[str, Any]:
    api = _mapping(value, "expected_state_api")
    required = {
        "base_url",
        "api_container_name",
        "worker_container_name",
        "image_id",
        "source_revision",
        "source_tree",
        "image_attestation",
    }
    if set(api) != required:
        raise R7ContractError("expected_state_api_fields_mismatch")
    if api["base_url"] != EXPECTED_API_BASE_URL:
        raise R7ContractError("api_base_url_mismatch")
    if api["api_container_name"] != "evm-api":
        raise R7ContractError("api_container_name_mismatch")
    if api["worker_container_name"] != "evm-task-queue-worker":
        raise R7ContractError("worker_container_name_mismatch")
    image_id = _sha256_id(api["image_id"], "api_image_id")
    if _full_sha1(api["source_revision"], "api_source_revision") != revision:
        raise R7ContractError("api_source_revision_mismatch")
    if _full_sha1(api["source_tree"], "api_source_tree") != tree:
        raise R7ContractError("api_source_tree_mismatch")
    attestation = _mapping(api["image_attestation"], "api_image_attestation")
    if set(attestation) != {"path", "sha256"}:
        raise R7ContractError("api_image_attestation_fields_mismatch")
    _nonempty(attestation["path"], "api_image_attestation_path")
    _full_sha256(attestation["sha256"], "api_image_attestation")
    service_pins = _mapping(compose["service_pins"], "compose_service_pins")
    for service in ("api", "task-queue-worker"):
        pin = _mapping(service_pins[service], f"compose_service_pin_{service}")
        if str(pin["image_id"]).lower() != image_id:
            raise R7ContractError(f"api_shared_image_id_mismatch:{service}")
    return dict(api)


def _validate_database(value: Any) -> dict[str, Any]:
    database = _mapping(value, "expected_state_database")
    required = {
        "instances",
        "control_plane_schema_versions",
        "mlflow_migration_head",
        "airflow_migration_head",
    }
    if set(database) != required:
        raise R7ContractError("expected_state_database_fields_mismatch")
    instances = _mapping(database["instances"], "database_instances")
    if {key: dict(_mapping(raw, f"database_instance_{key}")) for key, raw in instances.items()} != (
        DATABASE_INSTANCES
    ):
        raise R7ContractError("database_instance_contract_mismatch")
    versions = tuple(
        str(item)
        for item in _sequence(
            database["control_plane_schema_versions"], "control_plane_schema_versions"
        )
    )
    if not versions or len(set(versions)) != len(versions):
        raise R7ContractError("control_plane_schema_versions_nonempty_unique_required")
    if versions != tuple(sorted(versions)) or any(
        MIGRATION_VERSION.fullmatch(item) is None for item in versions
    ):
        raise R7ContractError("control_plane_schema_versions_canonical_order_required")
    if database["mlflow_migration_head"] != MLFLOW_MIGRATION_HEAD:
        raise R7ContractError("mlflow_migration_head_mismatch")
    if database["airflow_migration_head"] != AIRFLOW_MIGRATION_HEAD:
        raise R7ContractError("airflow_migration_head_mismatch")
    return dict(database)


def _typed_failed_pod(value: Any, label: str) -> dict[str, Any]:
    pod = _mapping(value, label)
    required = {
        "uid",
        "namespace",
        "name",
        "reason",
        "reason_source",
        "owner_uid",
        "owner_kind",
        "owner_name",
        "owner_controller",
    }
    if set(pod) != required:
        raise R7ContractError(f"{label}_fields_mismatch")
    for field_name in required - {"owner_controller"}:
        _nonempty(pod[field_name], f"{label}_{field_name}")
    _uuid(pod["uid"], f"{label}_uid")
    _uuid(pod["owner_uid"], f"{label}_owner_uid")
    if pod["owner_controller"] is not True:
        raise R7ContractError(f"{label}_controller_owner_required")
    source = pod["reason_source"]
    if source == "pod.status.reason":
        if (
            pod["reason"] != "UnexpectedAdmissionError"
            or pod["owner_kind"] != "ReplicaSet"
            or pod["namespace"] != "evm-production"
            or not str(pod["name"]).startswith("evm-b0-production-")
        ):
            raise R7ContractError(f"{label}_b0_reason_contract_mismatch")
    elif source == "owner_job.status.conditions[type=Failed].reason":
        if (
            pod["owner_kind"] != "Job"
            or pod["namespace"] != "evm-training"
            or pod["reason"] != "BackoffLimitExceeded"
            or not str(pod["name"]).startswith("evm-lifecycle-train-")
            or not str(pod["owner_name"]).startswith("evm-lifecycle-train-")
        ):
            raise R7ContractError(f"{label}_terminal_job_reason_contract_mismatch")
    else:
        raise R7ContractError(f"{label}_reason_source_mismatch")
    return dict(pod)


def _validate_kubernetes(value: Any) -> dict[str, Any]:
    kubernetes = _mapping(value, "expected_state_kubernetes")
    if set(kubernetes) != {
        "allowed_historical_failed_pods",
        "health_confirmation_samples",
        "residual_selectors",
    }:
        raise R7ContractError("expected_state_kubernetes_fields_mismatch")
    if kubernetes["health_confirmation_samples"] != 2 or isinstance(
        kubernetes["health_confirmation_samples"], bool
    ):
        raise R7ContractError("kubernetes_health_confirmation_samples_must_equal_2")
    pods = [
        _typed_failed_pod(raw, "historical_failed_pod")
        for raw in _sequence(kubernetes["allowed_historical_failed_pods"], "historical_failed_pods")
    ]
    if len(pods) != 14:
        raise R7ContractError("historical_failed_pod_count_must_equal_14")
    if (
        len({pod["uid"] for pod in pods}) != 14
        or len({(pod["namespace"], pod["name"]) for pod in pods}) != 14
    ):
        raise R7ContractError("historical_failed_pods_must_be_unique")
    if sum(pod["reason_source"] == "pod.status.reason" for pod in pods) != 11:
        raise R7ContractError("historical_b0_failed_pod_count_must_equal_11")
    if (
        sum(
            pod["reason_source"] == "owner_job.status.conditions[type=Failed].reason"
            for pod in pods
        )
        != 3
    ):
        raise R7ContractError("historical_terminal_job_pod_count_must_equal_3")
    projected_identities = tuple(
        tuple(pod[name] for name in FAILED_POD_IDENTITY_FIELDS) for pod in pods
    )
    if projected_identities != EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES:
        raise R7ContractError("historical_failed_pod_exact_identity_set_or_order_mismatch")
    selectors = tuple(_sequence(kubernetes["residual_selectors"], "residual_selectors"))
    if selectors != ("evm.openai.local/scenario=s8-v4-x1",):
        raise R7ContractError("kubernetes_residual_selectors_mismatch")
    return {**dict(kubernetes), "allowed_historical_failed_pods": pods}


MIN_OBSERVATION_GAP_SECONDS = 30
MAX_OBSERVATION_AGE_SECONDS = 3_600
TRUSTED_CHECKPOINT_SCHEMA = "s8-v4-x1-phase-b2-r7s1-trusted-terminal-fencing-checkpoint/v1"


def _utc_timestamp(value: Any, label: str) -> datetime:
    text = _nonempty(value, label)
    if not text.endswith("Z"):
        raise R7ContractError(f"{label}_utc_timestamp_required")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise R7ContractError(f"{label}_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise R7ContractError(f"{label}_utc_timestamp_required")
    return parsed


def _source_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    required = {
        "path",
        "sha256",
        "schema",
        "captured_at",
        "ordinal",
        "source_revision",
    }
    if set(pin) != required:
        raise R7ContractError(f"{label}_fields_mismatch")
    ordinal = pin["ordinal"]
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal not in {1, 2}:
        raise R7ContractError(f"{label}_ordinal_invalid")
    captured_at = _nonempty(pin["captured_at"], f"{label}_captured_at")
    _utc_timestamp(captured_at, f"{label}_captured_at")
    return {
        "path": str(_absolute_normalized_path(pin["path"], f"{label}_path")),
        "sha256": _full_sha256(pin["sha256"], label),
        "schema": _nonempty(pin["schema"], f"{label}_schema"),
        "captured_at": captured_at,
        "ordinal": ordinal,
        "source_revision": _full_sha1(pin["source_revision"], f"{label}_source_revision"),
    }


def _artifact_pin(value: Any, label: str, expected_schema: str) -> dict[str, str]:
    pin = _mapping(value, label)
    if set(pin) != {"path", "sha256", "schema"}:
        raise R7ContractError(f"{label}_fields_mismatch")
    if pin["schema"] != expected_schema:
        raise R7ContractError(f"{label}_schema_mismatch")
    return {
        "path": str(_absolute_normalized_path(pin["path"], f"{label}_path")),
        "sha256": _full_sha256(pin["sha256"], label),
        "schema": expected_schema,
    }


def _read_pinned_json(pin: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    path = Path(str(pin["path"]))
    if not path.is_file():
        raise R7ContractError(f"{label}_file_missing:{path}")
    try:
        payload, measured = _read_json_snapshot(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7ContractError(f"{label}_json_invalid") from exc
    if measured != pin["sha256"]:
        raise R7ContractError(f"{label}_sha256_mismatch")
    return _mapping(payload, f"{label}_payload")


def _mlflow_identity(value: Any, label: str) -> dict[str, str]:
    identity = _mapping(value, label)
    required = {"run_id", "status", "lifecycle_stage", "start_time", "end_time"}
    if set(identity) != required:
        raise R7ContractError(f"{label}_fields_mismatch")
    normalized = {name: "" if identity[name] is None else str(identity[name]) for name in required}
    for name in ("run_id", "status", "lifecycle_stage", "start_time"):
        _nonempty(normalized[name], f"{label}_{name}")
    if normalized["status"] != "RUNNING":
        raise R7ContractError("external_terminal_fencing_target_must_remain_factually_running")
    if normalized["end_time"] != "":
        raise R7ContractError("external_terminal_fencing_empty_end_time_required")
    return normalized


def _successor_binding(value: Any, label: str) -> dict[str, str]:
    binding = _mapping(value, label)
    if set(binding) != {
        "run_id",
        "attempt_id",
        "commit",
        "tree",
        "nonce",
        "parent_map_sha256",
        "staging_path",
        "output_path",
        "emergency_seal_path",
    }:
        raise R7ContractError(f"{label}_fields_mismatch")
    run_id = _nonempty(binding["run_id"], f"{label}_run_id")
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{15,160}", run_id) is None
        or "r7s1" not in run_id.lower()
    ):
        raise R7ContractError(f"{label}_r7s1_run_id_required")
    attempt_id = _nonempty(binding["attempt_id"], f"{label}_attempt_id")
    try:
        parsed_attempt = uuid.UUID(attempt_id)
    except (AttributeError, ValueError) as exc:
        raise R7ContractError(f"{label}_attempt_id_canonical_uuid_required") from exc
    if str(parsed_attempt) != attempt_id:
        raise R7ContractError(f"{label}_attempt_id_canonical_uuid_required")
    nonce = _full_sha256(binding["nonce"], f"{label}_nonce")
    if attempt_id.casefold() == run_id.casefold() or parsed_attempt.hex == nonce:
        raise R7ContractError(f"{label}_attempt_identity_must_be_distinct")
    staging_path = _absolute_normalized_path(binding["staging_path"], f"{label}_staging_path")
    output_path = _absolute_normalized_path(binding["output_path"], f"{label}_output_path")
    emergency_seal_path = _absolute_normalized_path(
        binding["emergency_seal_path"], f"{label}_emergency_seal_path"
    )
    expected_staging = (CANONICAL_STAGING_ROOT / run_id).resolve()
    expected_output = (CANONICAL_OUTPUT_ROOT / run_id).resolve()
    expected_emergency = (CANONICAL_OUTPUT_ROOT / f"{run_id}-emergency-seal").resolve()
    if str(staging_path).casefold() != str(expected_staging).casefold():
        raise R7ContractError(f"{label}_canonical_staging_path_mismatch")
    if str(output_path).casefold() != str(expected_output).casefold():
        raise R7ContractError(f"{label}_canonical_output_path_mismatch")
    if str(emergency_seal_path).casefold() != str(expected_emergency).casefold():
        raise R7ContractError(f"{label}_canonical_emergency_seal_path_mismatch")
    if (
        len(
            {
                str(staging_path).casefold(),
                str(output_path).casefold(),
                str(emergency_seal_path).casefold(),
            }
        )
        != 3
    ):
        raise R7ContractError(f"{label}_staging_output_paths_must_be_distinct")
    return {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "commit": _full_sha1(binding["commit"], f"{label}_commit"),
        "tree": _full_sha1(binding["tree"], f"{label}_tree"),
        "nonce": nonce,
        "parent_map_sha256": _full_sha256(binding["parent_map_sha256"], f"{label}_parent_map"),
        "staging_path": str(staging_path),
        "output_path": str(output_path),
        "emergency_seal_path": str(emergency_seal_path),
    }


def _validate_source_pin_readback(
    payload: Mapping[str, Any],
    pin: Mapping[str, Any],
    *,
    expected_schema: str,
    ordinal: int,
    label: str,
) -> datetime:
    if (
        pin["schema"] != expected_schema
        or pin["ordinal"] != ordinal
        or payload.get("schema") != expected_schema
        or payload.get("ordinal") != ordinal
        or payload.get("captured_at") != pin["captured_at"]
        or payload.get("source_revision") != pin["source_revision"]
    ):
        raise R7ContractError(f"{label}_pin_metadata_readback_mismatch")
    return _utc_timestamp(payload.get("captured_at"), label)


def _observation_timestamp(value: Any, label: str) -> datetime:
    text = _nonempty(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise R7ContractError(f"{label}_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise R7ContractError(f"{label}_utc_timestamp_required")
    return parsed.astimezone(UTC)


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise R7ContractError(f"{label}_nonnegative_integer_required")
    return value


def _validate_stream_digest(value: Any, label: str) -> None:
    stream = _mapping(value, label)
    if set(stream) != {"bytes", "redacted", "sha256"}:
        raise R7ContractError(f"{label}_fields_mismatch")
    _nonnegative_int(stream["bytes"], f"{label}_bytes")
    if stream["redacted"] is not True:
        raise R7ContractError(f"{label}_redaction_required")
    _full_sha256(stream["sha256"], label)


def _normalized_observation_argv_sha256(name: str, argv: Sequence[str], label: str) -> str:
    normalized = list(argv)
    if name == "windows_global_residuals":
        replacement, count = re.subn(
            r"\$excluded=@\([1-9][0-9]*\);",
            "$excluded=@(<pid>);",
            normalized[-1],
            count=1,
        )
        if count != 1:
            raise R7ContractError(f"{label}_windows_exclusion_binding_invalid")
        normalized[-1] = replacement
    elif name == "windows_run_links":
        pattern = r"\$excluded=@\(\$PID,([1-9][0-9]*),([1-9][0-9]*)\);"
        match = re.search(pattern, normalized[-1])
        if match is None or int(match.group(1)) >= int(match.group(2)):
            raise R7ContractError(f"{label}_windows_exclusion_binding_invalid")
        normalized[-1], count = re.subn(
            pattern,
            "$excluded=@($PID,<pids>);",
            normalized[-1],
            count=1,
        )
        if count != 1:
            raise R7ContractError(f"{label}_windows_exclusion_binding_invalid")
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def _validate_observation_commands(
    payload: Mapping[str, Any],
    expected_names: tuple[str, ...],
    expected_argv_sha256: Mapping[str, str],
    label: str,
) -> None:
    expected_count = len(expected_names)
    if (
        payload["command_count"] != expected_count
        or isinstance(payload["command_count"], bool)
        or payload["expected_command_count"] != expected_count
        or isinstance(payload["expected_command_count"], bool)
    ):
        raise R7ContractError(f"{label}_exact_command_count_required")
    commands = _sequence(payload["commands"], f"{label}_commands")
    if len(commands) != expected_count:
        raise R7ContractError(f"{label}_exact_command_count_required")
    required_fields = {
        "accounting",
        "active_process_zero",
        "cancelled",
        "command",
        "duration_seconds",
        "ended_at_utc",
        "errors",
        "events",
        "final_active_process_count",
        "forced_termination_attempts",
        "identities",
        "identity_coverage_complete",
        "job_limit_flags",
        "manual_intervention_required",
        "name",
        "residual_pids",
        "return_code",
        "run_uuid",
        "safe_for_followup",
        "safe_for_followup_gate",
        "started_at_utc",
        "stderr",
        "stderr_drained",
        "stdout",
        "stdout_drained",
        "streams_drained",
        "timed_out",
    }
    for expected_name, raw in zip(expected_names, commands, strict=True):
        command = _mapping(raw, f"{label}_command:{expected_name}")
        if set(command) != required_fields or command["name"] != expected_name:
            raise R7ContractError(f"{label}_command_schema_or_order_mismatch:{expected_name}")
        if (
            command["return_code"] != 0
            or isinstance(command["return_code"], bool)
            or command["timed_out"] is not False
            or command["cancelled"] is not False
            or command["manual_intervention_required"] is not False
            or command["active_process_zero"] is not True
            or command["final_active_process_count"] != 0
            or isinstance(command["final_active_process_count"], bool)
            or command["forced_termination_attempts"] != 0
            or isinstance(command["forced_termination_attempts"], bool)
            or command["identity_coverage_complete"] is not True
            or command["job_limit_flags"] != 0
            or isinstance(command["job_limit_flags"], bool)
            or command["stdout_drained"] is not True
            or command["stderr_drained"] is not True
            or command["streams_drained"] is not True
            or command["safe_for_followup"] is not True
            or command["safe_for_followup_gate"] is not True
        ):
            raise R7ContractError(f"{label}_unsafe_command:{expected_name}")
        duration = command["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or duration < 0
        ):
            raise R7ContractError(f"{label}_command_duration_invalid:{expected_name}")
        started = _observation_timestamp(
            command["started_at_utc"], f"{label}_command_started:{expected_name}"
        )
        ended = _observation_timestamp(
            command["ended_at_utc"], f"{label}_command_ended:{expected_name}"
        )
        if ended < started:
            raise R7ContractError(f"{label}_command_time_order_invalid:{expected_name}")
        wall_duration = (ended - started).total_seconds()
        if abs(wall_duration - float(duration)) > 0.25:
            raise R7ContractError(f"{label}_command_duration_readback_mismatch:{expected_name}")
        run_uuid = _uuid(command["run_uuid"], f"{label}_command_run_uuid:{expected_name}")
        argv = _sequence(command["command"], f"{label}_command_argv:{expected_name}")
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise R7ContractError(f"{label}_command_argv_invalid:{expected_name}")
        argv_sha256 = _normalized_observation_argv_sha256(
            expected_name, argv, f"{label}_command_argv:{expected_name}"
        )
        if argv_sha256 != expected_argv_sha256[expected_name]:
            raise R7ContractError(f"{label}_command_argv_mismatch:{expected_name}")
        if _sequence(command["errors"], f"{label}_command_errors:{expected_name}") or _sequence(
            command["residual_pids"], f"{label}_command_residual:{expected_name}"
        ):
            raise R7ContractError(f"{label}_command_error_or_residual:{expected_name}")
        _validate_stream_digest(command["stdout"], f"{label}_stdout:{expected_name}")
        _validate_stream_digest(command["stderr"], f"{label}_stderr:{expected_name}")

        accounting = _sequence(command["accounting"], f"{label}_accounting:{expected_name}")
        if not accounting:
            raise R7ContractError(f"{label}_accounting_empty:{expected_name}")
        accounting_sequences: list[int] = []
        accounting_events: list[tuple[int, int, datetime]] = []
        accounted_active_pids: set[int] = set()
        total_process_counts: list[int] = []
        for raw_accounting in accounting:
            item = _mapping(raw_accounting, f"{label}_accounting_item:{expected_name}")
            if set(item) != {
                "active_pids",
                "active_processes",
                "monotonic_ns",
                "sequence",
                "timestamp_utc",
                "total_processes",
                "total_terminated_processes",
            }:
                raise R7ContractError(f"{label}_accounting_fields_mismatch:{expected_name}")
            active_pids = _sequence(
                item["active_pids"], f"{label}_accounting_active_pids:{expected_name}"
            )
            for pid in active_pids:
                if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
                    raise R7ContractError(f"{label}_accounting_pid_invalid:{expected_name}")
                accounted_active_pids.add(pid)
            for accounting_field in (
                "active_processes",
                "monotonic_ns",
                "sequence",
                "total_processes",
                "total_terminated_processes",
            ):
                _nonnegative_int(
                    item[accounting_field],
                    f"{label}_accounting_{accounting_field}:{expected_name}",
                )
            if item["active_processes"] != len(active_pids):
                raise R7ContractError(f"{label}_accounting_active_count_mismatch:{expected_name}")
            accounting_time = _observation_timestamp(
                item["timestamp_utc"], f"{label}_accounting_timestamp:{expected_name}"
            )
            if accounting_time < started or accounting_time > ended:
                raise R7ContractError(f"{label}_accounting_outside_command_bracket:{expected_name}")
            accounting_sequences.append(item["sequence"])
            accounting_events.append((item["sequence"], item["monotonic_ns"], accounting_time))
            total_process_counts.append(item["total_processes"])
        if accounting_sequences != sorted(set(accounting_sequences)):
            raise R7ContractError(f"{label}_accounting_sequence_invalid:{expected_name}")
        final_accounting = _mapping(accounting[-1], f"{label}_final_accounting:{expected_name}")
        if final_accounting["active_processes"] != 0 or final_accounting["active_pids"] != []:
            raise R7ContractError(f"{label}_final_accounting_not_zero:{expected_name}")

        events = _sequence(command["events"], f"{label}_events:{expected_name}")
        if not events:
            raise R7ContractError(f"{label}_events_empty:{expected_name}")
        event_sequences: list[int] = []
        process_event_pids: set[int] = set()
        process_events: list[tuple[int, int, datetime]] = []
        for raw_event in events:
            event = _mapping(raw_event, f"{label}_event:{expected_name}")
            if set(event) != {
                "details",
                "event",
                "monotonic_ns",
                "pid",
                "sequence",
                "timestamp_utc",
            }:
                raise R7ContractError(f"{label}_event_fields_mismatch:{expected_name}")
            details = _mapping(event["details"], f"{label}_event_details:{expected_name}")
            if "run_uuid" in details and details["run_uuid"] != run_uuid:
                raise R7ContractError(f"{label}_event_run_uuid_mismatch:{expected_name}")
            _nonempty(event["event"], f"{label}_event_name:{expected_name}")
            _nonnegative_int(event["monotonic_ns"], f"{label}_event_monotonic:{expected_name}")
            _nonnegative_int(event["sequence"], f"{label}_event_sequence:{expected_name}")
            if event["pid"] is not None and (
                isinstance(event["pid"], bool)
                or not isinstance(event["pid"], int)
                or event["pid"] < 1
            ):
                raise R7ContractError(f"{label}_event_pid_invalid:{expected_name}")
            if event["pid"] is not None:
                process_event_pids.add(event["pid"])
            event_time = _observation_timestamp(
                event["timestamp_utc"], f"{label}_event_time:{expected_name}"
            )
            if event_time < started or event_time > ended:
                raise R7ContractError(f"{label}_event_outside_command_bracket:{expected_name}")
            event_sequences.append(event["sequence"])
            process_events.append((event["sequence"], event["monotonic_ns"], event_time))
        if event_sequences != sorted(set(event_sequences)):
            raise R7ContractError(f"{label}_event_sequence_invalid:{expected_name}")

        identities = _sequence(command["identities"], f"{label}_identities:{expected_name}")
        if not identities:
            raise R7ContractError(f"{label}_identity_evidence_empty:{expected_name}")
        observed_identity_keys: set[tuple[int, int]] = set()
        identity_pids: set[int] = set()
        for raw_identity in identities:
            identity = _mapping(raw_identity, f"{label}_identity:{expected_name}")
            if set(identity) != {
                "creation_time_ns",
                "creation_time_utc",
                "image",
                "observed_sequence",
                "pid",
                "ppid",
                "run_uuid",
            }:
                raise R7ContractError(f"{label}_identity_fields_mismatch:{expected_name}")
            _nonnegative_int(
                identity["creation_time_ns"], f"{label}_identity_creation_ns:{expected_name}"
            )
            _observation_timestamp(
                identity["creation_time_utc"], f"{label}_identity_creation_time:{expected_name}"
            )
            _nonempty(identity["image"], f"{label}_identity_image:{expected_name}")
            _nonnegative_int(
                identity["observed_sequence"], f"{label}_identity_sequence:{expected_name}"
            )
            if (
                isinstance(identity["pid"], bool)
                or not isinstance(identity["pid"], int)
                or identity["pid"] < 1
                or isinstance(identity["ppid"], bool)
                or not isinstance(identity["ppid"], int)
                or identity["ppid"] < 0
                or identity["run_uuid"] != run_uuid
            ):
                raise R7ContractError(f"{label}_identity_binding_invalid:{expected_name}")
            identity_key = (identity["pid"], identity["creation_time_ns"])
            if identity_key in observed_identity_keys:
                raise R7ContractError(f"{label}_duplicate_identity:{expected_name}")
            observed_identity_keys.add(identity_key)
            identity_pids.add(identity["pid"])
        if (
            max(total_process_counts) != len(observed_identity_keys)
            or not accounted_active_pids.issubset(identity_pids)
            or not process_event_pids.issubset(identity_pids)
        ):
            raise R7ContractError(f"{label}_identity_coverage_not_derived:{expected_name}")
        ordered_evidence = sorted((*accounting_events, *process_events), key=lambda item: item[0])
        if len({item[0] for item in ordered_evidence}) != len(ordered_evidence) or any(
            later[1] < earlier[1] or later[2] < earlier[2]
            for earlier, later in zip(ordered_evidence, ordered_evidence[1:])
        ):
            raise R7ContractError(f"{label}_process_event_sequence_invalid:{expected_name}")


def _validate_exact_query_sha256(value: Any, expected: Mapping[str, str], label: str) -> None:
    query_sha = _mapping(value, label)
    if set(query_sha) != set(expected):
        raise R7ContractError(f"{label}_fields_mismatch")
    normalized = {name: _full_sha256(query_sha[name], f"{label}_{name}") for name in expected}
    if normalized != dict(expected):
        raise R7ContractError(f"{label}_mismatch")


def _normalize_target_activity(value: Any, label: str) -> dict[str, Any]:
    row = _mapping(value, label)
    required = {
        "run_id",
        "status",
        "lifecycle_stage",
        "start_time",
        "end_time",
        "metric_count",
        "last_metric_timestamp",
        "parameter_count",
        "tag_count",
    }
    if set(row) != required:
        raise R7ContractError(f"{label}_fields_mismatch")
    identity = _mlflow_identity(
        {
            name: row[name]
            for name in ("run_id", "status", "lifecycle_stage", "start_time", "end_time")
        },
        f"{label}_identity",
    )
    if not identity["start_time"].isdigit():
        raise R7ContractError(f"{label}_start_time_epoch_millis_required")
    counts = {
        name: _nonnegative_int(row[name], f"{label}_{name}")
        for name in ("metric_count", "parameter_count", "tag_count")
    }
    last_metric_timestamp = row["last_metric_timestamp"]
    if not isinstance(last_metric_timestamp, str) or (
        last_metric_timestamp != "" and not last_metric_timestamp.isdigit()
    ):
        raise R7ContractError(f"{label}_last_metric_timestamp_invalid")
    if (counts["metric_count"] == 0) != (last_metric_timestamp == ""):
        raise R7ContractError(f"{label}_metric_timestamp_count_mismatch")
    return {**identity, **counts, "last_metric_timestamp": last_metric_timestamp}


def _validate_snapshot(
    payload: Mapping[str, Any],
    pin: Mapping[str, Any],
    *,
    ordinal: int,
    target_identity: Mapping[str, str],
) -> tuple[datetime, dict[str, Any]]:
    required = {
        "all_commands_safe",
        "automatic_retry_count",
        "captured_at",
        "command_count",
        "commands",
        "expected_command_count",
        "observed",
        "ordinal",
        "process_containment",
        "query_sha256",
        "read_only",
        "repository",
        "schema",
        "service_mutation_count",
        "source_revision",
        "stopped_after",
    }
    if set(payload) != required:
        raise R7ContractError(f"external_snapshot_exact_schema_mismatch:{ordinal}")
    captured_at = _validate_source_pin_readback(
        payload,
        pin,
        expected_schema=SNAPSHOT_SCHEMA,
        ordinal=ordinal,
        label=f"external_snapshot_{ordinal}",
    )
    if (
        payload["all_commands_safe"] is not True
        or payload["read_only"] is not True
        or payload["automatic_retry_count"] != 0
        or isinstance(payload["automatic_retry_count"], bool)
        or payload["service_mutation_count"] != 0
        or isinstance(payload["service_mutation_count"], bool)
        or payload["stopped_after"] is not None
    ):
        raise R7ContractError(f"external_snapshot_safety_contract_mismatch:{ordinal}")
    if payload["repository"] != SNAPSHOT_REPOSITORY:
        raise R7ContractError(f"external_snapshot_repository_mismatch:{ordinal}")
    containment = _mapping(
        payload["process_containment"], f"external_snapshot_process_containment:{ordinal}"
    )
    if dict(containment) != {
        "type": "windows_job_object",
        "create_suspended_before_assignment": True,
        "kill_on_job_close": False,
        "terminate_job_object_calls": 0,
        "forced_termination_attempts": 0,
    }:
        raise R7ContractError(f"external_snapshot_process_containment_mismatch:{ordinal}")
    _validate_observation_commands(
        payload,
        SNAPSHOT_COMMAND_NAMES,
        SNAPSHOT_ARGV_SHA256,
        f"external_snapshot:{ordinal}",
    )
    _validate_exact_query_sha256(
        payload["query_sha256"], SNAPSHOT_QUERY_SHA256, f"external_snapshot_query_sha256:{ordinal}"
    )
    observed = _mapping(payload["observed"], f"external_snapshot_observed:{ordinal}")
    if set(observed) != {
        "compose_project_containers",
        "control_plane_execution_links",
        "control_plane_history",
        "kubernetes_failed_pods",
        "kubernetes_jobs",
        "mlflow_activity",
        "queue_claims",
        "windows_global_residuals",
        "wsl_global_residuals",
    }:
        raise R7ContractError(f"external_snapshot_observed_fields_mismatch:{ordinal}")
    compose_rows = _sequence(
        observed["compose_project_containers"], f"external_snapshot_compose_rows:{ordinal}"
    )
    for raw in compose_rows:
        row = _mapping(raw, f"external_snapshot_compose_row:{ordinal}")
        if set(row) != {"id", "image", "labels", "name", "state", "status"}:
            raise R7ContractError(f"external_snapshot_compose_row_fields_mismatch:{ordinal}")
        for name in ("id", "image", "labels", "name", "state", "status"):
            if not isinstance(row[name], str):
                raise R7ContractError(f"external_snapshot_compose_row_type_mismatch:{ordinal}")
    execution_rows = _sequence(
        observed["control_plane_execution_links"],
        f"external_snapshot_control_plane_execution_links:{ordinal}",
    )
    for raw in execution_rows:
        row = _mapping(raw, f"external_snapshot_control_plane_execution_link:{ordinal}")
        if set(row) != {
            "active_claim_count",
            "active_job_count",
            "active_lease_count",
            "entity_id",
            "outcome_unknown_count",
        }:
            raise R7ContractError(
                f"external_snapshot_control_plane_execution_link_fields_mismatch:{ordinal}"
            )
        _nonempty(row["entity_id"], f"external_snapshot_execution_entity_id:{ordinal}")
        for name in (
            "active_claim_count",
            "active_job_count",
            "active_lease_count",
            "outcome_unknown_count",
        ):
            _nonnegative_int(row[name], f"external_snapshot_execution_{name}:{ordinal}")
    history_rows = _sequence(
        observed["control_plane_history"], f"external_snapshot_control_plane_history:{ordinal}"
    )
    for raw in history_rows:
        row = _mapping(raw, f"external_snapshot_control_plane_history_row:{ordinal}")
        if set(row) != {"created_at", "entity_id", "state", "updated_at"}:
            raise R7ContractError(f"external_snapshot_history_row_fields_mismatch:{ordinal}")
        _nonempty(row["entity_id"], f"external_snapshot_history_entity_id:{ordinal}")
        _nonempty(row["state"], f"external_snapshot_history_state:{ordinal}")
        _utc_timestamp(row["created_at"], f"external_snapshot_history_created:{ordinal}")
        _utc_timestamp(row["updated_at"], f"external_snapshot_history_updated:{ordinal}")
    failed_pods = _sequence(
        observed["kubernetes_failed_pods"], f"external_snapshot_failed_pods:{ordinal}"
    )
    for raw in failed_pods:
        pod = _mapping(raw, f"external_snapshot_failed_pod:{ordinal}")
        if set(pod) != {
            "containers",
            "name",
            "namespace",
            "owners",
            "phase",
            "status_reason",
            "uid",
        }:
            raise R7ContractError(f"external_snapshot_failed_pod_fields_mismatch:{ordinal}")
        _uuid(pod["uid"], f"external_snapshot_failed_pod_uid:{ordinal}")
        for name in ("name", "namespace", "phase"):
            _nonempty(pod[name], f"external_snapshot_failed_pod_{name}:{ordinal}")
        if not isinstance(pod["status_reason"], str):
            raise R7ContractError(f"external_snapshot_failed_pod_status_reason_type:{ordinal}")
        _sequence(pod["containers"], f"external_snapshot_failed_pod_containers:{ordinal}")
        owners = _sequence(pod["owners"], f"external_snapshot_failed_pod_owners:{ordinal}")
        for raw_owner in owners:
            owner = _mapping(raw_owner, f"external_snapshot_failed_pod_owner:{ordinal}")
            if set(owner) != {"controller", "kind", "name", "uid"}:
                raise R7ContractError(
                    f"external_snapshot_failed_pod_owner_fields_mismatch:{ordinal}"
                )
            if owner["controller"] is not True:
                raise R7ContractError(f"external_snapshot_failed_pod_controller_required:{ordinal}")
            _uuid(owner["uid"], f"external_snapshot_failed_pod_owner_uid:{ordinal}")
            _nonempty(owner["kind"], f"external_snapshot_failed_pod_owner_kind:{ordinal}")
            _nonempty(owner["name"], f"external_snapshot_failed_pod_owner_name:{ordinal}")
        if any(_mapping(owner, "failed_pod_owner").get("kind") == "ReplicaSet" for owner in owners):
            _nonempty(pod["status_reason"], f"external_snapshot_failed_pod_status_reason:{ordinal}")
    jobs = _sequence(observed["kubernetes_jobs"], f"external_snapshot_kubernetes_jobs:{ordinal}")
    for raw in jobs:
        job = _mapping(raw, f"external_snapshot_kubernetes_job:{ordinal}")
        if set(job) != {
            "active",
            "conditions",
            "failed",
            "name",
            "namespace",
            "succeeded",
            "uid",
        }:
            raise R7ContractError(f"external_snapshot_kubernetes_job_fields_mismatch:{ordinal}")
        for name in ("active", "failed", "succeeded"):
            _nonnegative_int(job[name], f"external_snapshot_kubernetes_job_{name}:{ordinal}")
        for name in ("name", "namespace"):
            _nonempty(job[name], f"external_snapshot_kubernetes_job_{name}:{ordinal}")
        _uuid(job["uid"], f"external_snapshot_kubernetes_job_uid:{ordinal}")
        for raw_condition in _sequence(
            job["conditions"], f"external_snapshot_kubernetes_job_conditions:{ordinal}"
        ):
            condition = _mapping(
                raw_condition, f"external_snapshot_kubernetes_job_condition:{ordinal}"
            )
            if set(condition) != {"last_transition_time", "reason", "status", "type"}:
                raise R7ContractError(
                    f"external_snapshot_kubernetes_job_condition_fields_mismatch:{ordinal}"
                )
            for name in condition:
                _nonempty(
                    condition[name], f"external_snapshot_kubernetes_job_condition_{name}:{ordinal}"
                )
    queue = _mapping(observed["queue_claims"], f"external_snapshot_queue_claims:{ordinal}")
    if set(queue) != {"active", "active_claims", "leased", "outcome_unknown", "unknown_state"}:
        raise R7ContractError(f"external_snapshot_queue_claims_fields_mismatch:{ordinal}")
    for name in queue:
        _nonnegative_int(queue[name], f"external_snapshot_queue_claims_{name}:{ordinal}")
    _sequence(
        observed["windows_global_residuals"], f"external_snapshot_windows_residuals:{ordinal}"
    )
    _sequence(observed["wsl_global_residuals"], f"external_snapshot_wsl_residuals:{ordinal}")
    rows = _sequence(
        observed.get("mlflow_activity"),
        f"external_snapshot_mlflow_activity:{ordinal}",
    )
    matches: list[dict[str, Any]] = []
    for raw in rows:
        row = _mapping(raw, f"external_snapshot_mlflow_row:{ordinal}")
        normalized_activity = _normalize_target_activity(
            row, f"external_snapshot_mlflow_row:{ordinal}"
        )
        if str(row["run_id"]) == target_identity["run_id"]:
            matches.append(normalized_activity)
    if len(matches) != 1 or {name: matches[0][name] for name in target_identity} != dict(
        target_identity
    ):
        raise R7ContractError(f"external_snapshot_exact_target_identity_required:{ordinal}")
    return captured_at, matches[0]


def _zero_table_matches(
    value: Any,
    label: str,
    *,
    expected_tables: tuple[str, ...],
    match_fields: frozenset[str],
) -> None:
    rows = _sequence(value, label)
    if tuple(str(_mapping(raw, label).get("table", "")) for raw in rows) != expected_tables:
        raise R7ContractError(f"{label}_table_set_or_order_mismatch")
    for raw in rows:
        row = _mapping(raw, label)
        if set(row) != {"table", *match_fields}:
            raise R7ContractError(f"{label}_fields_mismatch")
        for name in match_fields:
            count = row[name]
            if isinstance(count, bool) or not isinstance(count, int) or count != 0:
                raise R7ContractError(f"{label}_exact_zero_matches_required")


def _zero_inventory_matches(value: Any, label: str, *, observed_count_required: bool) -> None:
    item = _mapping(value, label)
    required = {"matches", "matching_count"}
    if observed_count_required:
        required.add("observed_count")
    if set(item) != required:
        raise R7ContractError(f"{label}_fields_mismatch")
    if (
        _sequence(item["matches"], f"{label}_matches")
        or item["matching_count"] != 0
        or isinstance(item["matching_count"], bool)
    ):
        raise R7ContractError(f"{label}_exact_zero_matches_required")
    if observed_count_required and (
        isinstance(item["observed_count"], bool)
        or not isinstance(item["observed_count"], int)
        or item["observed_count"] < 0
    ):
        raise R7ContractError(f"{label}_observed_count_invalid")


def _validate_link_scan(
    payload: Mapping[str, Any],
    pin: Mapping[str, Any],
    *,
    ordinal: int,
    target_run_id: str,
) -> datetime:
    required_payload = {
        "all_commands_safe",
        "all_exact_links_zero",
        "automatic_retry_count",
        "captured_at",
        "command_count",
        "commands",
        "expected_command_count",
        "forced_termination_attempts",
        "observed",
        "ordinal",
        "query_sha256",
        "read_only",
        "schema",
        "service_mutation_count",
        "source_revision",
        "stopped_after",
        "target_run_id",
    }
    if set(payload) != required_payload:
        raise R7ContractError(f"external_link_scan_exact_schema_mismatch:{ordinal}")
    captured_at = _validate_source_pin_readback(
        payload,
        pin,
        expected_schema=LINK_SCAN_SCHEMA,
        ordinal=ordinal,
        label=f"external_link_scan_{ordinal}",
    )
    if (
        payload["target_run_id"] != target_run_id
        or payload["all_commands_safe"] is not True
        or payload["all_exact_links_zero"] is not True
        or payload["read_only"] is not True
        or payload["automatic_retry_count"] != 0
        or isinstance(payload["automatic_retry_count"], bool)
        or payload["service_mutation_count"] != 0
        or isinstance(payload["service_mutation_count"], bool)
        or payload["forced_termination_attempts"] != 0
        or isinstance(payload["forced_termination_attempts"], bool)
        or payload["stopped_after"] is not None
    ):
        raise R7ContractError(f"external_link_scan_safety_contract_mismatch:{ordinal}")
    _validate_observation_commands(
        payload,
        LINK_SCAN_COMMAND_NAMES,
        LINK_SCAN_ARGV_SHA256,
        f"external_link_scan:{ordinal}",
    )
    _validate_exact_query_sha256(
        payload["query_sha256"],
        LINK_SCAN_QUERY_SHA256,
        f"external_link_scan_query_sha256:{ordinal}",
    )
    observed = _mapping(payload["observed"], f"external_link_scan_observed:{ordinal}")
    required = {
        "control_plane_run_links",
        "airflow_run_links",
        "docker_run_links",
        "kubernetes_run_links",
        "windows_run_links",
        "wsl_run_links",
    }
    if set(observed) != required:
        raise R7ContractError(f"external_link_scan_observed_fields_mismatch:{ordinal}")
    _zero_table_matches(
        observed["control_plane_run_links"],
        "control_plane_run_links",
        expected_tables=(
            "entities",
            "idempotency_keys",
            "lifecycle_claims",
            "s6bm_causal_events",
            "s6bm_route_revisions",
            "side_effect_outbox",
            "task_admission_queue",
            "task_dispatch_effects",
        ),
        match_fields=frozenset({"identity_matches", "payload_matches"}),
    )
    _zero_table_matches(
        observed["airflow_run_links"],
        "airflow_run_links",
        expected_tables=(
            "dag_run",
            "rendered_task_instance_fields",
            "task_instance",
            "xcom",
        ),
        match_fields=frozenset({"identity_matches", "payload_matches", "active_matches"}),
    )
    _zero_inventory_matches(
        observed["docker_run_links"],
        "docker_run_links",
        observed_count_required=True,
    )
    _zero_inventory_matches(
        observed["kubernetes_run_links"],
        "kubernetes_run_links",
        observed_count_required=True,
    )
    _zero_inventory_matches(
        observed["windows_run_links"],
        "windows_run_links",
        observed_count_required=False,
    )
    _zero_inventory_matches(
        observed["wsl_run_links"],
        "wsl_run_links",
        observed_count_required=False,
    )
    return captured_at


def _identity_sha256(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(identity))).hexdigest()


def validate_external_terminal_fencing(
    value: Any,
    *,
    verify_files: bool,
    expected_trusted_checkpoint_sha256: str | None,
    expected_successor_binding: Mapping[str, Any] | None = None,
    manifest_created_at: str | None = None,
    validation_time: datetime | None = None,
) -> dict[str, Any]:
    contract = _mapping(value, "external_terminal_fencing")
    required = {
        "target_source",
        "target_identity",
        "successor_binding",
        "decision_authority",
        "snapshots",
        "exact_link_scans",
        "terminal_decision",
        "trusted_checkpoint",
    }
    if set(contract) != required:
        raise R7ContractError("external_terminal_fencing_fields_mismatch")
    if contract["target_source"] != "mlflow_running_rows":
        raise R7ContractError("external_terminal_fencing_source_mismatch")
    identity = _mlflow_identity(
        contract["target_identity"], "external_terminal_fencing_target_identity"
    )
    binding = _successor_binding(
        contract["successor_binding"], "external_terminal_fencing_successor_binding"
    )
    if expected_successor_binding is not None and binding != _successor_binding(
        expected_successor_binding, "expected_successor_binding"
    ):
        raise R7ContractError("external_terminal_fencing_successor_binding_mismatch")
    if contract["decision_authority"] != EXTERNAL_DECISION_AUTHORITY:
        raise R7ContractError("external_terminal_fencing_authority_mismatch")
    snapshots = [
        _source_pin(raw, f"external_snapshot_pin:{index}")
        for index, raw in enumerate(
            _sequence(contract["snapshots"], "external_snapshot_pins"), start=1
        )
    ]
    scans = [
        _source_pin(raw, f"external_link_scan_pin:{index}")
        for index, raw in enumerate(
            _sequence(contract["exact_link_scans"], "external_link_scan_pins"),
            start=1,
        )
    ]
    if len(snapshots) != 2:
        raise R7ContractError("external_terminal_fencing_exactly_two_snapshots_required")
    if len(scans) != 2:
        raise R7ContractError("external_terminal_fencing_exactly_two_link_scans_required")
    for ordinal, pin in enumerate(snapshots, start=1):
        if pin["schema"] != SNAPSHOT_SCHEMA or pin["ordinal"] != ordinal:
            raise R7ContractError(f"external_snapshot_pin_schema_or_ordinal_mismatch:{ordinal}")
    for ordinal, pin in enumerate(scans, start=1):
        if pin["schema"] != LINK_SCAN_SCHEMA or pin["ordinal"] != ordinal:
            raise R7ContractError(f"external_link_scan_pin_schema_or_ordinal_mismatch:{ordinal}")
    source_revisions = {pin["source_revision"] for pin in (*snapshots, *scans)}
    if source_revisions != {OBSERVATION_SOURCE_REVISION}:
        raise R7ContractError("external_observation_source_revision_mismatch")
    decision_pin = (
        None
        if contract["terminal_decision"] is None
        else _artifact_pin(
            contract["terminal_decision"],
            "external_terminal_decision_pin",
            TERMINAL_FENCING_DECISION_SCHEMA,
        )
    )
    checkpoint_pin = (
        None
        if contract["trusted_checkpoint"] is None
        else _artifact_pin(
            contract["trusted_checkpoint"],
            "external_trusted_checkpoint_pin",
            TRUSTED_CHECKPOINT_SCHEMA,
        )
    )
    if (decision_pin is None) != (checkpoint_pin is None):
        raise R7ContractError(
            "external_terminal_decision_and_checkpoint_must_be_both_present_or_absent"
        )
    all_paths = [pin["path"] for pin in (*snapshots, *scans)]
    if decision_pin is not None and checkpoint_pin is not None:
        all_paths.extend((decision_pin["path"], checkpoint_pin["path"]))
    if len(set(all_paths)) != len(all_paths):
        raise R7ContractError("external_terminal_fencing_paths_must_be_distinct")
    result = {
        "source": "mlflow_running_rows",
        "identity": identity,
        "successor_binding": binding,
        "decision": "unproven",
        "authority": EXTERNAL_DECISION_AUTHORITY,
        "verified": False,
        "trust_reason": "trusted_checkpoint_absent_or_not_independently_approved",
        "trust_model": (
            "caller_supplied_out_of_band_sha256_is_approval_tcb;"
            "independent_approval_metadata_is_not_cryptographic_reviewer_authentication"
        ),
        "artifact": decision_pin,
        "trusted_checkpoint": checkpoint_pin,
        "snapshots": snapshots,
        "exact_link_scans": scans,
    }
    if not verify_files:
        return result
    snapshot_results = [
        _validate_snapshot(
            _read_pinned_json(pin, f"external_snapshot:{index}"),
            pin,
            ordinal=index,
            target_identity=identity,
        )
        for index, pin in enumerate(snapshots, start=1)
    ]
    snapshot_times = [item[0] for item in snapshot_results]
    target_activities = [item[1] for item in snapshot_results]
    if target_activities[0] != target_activities[1]:
        raise R7ContractError("external_snapshot_target_activity_changed")
    result["target_activity"] = target_activities
    scan_times = [
        _validate_link_scan(
            _read_pinned_json(pin, f"external_link_scan:{index}"),
            pin,
            ordinal=index,
            target_run_id=identity["run_id"],
        )
        for index, pin in enumerate(scans, start=1)
    ]
    times = [*snapshot_times, *scan_times]
    if (snapshot_times[1] - snapshot_times[0]).total_seconds() < MIN_OBSERVATION_GAP_SECONDS:
        raise R7ContractError("external_snapshot_minimum_gap_not_met")
    if (scan_times[1] - scan_times[0]).total_seconds() < MIN_OBSERVATION_GAP_SECONDS:
        raise R7ContractError("external_link_scan_minimum_gap_not_met")
    latest_observation = max(times)
    oldest_observation = min(times)
    if manifest_created_at is not None:
        created_at = _utc_timestamp(
            manifest_created_at, "external_terminal_fencing_manifest_created_at"
        )
        if created_at < latest_observation:
            raise R7ContractError("manifest_created_before_external_observations_completed")
        if (created_at - oldest_observation).total_seconds() > MAX_OBSERVATION_AGE_SECONDS:
            raise R7ContractError("external_observations_stale_at_manifest_creation")
    checked_at = validation_time or datetime.now(UTC)
    if checked_at.tzinfo is None:
        raise R7ContractError("external_validation_time_timezone_required")
    checked_at = checked_at.astimezone(UTC)
    if checked_at < latest_observation:
        raise R7ContractError("external_observation_from_future")
    if (checked_at - oldest_observation).total_seconds() > MAX_OBSERVATION_AGE_SECONDS:
        raise R7ContractError("external_observations_runtime_max_age_exceeded")
    if decision_pin is None or checkpoint_pin is None:
        return result

    decision = _read_pinned_json(decision_pin, "external_terminal_decision")
    required_decision = {
        "schema",
        "target_source",
        "target_identity",
        "successor_binding",
        "decision",
        "decision_authority",
        "issued_at",
        "future_dispatch_fenced",
        "supporting_sha256",
    }
    if set(decision) != required_decision:
        raise R7ContractError("external_terminal_decision_fields_mismatch")
    if decision["schema"] != TERMINAL_FENCING_DECISION_SCHEMA:
        raise R7ContractError("external_terminal_decision_schema_mismatch")
    if (
        decision["target_source"] != "mlflow_running_rows"
        or _mlflow_identity(
            decision["target_identity"], "external_terminal_decision_target_identity"
        )
        != identity
        or _successor_binding(decision["successor_binding"], "external_terminal_decision_binding")
        != binding
    ):
        raise R7ContractError("external_terminal_decision_target_or_binding_mismatch")
    if decision["decision"] != "proven_terminal_fenced":
        raise R7ContractError("external_terminal_decision_exact_value_required")
    if decision["decision_authority"] != EXTERNAL_DECISION_AUTHORITY:
        raise R7ContractError("external_terminal_decision_authority_mismatch")
    if decision["future_dispatch_fenced"] is not True:
        raise R7ContractError("external_terminal_decision_future_dispatch_fence_required")
    expected_support = {
        "historical_snapshot_1": snapshots[0]["sha256"],
        "historical_snapshot_2": snapshots[1]["sha256"],
        "exact_link_scan_1": scans[0]["sha256"],
        "exact_link_scan_2": scans[1]["sha256"],
        "successor_binding_sha256": hashlib.sha256(canonical_json_bytes(binding)).hexdigest(),
        "historical_snapshot_1_target_activity_sha256": hashlib.sha256(
            canonical_json_bytes(target_activities[0])
        ).hexdigest(),
        "historical_snapshot_2_target_activity_sha256": hashlib.sha256(
            canonical_json_bytes(target_activities[1])
        ).hexdigest(),
    }
    if dict(_mapping(decision["supporting_sha256"], "supporting_sha256")) != expected_support:
        raise R7ContractError("external_terminal_decision_support_chain_mismatch")
    issued_at = _utc_timestamp(decision["issued_at"], "external_terminal_decision_issued_at")
    if issued_at <= latest_observation:
        raise R7ContractError("external_terminal_decision_must_follow_all_observations")

    checkpoint = _read_pinned_json(checkpoint_pin, "external_trusted_checkpoint")
    required_checkpoint = {
        "schema",
        "checkpointed_at",
        "expires_at",
        "decision_authority",
        "independent_approval",
        "successor_binding",
        "target_source",
        "target_identity_sha256",
        "decision_sha256",
        "supporting_sha256",
        "fence_readback",
    }
    if set(checkpoint) != required_checkpoint:
        raise R7ContractError("external_trusted_checkpoint_fields_mismatch")
    if checkpoint["schema"] != TRUSTED_CHECKPOINT_SCHEMA:
        raise R7ContractError("external_trusted_checkpoint_schema_mismatch")
    approval = _mapping(
        checkpoint["independent_approval"], "external_checkpoint_independent_approval"
    )
    if set(approval) != {"source", "reviewer_identity", "approval_id"}:
        raise R7ContractError("external_checkpoint_approval_fields_mismatch")
    for name in approval:
        _nonempty(approval[name], f"external_checkpoint_approval_{name}")
    fence = _mapping(checkpoint["fence_readback"], "external_checkpoint_fence_readback")
    if set(fence) != {
        "target_run_id",
        "future_dispatch_fenced",
        "fence_state",
        "read_back_at",
    }:
        raise R7ContractError("external_checkpoint_fence_readback_fields_mismatch")
    fence_read_at = _utc_timestamp(fence["read_back_at"], "external_checkpoint_fence_read_at")
    if (
        fence["target_run_id"] != identity["run_id"]
        or fence["future_dispatch_fenced"] is not True
        or fence["fence_state"] != "fenced"
        or fence_read_at <= latest_observation
    ):
        raise R7ContractError("external_checkpoint_fence_readback_mismatch")
    if (
        checkpoint["decision_authority"] != EXTERNAL_DECISION_AUTHORITY
        or checkpoint["target_source"] != "mlflow_running_rows"
        or _successor_binding(
            checkpoint["successor_binding"], "external_checkpoint_successor_binding"
        )
        != binding
        or checkpoint["target_identity_sha256"] != _identity_sha256(identity)
        or checkpoint["decision_sha256"] != decision_pin["sha256"]
        or dict(
            _mapping(
                checkpoint["supporting_sha256"],
                "external_checkpoint_supporting_sha256",
            )
        )
        != expected_support
    ):
        raise R7ContractError("external_trusted_checkpoint_binding_mismatch")
    checkpointed_at = _utc_timestamp(
        checkpoint["checkpointed_at"], "external_checkpoint_checkpointed_at"
    )
    expires_at = _utc_timestamp(checkpoint["expires_at"], "external_checkpoint_expires_at")
    if checkpointed_at < max(issued_at, fence_read_at) or expires_at <= checkpointed_at:
        raise R7ContractError("external_trusted_checkpoint_time_contract_mismatch")
    if checked_at < checkpointed_at:
        raise R7ContractError("external_trusted_checkpoint_from_future")
    if checked_at > expires_at:
        raise R7ContractError("external_trusted_checkpoint_expired")
    if expected_trusted_checkpoint_sha256 is None:
        return result
    trusted_sha = _full_sha256(
        expected_trusted_checkpoint_sha256,
        "expected_trusted_checkpoint_sha256",
    )
    if trusted_sha != checkpoint_pin["sha256"]:
        raise R7ContractError("external_trusted_checkpoint_out_of_band_sha_mismatch")
    result.update(
        {
            "decision": "proven_terminal_fenced",
            "verified": True,
            "trust_reason": "independently_approved_out_of_band_checkpoint_sha256",
            "issued_at": decision["issued_at"],
            "checkpointed_at": checkpoint["checkpointed_at"],
            "expires_at": checkpoint["expires_at"],
            "independent_approval": dict(approval),
        }
    )
    return result


def find_verified_decision(
    validated_manifest: Mapping[str, Any],
    source: str,
    identity: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    normalized = _mlflow_identity(identity, "decision_lookup_identity")
    for raw in _sequence(
        validated_manifest.get("historical_decisions", ()), "historical_decisions"
    ):
        decision = _mapping(raw, "historical_decision")
        if (
            decision.get("source") == source
            and decision.get("identity") == normalized
            and decision.get("verified") is True
            and decision.get("decision") == "proven_terminal_fenced"
            and decision.get("trust_reason")
            == "independently_approved_out_of_band_checkpoint_sha256"
        ):
            return decision
    return None


def _attestation_time(value: Any, source: str) -> str:
    captured_at = _nonempty(value, f"historical_attestation_captured_at:{source}")
    if not captured_at.endswith("Z"):
        raise R7ContractError(f"historical_attestation_utc_timestamp_required:{source}")
    try:
        parsed = datetime.fromisoformat(captured_at[:-1] + "+00:00")
    except ValueError as exc:
        raise R7ContractError(f"historical_attestation_timestamp_invalid:{source}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise R7ContractError(f"historical_attestation_utc_timestamp_required:{source}")
    return captured_at


def _validate_historical_attestation(
    *,
    source: str,
    manifest_item: Mapping[str, Any],
    attestation: Mapping[str, Any],
    attestation_path: Path,
    expected_kubernetes_uids: frozenset[str],
    proof_paths: set[Path],
    external_decision: Mapping[str, Any],
) -> None:
    required = {
        "source",
        "captured_at",
        "query_sha256",
        "counts",
        "classification",
        "records",
    }
    if set(attestation) != required or attestation["source"] != source:
        raise R7ContractError(f"historical_attestation_source_or_fields_mismatch:{source}")
    _attestation_time(attestation["captured_at"], source)
    query_sha = _full_sha256(attestation["query_sha256"], f"historical_attestation_query:{source}")
    if query_sha != HISTORICAL_QUERY_SHA256[source]:
        raise R7ContractError(f"historical_attestation_canonical_query_mismatch:{source}")
    count_names = (
        "observed_count",
        "executing_count",
        "historical_count",
        "unproven_count",
    )
    raw_counts = _mapping(attestation["counts"], f"historical_attestation_counts:{source}")
    if set(raw_counts) != set(count_names):
        raise R7ContractError(f"historical_attestation_count_fields_mismatch:{source}")
    counts: dict[str, int] = {}
    for name in count_names:
        raw = raw_counts[name]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise R7ContractError(f"historical_attestation_count_invalid:{source}:{name}")
        counts[name] = raw
        if raw != manifest_item[name]:
            raise R7ContractError(f"historical_attestation_manifest_count_mismatch:{source}:{name}")
    if attestation["classification"] != manifest_item["classification"]:
        raise R7ContractError(f"historical_attestation_classification_mismatch:{source}")

    records = _sequence(attestation["records"], f"historical_attestation_records:{source}")
    if len(records) != counts["observed_count"]:
        raise R7ContractError(f"historical_attestation_record_count_mismatch:{source}")
    identities: set[str] = set()
    kubernetes_uids: set[str] = set()
    derived = {"executing": 0, "historical_nonexecuting": 0, "unproven": 0}
    proof_count_names = (
        "active_job_count",
        "active_claim_count",
        "active_lease_count",
        "outcome_unknown_count",
    )
    for raw_record in records:
        record = _mapping(raw_record, f"historical_attestation_record:{source}")
        if source == "mlflow_running_rows" and set(record) == {
            "identity",
            "observed_state",
            "classification",
        }:
            identity = _mlflow_identity(
                record["identity"], "historical_attestation_mlflow_identity"
            )
            if record["observed_state"] != "RUNNING":
                raise R7ContractError("historical_attestation_mlflow_observed_state_mismatch")
            classification = str(record["classification"])
            exact_external = (
                external_decision.get("verified") is True
                and external_decision.get("identity") == identity
            )
            required_classification = "historical_nonexecuting" if exact_external else "unproven"
            if classification != required_classification:
                raise R7ContractError(
                    "mlflow_running_row_requires_verified_terminal_fencing_decision"
                )
            stable_identity = json.dumps(
                identity,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            if stable_identity in identities:
                raise R7ContractError(
                    "historical_attestation_record_identity_duplicate:mlflow_running_rows"
                )
            identities.add(stable_identity)
            derived[classification] += 1
            continue
        if set(record) != {
            "identity",
            "observed_state",
            "classification",
            "execution_proof",
        }:
            raise R7ContractError(f"historical_attestation_record_fields_mismatch:{source}")
        identity = _mapping(record["identity"], f"historical_attestation_identity:{source}")
        if source == "control_plane_task_entity_statuses":
            if set(identity) != {"entity_id", "created_at", "updated_at"}:
                raise R7ContractError(
                    "historical_attestation_control_plane_identity_fields_mismatch"
                )
            _nonempty(identity["entity_id"], "historical_attestation_control_plane_entity_id")
            _nonempty(identity["created_at"], "historical_attestation_control_plane_created_at")
            _nonempty(identity["updated_at"], "historical_attestation_control_plane_updated_at")
        elif source == "mlflow_running_rows":
            identity = _mlflow_identity(identity, "historical_attestation_mlflow_identity")
        else:
            identity = _typed_failed_pod(identity, "historical_attestation_kubernetes_identity")
            uid = identity["uid"]
            kubernetes_uids.add(uid)
        stable_identity = json.dumps(
            dict(identity),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if stable_identity in identities:
            raise R7ContractError(f"historical_attestation_record_identity_duplicate:{source}")
        identities.add(stable_identity)
        _nonempty(record["observed_state"], f"historical_attestation_observed_state:{source}")
        classification = str(record["classification"])
        if source == "mlflow_running_rows":
            if record["observed_state"] != "RUNNING":
                raise R7ContractError("historical_attestation_mlflow_observed_state_mismatch")
            exact_external = external_decision.get("verified") is True and external_decision.get(
                "identity"
            ) == dict(identity)
            required_classification = "historical_nonexecuting" if exact_external else "unproven"
            if classification != required_classification:
                raise R7ContractError(
                    "mlflow_running_row_requires_verified_terminal_fencing_decision"
                )
        if classification not in derived:
            raise R7ContractError(f"historical_attestation_record_classification_invalid:{source}")
        proof = _mapping(
            record["execution_proof"], f"historical_attestation_execution_proof:{source}"
        )
        if set(proof) != {"inactivity_proven", *proof_count_names, "evidence"}:
            raise R7ContractError(
                f"historical_attestation_execution_proof_fields_mismatch:{source}"
            )
        if not isinstance(proof["inactivity_proven"], bool):
            raise R7ContractError(
                f"historical_attestation_inactivity_proven_boolean_required:{source}"
            )
        proof_counts: dict[str, int] = {}
        for name in proof_count_names:
            raw = proof[name]
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise R7ContractError(f"historical_attestation_proof_count_invalid:{source}:{name}")
            proof_counts[name] = raw
        active_links = sum(proof_counts.values())
        evidence = _mapping(proof["evidence"], f"historical_attestation_proof_evidence:{source}")
        if set(evidence) != {"path", "sha256"}:
            raise R7ContractError(f"historical_attestation_proof_evidence_fields_mismatch:{source}")
        evidence_path = Path(
            _nonempty(evidence["path"], f"historical_attestation_proof_evidence_path:{source}")
        ).resolve()
        evidence_sha = _full_sha256(
            evidence["sha256"], f"historical_attestation_proof_evidence:{source}"
        )
        if evidence_path == attestation_path or evidence_path in proof_paths:
            raise R7ContractError("historical_attestation_proof_paths_must_be_distinct")
        proof_paths.add(evidence_path)
        if not evidence_path.is_file():
            raise R7ContractError(f"historical_attestation_proof_file_missing:{source}")
        try:
            proof_payload, measured_evidence_sha = _read_json_snapshot(evidence_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7ContractError(f"historical_attestation_proof_json_invalid:{source}") from exc
        if measured_evidence_sha != evidence_sha:
            raise R7ContractError(f"historical_attestation_proof_sha256_mismatch:{source}")
        proof_payload = _mapping(proof_payload, f"historical_attestation_proof_payload:{source}")
        required_proof_payload = {
            "source",
            "identity",
            "observed_state",
            "captured_at",
            "query_sha256",
            *proof_count_names,
            "inactivity_decision",
            "decision_authority",
        }
        if set(proof_payload) != required_proof_payload:
            raise R7ContractError(f"historical_attestation_proof_payload_fields_mismatch:{source}")
        if proof_payload["source"] != source:
            raise R7ContractError(f"historical_attestation_proof_source_mismatch:{source}")
        if dict(_mapping(proof_payload["identity"], "proof_identity")) != dict(identity):
            raise R7ContractError(f"historical_attestation_proof_identity_mismatch:{source}")
        if proof_payload["observed_state"] != record["observed_state"]:
            raise R7ContractError(f"historical_attestation_proof_observed_state_mismatch:{source}")
        _attestation_time(proof_payload["captured_at"], f"proof:{source}")
        if proof_payload["query_sha256"] != attestation["query_sha256"]:
            raise R7ContractError(f"historical_attestation_proof_query_mismatch:{source}")
        for name in proof_count_names:
            if proof_payload[name] != proof_counts[name]:
                raise R7ContractError(
                    f"historical_attestation_proof_count_mismatch:{source}:{name}"
                )
        expected_decision = (
            "executing"
            if active_links
            else "proven_inactive"
            if proof["inactivity_proven"] is True
            else "unproven"
        )
        if proof_payload["inactivity_decision"] != expected_decision:
            raise R7ContractError(f"historical_attestation_proof_decision_mismatch:{source}")
        if proof_payload["decision_authority"] != HISTORICAL_DECISION_AUTHORITY:
            raise R7ContractError(
                f"historical_attestation_proof_decision_authority_mismatch:{source}"
            )
        if classification == "historical_nonexecuting" and (
            proof["inactivity_proven"] is not True or active_links != 0
        ):
            raise R7ContractError(f"historical_attestation_inactivity_proof_required:{source}")
        if classification == "unproven" and (
            proof["inactivity_proven"] is not False or active_links != 0
        ):
            raise R7ContractError(f"historical_attestation_unproven_record_mismatch:{source}")
        if classification == "executing" and (
            active_links == 0 or proof["inactivity_proven"] is not False
        ):
            raise R7ContractError(f"historical_attestation_execution_proof_required:{source}")
        derived[classification] += 1

    if derived["executing"] != counts["executing_count"]:
        raise R7ContractError(f"historical_attestation_executing_records_mismatch:{source}")
    if derived["historical_nonexecuting"] != counts["historical_count"]:
        raise R7ContractError(f"historical_attestation_historical_records_mismatch:{source}")
    if derived["unproven"] != counts["unproven_count"]:
        raise R7ContractError(f"historical_attestation_unproven_records_mismatch:{source}")
    if source == "kubernetes_terminal_failed_objects" and kubernetes_uids != set(
        expected_kubernetes_uids
    ):
        raise R7ContractError("historical_attestation_kubernetes_identity_set_mismatch")


def _validate_job_scope(
    value: Any,
    *,
    verify_attestations: bool = False,
    expected_kubernetes_uids: frozenset[str] = frozenset(),
    external_decision: Mapping[str, Any] = {},
) -> dict[str, Any]:
    contract = _mapping(value, "job_scope_contract")
    required = {
        "canonical_active_jobs",
        "historical_observations",
        "historical_classifications",
    }
    if set(contract) != required:
        raise R7ContractError("job_scope_contract_fields_mismatch")
    for name in ("canonical_active_jobs", "historical_observations"):
        if dict(_mapping(contract[name], f"job_scope_{name}")) != JOB_SCOPE_CONTRACT[name]:
            raise R7ContractError(f"job_scope_{name}_mismatch")
    raw_classifications = _sequence(
        contract["historical_classifications"], "historical_classifications"
    )
    if len(raw_classifications) != len(HISTORICAL_CLASSIFICATION_SOURCES):
        raise R7ContractError("historical_classification_count_mismatch")
    normalized: list[dict[str, Any]] = []
    attestation_paths: set[Path] = set()
    proof_paths: set[Path] = set()
    for expected_source, raw in zip(
        HISTORICAL_CLASSIFICATION_SOURCES, raw_classifications, strict=True
    ):
        item = _mapping(raw, "historical_classification")
        required_item = {
            "source",
            "observed_count",
            "executing_count",
            "historical_count",
            "unproven_count",
            "classification",
            "attestation",
        }
        if set(item) != required_item or item["source"] != expected_source:
            raise R7ContractError("historical_classification_source_or_fields_mismatch")
        counts: dict[str, int] = {}
        for field_name in (
            "observed_count",
            "executing_count",
            "historical_count",
            "unproven_count",
        ):
            raw_count = item[field_name]
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise R7ContractError(
                    f"historical_classification_nonnegative_integer_required:{expected_source}"
                )
            counts[field_name] = raw_count
        if counts["observed_count"] != (
            counts["executing_count"] + counts["historical_count"] + counts["unproven_count"]
        ):
            raise R7ContractError(f"historical_classification_count_sum_mismatch:{expected_source}")
        classification = str(item["classification"])
        expected_classification = (
            "unproven"
            if counts["unproven_count"]
            else "executing"
            if counts["executing_count"]
            else "historical_nonexecuting"
        )
        if classification != expected_classification:
            raise R7ContractError(f"historical_classification_label_mismatch:{expected_source}")
        if (
            expected_source == "kubernetes_terminal_failed_objects"
            and counts["observed_count"] != 14
        ):
            raise R7ContractError("historical_failed_pod_classification_count_mismatch")
        attestation = _mapping(item["attestation"], "historical_classification_attestation")
        if set(attestation) != {"path", "sha256"}:
            raise R7ContractError("historical_classification_attestation_fields_mismatch")
        attestation_value = {
            "path": _nonempty(attestation["path"], "historical_attestation_path"),
            "sha256": _full_sha256(attestation["sha256"], "historical_attestation"),
        }
        attestation_path = Path(attestation_value["path"]).resolve()
        if attestation_path in attestation_paths:
            raise R7ContractError("historical_attestation_paths_must_be_distinct")
        attestation_paths.add(attestation_path)
        if verify_attestations:
            if not attestation_path.is_file():
                raise R7ContractError(f"historical_attestation_file_missing:{expected_source}")
            try:
                payload, measured_attestation_sha = _read_json_snapshot(attestation_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise R7ContractError(
                    f"historical_attestation_json_invalid:{expected_source}"
                ) from exc
            if measured_attestation_sha != attestation_value["sha256"]:
                raise R7ContractError(f"historical_attestation_sha256_mismatch:{expected_source}")
            _validate_historical_attestation(
                source=expected_source,
                manifest_item=item,
                attestation=_mapping(payload, f"historical_attestation:{expected_source}"),
                attestation_path=attestation_path,
                expected_kubernetes_uids=expected_kubernetes_uids,
                proof_paths=proof_paths,
                external_decision=external_decision,
            )
        normalized.append(
            {
                "source": expected_source,
                **counts,
                "classification": classification,
                "attestation": attestation_value,
            }
        )
    if proof_paths & attestation_paths:
        raise R7ContractError("historical_attestation_and_proof_paths_must_be_distinct")
    return {
        "canonical_active_jobs": dict(JOB_SCOPE_CONTRACT["canonical_active_jobs"]),
        "historical_observations": dict(JOB_SCOPE_CONTRACT["historical_observations"]),
        "historical_classifications": normalized,
    }


def _validate_expected_state(value: Any, revision: str, tree: str) -> dict[str, Any]:
    state = _mapping(value, "expected_state")
    required = {
        "compose",
        "api",
        "database",
        "kubernetes",
        "compose_services",
        "api_base_url",
        "b0",
        "prometheus_jobs",
        "prometheus_targets_url",
        "gpu_lease_path",
        "active_job_roots",
        "active_claim_roots",
        "x1_residue_paths",
        "x1_docker_name_filter",
        "x1_ports",
        "x1_kubernetes_selectors",
    }
    if set(state) != required:
        raise R7ContractError("expected_state_fields_mismatch")
    compose = _validate_compose(state["compose"])
    api = _validate_api(state["api"], revision, tree, compose)
    database = _validate_database(state["database"])
    kubernetes = _validate_kubernetes(state["kubernetes"])
    if tuple(_sequence(state["compose_services"], "compose_services")) != LONG_LIVED_SERVICES:
        raise R7ContractError("compose_services_mismatch")
    if state["api_base_url"] != api["base_url"]:
        raise R7ContractError("api_base_url_projection_mismatch")
    b0 = _mapping(state["b0"], "expected_state_b0")
    if set(b0) != set(EXPECTED_B0):
        raise R7ContractError("expected_b0_fields_mismatch")
    if dict(b0) != EXPECTED_B0:
        raise R7ContractError("expected_b0_identity_or_endpoint_mismatch")
    if tuple(_sequence(state["prometheus_jobs"], "prometheus_jobs")) != PROMETHEUS_JOBS:
        raise R7ContractError("prometheus_jobs_mismatch")
    if state["prometheus_targets_url"] != EXPECTED_PROMETHEUS_TARGETS_URL:
        raise R7ContractError("prometheus_targets_url_mismatch")
    if state["gpu_lease_path"] != EXPECTED_GPU_LEASE_PATH:
        raise R7ContractError("gpu_lease_path_mismatch")
    if list(_sequence(state["active_job_roots"], "active_job_roots")) != []:
        raise R7ContractError("active_job_roots_must_be_empty")
    if list(_sequence(state["active_claim_roots"], "active_claim_roots")) != []:
        raise R7ContractError("active_claim_roots_must_be_empty")
    residue_paths = tuple(_sequence(state["x1_residue_paths"], "x1_residue_paths"))
    if residue_paths != EXPECTED_X1_RESIDUE_PATHS:
        raise R7ContractError("x1_residue_paths_mismatch")
    if state["x1_docker_name_filter"] != "name=evm-x1":
        raise R7ContractError("x1_docker_name_filter_mismatch")
    if tuple(_sequence(state["x1_ports"], "x1_ports")) != (31120, 31121, 31122):
        raise R7ContractError("x1_ports_mismatch")
    selectors = tuple(_sequence(state["x1_kubernetes_selectors"], "x1_selectors"))
    if selectors != tuple(kubernetes["residual_selectors"]):
        raise R7ContractError("x1_selector_projection_mismatch")
    return {
        "compose": compose,
        "api": api,
        "database": database,
        "kubernetes": kubernetes,
        "compose_services": list(LONG_LIVED_SERVICES),
        "api_base_url": EXPECTED_API_BASE_URL,
        "b0": dict(EXPECTED_B0),
        "prometheus_jobs": list(PROMETHEUS_JOBS),
        "prometheus_targets_url": EXPECTED_PROMETHEUS_TARGETS_URL,
        "gpu_lease_path": EXPECTED_GPU_LEASE_PATH,
        "active_job_roots": [],
        "active_claim_roots": [],
        "x1_residue_paths": list(EXPECTED_X1_RESIDUE_PATHS),
        "x1_docker_name_filter": "name=evm-x1",
        "x1_ports": [31120, 31121, 31122],
        "x1_kubernetes_selectors": list(kubernetes["residual_selectors"]),
    }


def validate_r7s1_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_revision: str,
    mode: str = "restore-only",
    repository_root: Path | None = None,
    runtime_timeout: TimeoutContract | None = None,
    lifecycle_timeout: LifecycleTimeoutContract | None = None,
    expected_untracked_path_set_sha256: str | None = None,
    verify_attestations: bool | None = None,
    expected_trusted_checkpoint_sha256: str | None,
    validation_time: datetime | None = None,
) -> dict[str, Any]:
    """Validate a restore-only r7 manifest against executable defaults."""

    required_top_level = {
        "schema_version",
        "work_order_id",
        "bundle_id",
        "execution_mode",
        "created_at",
        "canonical_revision",
        "canonical_tree",
        "bundle",
        "repository",
        "parent_checkpoints",
        "output",
        "timeout_contract",
        "lifecycle_timeout_contract",
        "process_containment",
        "probe_max_attempts",
        "call_contract",
        "expected_state",
        "job_scope_contract",
        "external_terminal_fencing",
        "etw_contract",
        "evidence",
        "runtime",
        "toolchain",
    }
    if set(manifest) != required_top_level:
        raise R7ContractError("r7_restore_manifest_top_level_fields_mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise R7ContractError("r7_restore_manifest_schema_required")
    if manifest.get("work_order_id") != WORK_ORDER_ID:
        raise R7ContractError("r7_restore_work_order_id_mismatch")
    bundle_id = _nonempty(manifest.get("bundle_id"), "r7_bundle_id")
    if "r7s1" not in bundle_id.lower():
        raise R7ContractError("r7_bundle_identity_required")
    _attestation_time(manifest.get("created_at"), "manifest")
    if mode != "restore-only" or manifest.get("execution_mode") != "restore-only":
        raise R7ContractError("r7_restore_only_mode_required")
    revision = _full_sha1(manifest.get("canonical_revision"), "canonical_revision")
    expected = _full_sha1(expected_revision, "expected_revision")
    if revision != expected:
        raise R7ContractError("manifest_canonical_revision_mismatch")
    if revision == PRE_R7_REVISION:
        raise R7ContractError("pre_r7_revision_pin_reuse_forbidden")
    tree = _full_sha1(manifest.get("canonical_tree"), "canonical_tree")

    executable_timeout = (runtime_timeout or TimeoutContract()).validate()
    promised_timeout = TimeoutContract.from_mapping(
        _mapping(manifest.get("timeout_contract"), "timeout_contract")
    )
    if promised_timeout.to_dict() != executable_timeout.to_dict():
        raise R7ContractError("manifest_runtime_timeout_contract_mismatch")
    executable_lifecycle = (lifecycle_timeout or LifecycleTimeoutContract()).validate()
    promised_lifecycle = LifecycleTimeoutContract.from_mapping(
        manifest.get("lifecycle_timeout_contract")
    )
    if promised_lifecycle.to_dict() != executable_lifecycle.to_dict():
        raise R7ContractError("manifest_runtime_lifecycle_timeout_mismatch")
    containment_value = _mapping(manifest.get("process_containment"), "process_containment")
    if dict(containment_value) != PROCESS_CONTAINMENT_CONTRACT:
        raise R7ContractError("process_containment_contract_mismatch")
    containment = dict(PROCESS_CONTAINMENT_CONTRACT)
    toolchain = validate_toolchain_contract(
        manifest.get("toolchain"), verify_files=repository_root is not None
    )

    if manifest.get("probe_max_attempts") != 1 or isinstance(
        manifest.get("probe_max_attempts"), bool
    ):
        raise R7ContractError("probe_max_attempts_must_equal_1")
    calls = _mapping(manifest.get("call_contract"), "call_contract")
    if set(calls) != {"restore-only", "launcher", "collectors", "downstream"}:
        raise R7ContractError("call_contract_sections_mismatch")
    _exact_counts(calls["restore-only"], RESTORE_LIFECYCLE_COUNTS, "restore_call_contract")
    launcher = _exact_counts(calls["launcher"], LAUNCHER_COUNTS, "launcher_call_contract")
    _exact_counts(calls["collectors"], RESTORE_COLLECTOR_COUNTS, "collector_call_contract")
    _exact_counts(calls["downstream"], DOWNSTREAM_COUNTS, "downstream_call_contract")

    repository = _mapping(manifest.get("repository"), "repository")
    expected_repository_fields = {
        "preserved_untracked_count",
        "untracked_path_set_sha256",
        "untracked_path_set_encoding",
        "tracked_changes",
    }
    if set(repository) != expected_repository_fields:
        raise R7ContractError("repository_contract_fields_mismatch")
    if repository["preserved_untracked_count"] != PRESERVED_UNTRACKED_COUNT or isinstance(
        repository["preserved_untracked_count"], bool
    ):
        raise R7ContractError("preserved_untracked_count_mismatch")
    untracked_digest = _full_sha256(repository["untracked_path_set_sha256"], "untracked_path_set")
    if expected_untracked_path_set_sha256 is not None and untracked_digest != _full_sha256(
        expected_untracked_path_set_sha256, "expected_untracked_path_set"
    ):
        raise R7ContractError("untracked_path_set_sha256_mismatch")
    if repository["untracked_path_set_encoding"] != UNTRACKED_PATH_SET_ENCODING:
        raise R7ContractError("untracked_path_set_encoding_mismatch")
    if repository["tracked_changes"] != 0 or isinstance(repository["tracked_changes"], bool):
        raise R7ContractError("tracked_changes_must_equal_zero")

    expected_state = _validate_expected_state(manifest.get("expected_state"), revision, tree)
    verify_attestation_files = (
        repository_root is not None if verify_attestations is None else bool(verify_attestations)
    )
    parent_preview = _validate_parent_entries(manifest.get("parent_checkpoints"))
    expected_parent_map_sha256 = parent_map_sha256(parent_preview)
    output = _mapping(manifest.get("output"), "output")
    if set(output) != {"path", "must_not_exist_before_runner", "write_mode"}:
        raise R7ContractError("output_contract_fields_mismatch")
    if output.get("write_mode") != "create-exclusive":
        raise R7ContractError("output_create_exclusive_required")
    if output.get("must_not_exist_before_runner") is not True:
        raise R7ContractError("output_must_not_exist_before_runner_required")
    output_path = _absolute_normalized_path(output.get("path"), "output_path")
    bundle = _mapping(manifest.get("bundle"), "bundle")
    if set(bundle) != {"path"}:
        raise R7ContractError("bundle_contract_fields_mismatch")
    bundle_path = _absolute_normalized_path(bundle.get("path"), "bundle_path")
    external_contract = _mapping(
        manifest.get("external_terminal_fencing"), "external_terminal_fencing"
    )
    external_binding = _successor_binding(
        external_contract.get("successor_binding"),
        "external_terminal_fencing_successor_binding",
    )
    external_decision = validate_external_terminal_fencing(
        external_contract,
        verify_files=verify_attestation_files,
        expected_trusted_checkpoint_sha256=expected_trusted_checkpoint_sha256,
        expected_successor_binding={
            "run_id": bundle_id,
            "attempt_id": external_binding.get("attempt_id"),
            "commit": revision,
            "tree": tree,
            "nonce": external_binding.get("nonce"),
            "parent_map_sha256": expected_parent_map_sha256,
            "staging_path": str(bundle_path),
            "output_path": str(output_path),
            "emergency_seal_path": str(
                (CANONICAL_OUTPUT_ROOT / f"{bundle_id}-emergency-seal").resolve()
            ),
        },
        manifest_created_at=str(manifest.get("created_at")),
        validation_time=validation_time,
    )
    kubernetes_uids = frozenset(
        str(item["uid"]) for item in expected_state["kubernetes"]["allowed_historical_failed_pods"]
    )
    job_scope = _validate_job_scope(
        manifest.get("job_scope_contract"),
        verify_attestations=verify_attestation_files,
        expected_kubernetes_uids=kubernetes_uids,
        external_decision=external_decision,
    )
    mlflow_scope = next(
        item
        for item in job_scope["historical_classifications"]
        if item["source"] == "mlflow_running_rows"
    )
    if external_decision["verified"] is not True and (
        mlflow_scope["historical_count"] != 0 or mlflow_scope["unproven_count"] == 0
    ):
        raise R7ContractError("mlflow_running_row_requires_verified_terminal_fencing_decision")

    parents = _validate_parent_entries(
        manifest.get("parent_checkpoints"),
        bundle_directory=bundle_path,
        output_directory=output_path,
    )
    if parent_map_sha256(parents) != expected_parent_map_sha256:
        raise R7ContractError("parent_map_changed_during_validation")

    evidence = _mapping(manifest.get("evidence"), "evidence")
    required_evidence = {
        "write_mode": "create-exclusive",
        "failure_creates_completion_marker": False,
        "restore_only_creates_completion_marker": False,
        "failure_index_is_not_success_index": True,
        "success_requires_all_invariants": True,
    }
    if dict(evidence) != required_evidence:
        raise R7ContractError("evidence_contract_mismatch")

    etw = _mapping(manifest.get("etw_contract"), "etw_contract")
    if set(etw) != {
        "decision",
        "amendment_path",
        "amendment_sha256",
        "fresh_capture_required_for_phase_b2_go",
        "fresh_invocations",
    }:
        raise R7ContractError("etw_contract_fields_mismatch")
    if etw["decision"] != (
        "existing_pinned_etw_evidence_is_admissible;fresh_capture_not_a_phase_b2_go_invariant"
    ):
        raise R7ContractError("etw_contract_decision_mismatch")
    _nonempty(etw["amendment_path"], "etw_amendment_path")
    _full_sha256(etw["amendment_sha256"], "etw_amendment")
    if (
        etw["fresh_capture_required_for_phase_b2_go"] is not False
        or etw["fresh_invocations"] != 0
        or isinstance(etw["fresh_invocations"], bool)
    ):
        raise R7ContractError("etw_fresh_capture_must_remain_zero")
    runtime = None
    if repository_root is not None:
        runtime = validate_runtime_pins(manifest, repository_root)
    else:
        runtime_contract = _mapping(manifest.get("runtime"), "runtime")
        if set(runtime_contract) != set(RUNTIME_COMPONENTS):
            raise R7ContractError("runtime_component_role_set_mismatch")
        for name in RUNTIME_COMPONENTS:
            component = _mapping(runtime_contract[name], f"runtime_{name}")
            if set(component) != {
                "path",
                "sha256",
                "worktree_blob_oid",
                "head_blob_oid",
                "bytes",
            }:
                raise R7ContractError(f"runtime_{name}_fields_mismatch")
            _nonempty(component["path"], f"runtime_{name}_path")
            _full_sha256(component.get("sha256"), f"runtime_{name}")
            _full_sha1(component.get("worktree_blob_oid"), f"runtime_{name}_worktree_blob")
            _full_sha1(component.get("head_blob_oid"), f"runtime_{name}_head_blob")
            if (
                isinstance(component["bytes"], bool)
                or not isinstance(component["bytes"], int)
                or component["bytes"] < 1
            ):
                raise R7ContractError(f"runtime_{name}_positive_bytes_required")

    return {
        "schema_version": SCHEMA_VERSION,
        "work_order_id": WORK_ORDER_ID,
        "revision": revision,
        "tree": tree,
        "mode": "restore-only",
        "timeout_contract": executable_timeout.to_dict(),
        "lifecycle_timeout_contract": executable_lifecycle.to_dict(),
        "process_containment": containment,
        "launcher_calls": launcher,
        "untracked_path_set_sha256": untracked_digest,
        "parents": parents,
        "job_scope_contract": job_scope,
        "typed_historical_failed_pods": expected_state["kubernetes"][
            "allowed_historical_failed_pods"
        ],
        "historical_decisions": [external_decision],
        "successor_binding": external_decision["successor_binding"],
        "historical_go": (
            verify_attestation_files
            and external_decision["verified"] is True
            and all(
                item["executing_count"] == 0 and item["unproven_count"] == 0
                for item in job_scope["historical_classifications"]
            )
        ),
        "expected_state": expected_state,
        "runtime": runtime,
        "toolchain": toolchain,
    }


def read_parent_checkpoints(
    manifest_parent_entries: Any,
) -> tuple[dict[str, dict[str, Any]], RestoreCheckpoint]:
    """Read and hash ten immutable parents without executing any of them."""

    entries = _validate_parent_entries(manifest_parent_entries)
    protected_root = Path(
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/private/s8-v4"
    ).resolve()
    payloads: dict[str, dict[str, Any]] = {}
    for role in PARENT_CHECKPOINT_ROLES:
        entry = entries[role]
        path = Path(entry["path"])
        try:
            path.relative_to(protected_root)
        except ValueError as exc:
            raise R7ContractError(f"parent_checkpoint_outside_protected_root:{role}") from exc
        if not path.is_file():
            raise R7ContractError(f"parent_checkpoint_file_missing:{role}:{path}")
        try:
            payload, measured_parent_sha = _read_json_snapshot(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7ContractError(f"parent_checkpoint_json_invalid:{role}") from exc
        if measured_parent_sha != entry["sha256"]:
            raise R7ContractError(f"parent_checkpoint_sha256_mismatch:{role}")
        if not isinstance(payload, dict):
            raise R7ContractError(f"parent_checkpoint_object_required:{role}")
        if payload.get("schema") != entry["schema"]:
            raise R7ContractError(f"parent_checkpoint_schema_mismatch:{role}")
        if payload.get("acceptance_credit") is True:
            raise R7ContractError(f"parent_checkpoint_credit_forbidden:{role}")
        if (
            payload.get("success_marker_created") is True
            or payload.get("completion_marker_created") is True
        ):
            raise R7ContractError(f"parent_checkpoint_success_marker_forbidden:{role}")
        if payload.get("phase_b2_executed") is True:
            raise R7ContractError(f"parent_checkpoint_phase_b2_execution_forbidden:{role}")
        payloads[role] = payload

    r5_seal = payloads["r5_failure_seal"]
    if (
        r5_seal.get("failure_only") is not True
        or r5_seal.get("acceptance_credit") is not False
        or r5_seal.get("decision") != "manual_intervention_required"
        or r5_seal.get("success_marker_created") is not False
    ):
        raise R7ContractError("r5_failure_seal_semantics_required")
    r5_index = payloads["r5_failure_index"]
    if (
        r5_index.get("failure_only") is not True
        or r5_index.get("acceptance_credit") is not False
        or r5_index.get("is_success_index") is not False
        or r5_index.get("completion_marker_created") is not False
    ):
        raise R7ContractError("r5_failure_index_semantics_required")
    report_value = r5_seal.get("report")
    if not isinstance(report_value, Mapping):
        metadata = r5_seal.get("metadata")
        report_value = metadata.get("report") if isinstance(metadata, Mapping) else None
    report = _mapping(report_value, "r5_failure_seal_report")
    r5_run_id = _nonempty(report.get("run_id"), "r5_failure_seal_report_run_id")
    if (
        report.get("passed") is not False
        or report.get("overall_pass") is not False
        or report.get("phase_b2_executed") is not False
        or report.get("completion_marker_created") is not False
    ):
        raise R7ContractError("r5_failure_seal_report_no_credit_semantics_required")
    r5_files = _sequence(r5_index.get("files"), "r5_failure_index_files")
    if len(r5_files) != 1:
        raise R7ContractError("r5_failure_index_exact_seal_link_required")
    r5_link = _mapping(r5_files[0], "r5_failure_index_seal_link")
    if (
        set(r5_link) != {"path", "sha256", "bytes"}
        or r5_link.get("path") != "failure-seal.json"
        or r5_link.get("sha256") != entries["r5_failure_seal"]["sha256"]
        or isinstance(r5_link.get("bytes"), bool)
        or not isinstance(r5_link.get("bytes"), int)
        or r5_link["bytes"] < 1
    ):
        raise R7ContractError("r5_failure_index_exact_seal_link_required")

    r6_rca = payloads["r6_compose_rca"]
    r6_run_id = _nonempty(r6_rca.get("run_identity"), "r6_compose_rca_run_identity")
    r6_calls = _mapping(r6_rca.get("r6_restore_only"), "r6_compose_rca_restore_only")
    if (
        r6_rca.get("decision") != "manual_intervention_required"
        or r6_rca.get("credit") != "zero_credit"
        or r6_rca.get("go") is not False
        or r6_rca.get("completion_marker_created") is not False
        or r6_calls
        != {
            "bundle_created": False,
            "executed": False,
            "outer_calls": 0,
            "bridge_calls": 0,
            "runner_calls": 0,
            "retries": 0,
        }
    ):
        raise R7ContractError("r6_compose_rca_no_credit_semantics_required")

    r6_amendment = payloads["r6_failure_seal_amendment"]
    if (
        r6_amendment.get("base_rca_sha256") != entries["r6_compose_rca"]["sha256"]
        or r6_amendment.get("decision") != "manual_intervention_required"
        or r6_amendment.get("result") != "no_go"
        or r6_amendment.get("credit") != "zero_credit"
        or r6_amendment.get("r6_restore_only_executed") is not False
        or r6_amendment.get("completion_marker_created") is not False
    ):
        raise R7ContractError("r6_failure_seal_amendment_chain_or_semantics_mismatch")

    r6_index = payloads["r6_final_index"]
    r6_amendment_link = _mapping(r6_index.get("seal_amendment"), "r6_final_index_seal_amendment")
    if (
        r6_index.get("decision") != "manual_intervention_required"
        or r6_index.get("completion_marker") is not None
        or r6_amendment_link.get("sha256") != entries["r6_failure_seal_amendment"]["sha256"]
    ):
        raise R7ContractError("r6_final_index_chain_or_semantics_mismatch")

    post_manual_readback = payloads["post_manual_on_readback"]
    if (
        post_manual_readback.get("decision") != "manual_intervention_required"
        or post_manual_readback.get("result") != "no_go"
        or post_manual_readback.get("r6_restore_only_calls") != 0
        or isinstance(post_manual_readback.get("r6_restore_only_calls"), bool)
        or post_manual_readback.get("completion_marker_created") is not False
    ):
        raise R7ContractError("post_manual_on_readback_no_credit_semantics_required")
    post_manual_index = payloads["post_manual_on_index"]
    previous_index = _mapping(
        post_manual_index.get("previous_index"), "post_manual_on_index_previous_index"
    )
    readback_link = _mapping(
        post_manual_index.get("final_runtime_readback"),
        "post_manual_on_index_final_runtime_readback",
    )
    if (
        post_manual_index.get("decision") != "manual_intervention_required"
        or post_manual_index.get("completion_marker") is not None
        or previous_index.get("sha256") != entries["r6_final_index"]["sha256"]
        or readback_link.get("sha256") != entries["post_manual_on_readback"]["sha256"]
    ):
        raise R7ContractError("post_manual_on_index_chain_or_semantics_mismatch")

    r7_seal = payloads["r7_failure_seal"]
    r7_index = payloads["r7_failure_index"]
    r7_seal_run_id = _nonempty(r7_seal.get("run_identity"), "r7_failure_seal_run_identity")
    r7_index_run_id = _nonempty(r7_index.get("run_identity"), "r7_failure_index_run_identity")
    if r7_seal_run_id != r7_index_run_id:
        raise R7ContractError("r7_failure_seal_index_run_identity_mismatch")
    r7_pinned = _mapping(r7_seal.get("pinned_evidence"), "r7_failure_seal_pinned_evidence")
    if (
        r7_seal.get("decision") != "NO-GO"
        or r7_seal.get("credit") != "zero-credit"
        or r7_seal.get("manual_intervention_required") is not True
        or r7_seal.get("completion_marker_created") is not False
        or r7_seal.get("seal_is_final_commit_record") is not True
        or r7_pinned.get("failure_index_sha256") != entries["r7_failure_index"]["sha256"]
    ):
        raise R7ContractError("r7_failure_seal_chain_or_semantics_mismatch")
    if (
        r7_index.get("decision") != "NO-GO"
        or r7_index.get("credit") != "zero-credit"
        or r7_index.get("manual_intervention_required") is not True
        or r7_index.get("completion_marker_present") is not False
        or r7_index.get("success_private_index_present") is not False
        or r7_index.get("failure_seal_expected_last") is not True
    ):
        raise R7ContractError("r7_failure_index_no_credit_semantics_required")
    r7_post_seal = payloads["r7_post_seal_residual_amendment"]
    if (
        r7_post_seal.get("parent_failure_seal_sha256") != entries["r7_failure_seal"]["sha256"]
        or r7_post_seal.get("decision") != "manual_intervention_required"
        or r7_post_seal.get("additional_automatic_work_authorized") is not False
    ):
        raise R7ContractError("r7_post_seal_amendment_chain_or_semantics_mismatch")

    inherited_run_ids = {
        "r5_failure_seal": r5_run_id,
        "r5_failure_index": r5_run_id,
        "r6_compose_rca": r6_run_id,
        "r6_failure_seal_amendment": r6_run_id,
        "r6_final_index": r6_run_id,
        "post_manual_on_readback": r6_run_id,
        "post_manual_on_index": r6_run_id,
        "r7_failure_seal": r7_seal_run_id,
        "r7_failure_index": r7_seal_run_id,
        "r7_post_seal_residual_amendment": r7_seal_run_id,
    }
    for role, inherited_run_id in inherited_run_ids.items():
        if entries[role]["run_id"] != inherited_run_id:
            raise R7ContractError(f"parent_checkpoint_run_id_mismatch:{role}")

    historical_counts = _exact_counts(
        report.get("call_counts"), RESTORE_LIFECYCLE_COUNTS, "r5_historical_call_counts"
    )
    checkpoint = RestoreCheckpoint(
        source="r7s1_ten_parent_checkpoint_set",
        historical_call_counts=historical_counts,
        previous_attempt_failed=True,
    )
    return payloads, checkpoint


def decode_launcher_evidence(encoded: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7ContractError("launcher_evidence_base64_json_invalid") from exc
    if not isinstance(value, dict):
        raise R7ContractError("launcher_evidence_object_required")
    required_fields = {
        "schema",
        "token_evidence",
        "sha_chain",
        "toolchain_observation",
        "git",
        "run_id",
        "mode",
        "invocation_counts",
    }
    if set(value) != required_fields:
        raise R7ContractError("launcher_evidence_top_level_fields_mismatch")
    if value.get("schema") != LAUNCHER_EVIDENCE_SCHEMA:
        raise R7ContractError("launcher_evidence_schema_mismatch")
    if value.get("run_id") != manifest.get("bundle_id"):
        raise R7ContractError("launcher_evidence_run_id_mismatch")
    if value.get("mode") != "restore-only":
        raise R7ContractError("launcher_evidence_mode_mismatch")
    token = _mapping(value.get("token_evidence"), "launcher_token_evidence")
    if token.get("administrator") is not True:
        raise R7ContractError("launcher_administrator_token_required")
    integrity = str(token.get("integrity", "")).lower()
    if integrity not in {"high", "system"}:
        raise R7ContractError("launcher_high_or_system_integrity_required")
    if str(token.get("token_elevation_type", "")).lower() != "full":
        raise R7ContractError("launcher_full_token_required")
    chain = _mapping(value.get("sha_chain"), "launcher_sha_chain")
    required_chain = {
        "outer",
        "bridge",
        "manifest",
        "trusted_checkpoint",
        "parent_map",
        "python_distribution",
        "git_distribution",
        *RUNTIME_COMPONENTS,
        *PARENT_CHECKPOINT_ROLES,
    }
    if set(chain) != required_chain:
        raise R7ContractError("launcher_sha_chain_role_set_mismatch")
    for name in required_chain:
        _full_sha256(chain[name], f"launcher_sha_chain_{name}")
    toolchain = _mapping(manifest.get("toolchain"), "toolchain")
    observations = _mapping(value.get("toolchain_observation"), "launcher_toolchain_observation")
    if set(observations) != {"python_distribution", "git_distribution"}:
        raise R7ContractError("launcher_toolchain_observation_role_set_mismatch")
    for name in ("python_distribution", "git_distribution"):
        observed = _mapping(observations[name], f"launcher_toolchain_observation_{name}")
        if set(observed) != {
            "distribution_tree_sha256",
            "file_count",
            "tree_encoding",
        }:
            raise R7ContractError(f"launcher_toolchain_observation_fields_mismatch:{name}")
        pinned = _mapping(toolchain.get(name), f"toolchain_{name}")
        expected_projection = {
            "distribution_tree_sha256": _full_sha256(
                pinned.get("distribution_tree_sha256"), f"toolchain_{name}_tree"
            ),
            "file_count": pinned.get("file_count"),
            "tree_encoding": pinned.get("tree_encoding"),
        }
        if observed != expected_projection:
            raise R7ContractError(f"launcher_toolchain_observation_mismatch:{name}")
        if str(chain[name]).lower() != expected_projection["distribution_tree_sha256"]:
            raise R7ContractError(f"launcher_toolchain_sha_chain_mismatch:{name}")
    runtime = _mapping(manifest.get("runtime"), "runtime")
    for name in RUNTIME_COMPONENTS:
        component = _mapping(runtime.get(name), f"runtime_{name}")
        if str(chain[name]).lower() != str(component.get("sha256", "")).lower():
            raise R7ContractError(f"launcher_runtime_sha_chain_mismatch:{name}")
    parents = _validate_parent_entries(manifest.get("parent_checkpoints"))
    checkpoint = _mapping(
        _mapping(
            manifest.get("external_terminal_fencing"),
            "external_terminal_fencing",
        ).get("trusted_checkpoint"),
        "trusted_checkpoint",
    )
    if str(chain["trusted_checkpoint"]).lower() != str(checkpoint.get("sha256", "")).lower():
        raise R7ContractError("launcher_trusted_checkpoint_sha_chain_mismatch")
    if str(chain["parent_map"]).lower() != parent_map_sha256(parents):
        raise R7ContractError("launcher_parent_map_sha_chain_mismatch")
    for role in PARENT_CHECKPOINT_ROLES:
        if str(chain[role]).lower() != parents[role]["sha256"]:
            raise R7ContractError(f"launcher_parent_sha_chain_mismatch:{role}")
    value["invocation_counts"] = _exact_counts(
        value.get("invocation_counts"), LAUNCHER_COUNTS, "launcher_evidence_invocation_counts"
    )
    return value


def r7s1_restore_report(report: RestoreReport, run_id: str) -> dict[str, Any]:
    if report.mode != "restore-only":
        raise R7ContractError("restore_only_report_mode_required")
    _exact_counts(report.call_counts, RESTORE_LIFECYCLE_COUNTS, "restore_report_call_counts")
    value = report.to_dict()
    value.update(
        {
            "schema": "s8-v4-x1-phase-b2-r7s1-restore-report/v1",
            "run_id": _nonempty(run_id, "run_id"),
            "restore_only_pass": bool(report.passed),
            "acceptance_credit": False,
            "phase_b2_executed": False,
            "completion_marker_created": False,
            "process_containment": "windows_job_object",
        }
    )
    return value


def _file_identity(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _reject_reparse_ancestor_chain(path: Path, label: str) -> None:
    """Reject every existing symlink or Windows reparse point up to *path*."""

    candidate = Path(os.path.abspath(os.fspath(path)))
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for current in reversed((candidate, *candidate.parents)):
        try:
            measured = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise R7ContractError(f"{label}_ancestor_lstat_failed:{current}") from exc
        attributes = int(getattr(measured, "st_file_attributes", 0))
        if stat.S_ISLNK(measured.st_mode) or attributes & reparse_flag:
            raise R7ContractError(f"{label}_reparse_point_forbidden:{current}")


def _partial_artifact_inventory(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Inventory direct regular-file fragments without following link-like entries."""

    try:
        root_status = os.lstat(root)
    except FileNotFoundError:
        return [], "primary_output_missing"
    except OSError as exc:
        return [], f"primary_output_lstat_failed:{type(exc).__name__}:{exc}"
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(root_status.st_mode)
        or int(getattr(root_status, "st_file_attributes", 0)) & reparse_flag
    ):
        return [], "primary_output_reparse_point_forbidden"
    if not stat.S_ISDIR(root_status.st_mode):
        return [], "primary_output_not_directory"
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        return [], f"primary_output_enumeration_failed:{type(exc).__name__}:{exc}"
    inventory: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in entries:
        try:
            measured = os.lstat(entry)
            attributes = int(getattr(measured, "st_file_attributes", 0))
            if stat.S_ISLNK(measured.st_mode) or attributes & reparse_flag:
                errors.append(f"reparse_entry_forbidden:{entry.name}")
                continue
            if not stat.S_ISREG(measured.st_mode):
                errors.append(f"non_regular_entry_ignored:{entry.name}")
                continue
            inventory.append(
                {
                    "path": entry.name,
                    "bytes": measured.st_size,
                    "sha256": sha256_file(entry),
                }
            )
        except OSError as exc:
            errors.append(f"entry_inventory_failed:{entry.name}:{type(exc).__name__}:{exc}")
    return inventory, ";".join(errors) or None


def _emergency_process_residue(value: Any) -> dict[str, Any]:
    residue = _mapping(value, "emergency_process_residue")
    if set(residue) != {
        "manual_intervention_required",
        "residual_pids",
        "residual_status",
    }:
        raise R7ContractError("emergency_process_residue_fields_mismatch")
    if residue["manual_intervention_required"] is not True:
        raise R7ContractError("emergency_manual_intervention_latch_required")
    raw_pids = _sequence(residue["residual_pids"], "emergency_residual_pids")
    pids: list[int] = []
    for raw_pid in raw_pids:
        if isinstance(raw_pid, bool) or not isinstance(raw_pid, int) or raw_pid < 1:
            raise R7ContractError("emergency_residual_pid_positive_integer_required")
        pids.append(raw_pid)
    if len(set(pids)) != len(pids):
        raise R7ContractError("emergency_residual_pids_must_be_unique")
    return {
        "manual_intervention_required": True,
        "residual_pids": sorted(pids),
        "residual_status": _nonempty(residue["residual_status"], "emergency_residual_status"),
    }


def _emergency_manifest_identity(
    value: Any, successor_binding: Mapping[str, str]
) -> dict[str, str]:
    identity = _mapping(value, "emergency_manifest_identity")
    if set(identity) != {"path", "sha256", "canonical_revision", "canonical_tree"}:
        raise R7ContractError("emergency_manifest_identity_fields_mismatch")
    path = _absolute_normalized_path(identity["path"], "emergency_manifest_path")
    expected_path = (
        Path(successor_binding["staging_path"]) / "phase-b2-r7s1-work-order.json"
    ).resolve()
    if str(path).casefold() != str(expected_path).casefold():
        raise R7ContractError("emergency_manifest_path_mismatch")
    revision = _full_sha1(identity["canonical_revision"], "emergency_manifest_revision")
    tree = _full_sha1(identity["canonical_tree"], "emergency_manifest_tree")
    if revision != successor_binding["commit"] or tree != successor_binding["tree"]:
        raise R7ContractError("emergency_manifest_source_identity_mismatch")
    return {
        "path": str(path),
        "sha256": _full_sha256(identity["sha256"], "emergency_manifest"),
        "canonical_revision": revision,
        "canonical_tree": tree,
    }


class EvidenceWriter:
    """One create-exclusive evidence directory for one r7 restore attempt."""

    def __init__(
        self,
        output_directory: Path,
        *,
        successor_binding: Mapping[str, Any],
    ) -> None:
        self._initialize_bound_directory(
            output_directory,
            successor_binding=successor_binding,
            binding_path_key="output_path",
        )

    def _initialize_bound_directory(
        self,
        directory: Path,
        *,
        successor_binding: Mapping[str, Any],
        binding_path_key: str,
    ) -> None:
        binding = _successor_binding(successor_binding, "evidence_successor_binding")
        _reject_reparse_ancestor_chain(Path(directory), f"evidence_{binding_path_key}")
        root = _absolute_normalized_path(directory, f"evidence_{binding_path_key}")
        if str(root).casefold() != binding[binding_path_key].casefold():
            raise R7ContractError(f"evidence_{binding_path_key}_binding_mismatch")
        self.root = root
        self.successor_binding = binding
        # This is deliberately the final live check before the create-exclusive
        # mkdir.  Windows handle-relative/root-swap protection remains an OS TCB
        # assumption documented by the outer runner.
        _reject_reparse_ancestor_chain(self.root, f"evidence_{binding_path_key}_pre_create")
        try:
            self.root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise R7EvidenceExistsError(f"evidence_directory_exists:{self.root}") from exc

    @classmethod
    def seal_emergency(
        cls,
        *,
        primary_output: Path,
        successor_binding: Mapping[str, Any],
        failed_stage: str,
        exception: BaseException,
        process_residue: Mapping[str, Any],
        manifest_identity: Mapping[str, Any],
        expected_trusted_checkpoint_sha256: str,
    ) -> dict[str, Any]:
        """Publish the one-shot upper seal after ordinary publication failed."""

        try:
            binding = _successor_binding(successor_binding, "emergency_successor_binding")
            primary = _absolute_normalized_path(primary_output, "emergency_primary_output")
            if str(primary).casefold() != binding["output_path"].casefold():
                raise R7ContractError("emergency_primary_output_binding_mismatch")
            emergency = Path(binding["emergency_seal_path"])
            if primary.parent != emergency.parent:
                raise R7ContractError("emergency_primary_and_seal_must_be_siblings")
            residue = _emergency_process_residue(process_residue)
            manifest = _emergency_manifest_identity(manifest_identity, binding)
            partial_artifacts, partial_inventory_error = _partial_artifact_inventory(primary)
            try:
                exception_message = str(exception)
            except Exception as stringify_error:
                exception_message = (
                    f"exception_stringification_failed:{type(stringify_error).__name__}"
                )
            payload = {
                "schema": "s8-v4-x1-phase-b2-r7s1-emergency-failure-seal/v1",
                "created_at": utc_now(),
                "emergency_only": True,
                "failure_only": True,
                "decision": "manual_intervention_required",
                "manual_intervention_required": True,
                "acceptance_credit": False,
                "success_marker_created": False,
                "completion_marker_created": False,
                "phase_b2_executed": False,
                "automatic_retry": 0,
                "run_id": binding["run_id"],
                "attempt_id": binding["attempt_id"],
                "successor_binding": binding,
                "successor_binding_sha256": hashlib.sha256(
                    canonical_json_bytes(binding)
                ).hexdigest(),
                "primary_output": str(primary),
                "failed_stage": _nonempty(failed_stage, "emergency_failed_stage"),
                "exception": {
                    "type": type(exception).__name__,
                    "message": exception_message,
                },
                "partial_artifacts": partial_artifacts,
                "partial_inventory_error": partial_inventory_error,
                "process_residue": residue,
                "manifest_identity": manifest,
                "expected_trusted_checkpoint_sha256": _full_sha256(
                    expected_trusted_checkpoint_sha256,
                    "emergency_expected_trusted_checkpoint",
                ),
            }
            # Serialize before reserving the emergency directory.  A payload
            # error therefore cannot leave an empty seal path behind.
            payload_bytes = canonical_json_bytes(payload)
            writer = cls.__new__(cls)
            writer._initialize_bound_directory(
                emergency,
                successor_binding=binding,
                binding_path_key="emergency_seal_path",
            )
            seal_identity = writer.write_bytes("emergency-failure-seal.json", payload_bytes)
            return {
                "emergency_directory": str(emergency),
                "emergency_seal": seal_identity,
            }
        except R7EmergencySealError:
            raise
        except Exception as seal_error:
            raise R7EmergencySealError(
                f"emergency_seal_failed:{type(seal_error).__name__}:{seal_error}"
            ) from seal_error

    @staticmethod
    def _publish_source_leaf(name: str) -> str:
        return f".{name}.publish-source"

    @classmethod
    def _planned_publish_source(cls, name: str, payload: bytes) -> dict[str, Any]:
        return {
            "path": cls._publish_source_leaf(name),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def write_bytes(self, name: str, payload: bytes) -> dict[str, Any]:
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("evidence_leaf_name_required")
        if not isinstance(payload, bytes):
            raise TypeError("evidence_payload_bytes_required")
        path = self.root / name
        if path.exists():
            raise R7EvidenceExistsError(f"evidence_path_exists:{path}")
        publish_source = self.root / self._publish_source_leaf(name)
        if publish_source.exists():
            raise R7EvidenceExistsError(f"evidence_publish_source_exists:{publish_source}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(publish_source, flags, 0o600)
        except FileExistsError as exc:
            raise R7EvidenceExistsError(f"evidence_publish_source_exists:{publish_source}") from exc
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor, view[offset:])
                if written <= 0:
                    raise OSError("exclusive evidence write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            # Hard-link publication is an atomic create-new operation: it
            # cannot replace an existing leaf, and the final name never
            # exposes a partially written payload.
            os.link(publish_source, path)
        except FileExistsError as exc:
            raise R7EvidenceExistsError(f"evidence_path_exists:{path}") from exc
        if not os.path.samefile(publish_source, path):
            raise OSError("evidence_publish_source_identity_mismatch")
        return _file_identity(path, self.root)

    def write_json(self, name: str, value: Any) -> dict[str, Any]:
        return self.write_bytes(name, canonical_json_bytes(value))

    def inventory(self, *, exclude: Sequence[str] = ()) -> list[dict[str, Any]]:
        excluded = set(exclude)
        return [
            _file_identity(path, self.root)
            for path in sorted(self.root.iterdir(), key=lambda item: item.name)
            if path.is_file() and path.name not in excluded
        ]

    def seal_failure(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        prior = self.inventory(exclude=("failure-seal.json", "failure-evidence-index.json"))
        seal = {
            "schema": "s8-v4-x1-phase-b2-r7s1-failure-seal/v1",
            "sealed_at": utc_now(),
            "failure_only": True,
            "decision": "manual_intervention_required",
            "acceptance_credit": False,
            "success_marker_created": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
            "report": dict(report),
            "metadata": dict(metadata or {}),
            "prior_files": prior,
        }
        # The seal is the final create-exclusive commit record.  Publish the
        # index first with the exact planned seal identity so a crash can leave
        # only an explicitly uncommitted draft, never a committed seal lacking
        # its index.
        seal_payload = canonical_json_bytes(seal)
        planned_seal = {
            "path": "failure-seal.json",
            "bytes": len(seal_payload),
            "sha256": hashlib.sha256(seal_payload).hexdigest(),
        }
        planned_seal_source = self._planned_publish_source("failure-seal.json", seal_payload)
        index = {
            "schema": "s8-v4-x1-phase-b2-r7s1-failure-evidence-index/v1",
            "created_at": utc_now(),
            "failure_only": True,
            "is_success_index": False,
            "acceptance_credit": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
            "publication_state": "pending_until_commit_record_exists",
            "commit_record": planned_seal,
            "files": [*prior, planned_seal_source, planned_seal],
        }
        index_file = self.write_json("failure-evidence-index.json", index)
        seal_file = self.write_bytes("failure-seal.json", seal_payload)
        if seal_file != planned_seal:
            raise OSError("failure seal identity differs from planned commit record")
        seal_source_file = _file_identity(
            self.root / self._publish_source_leaf("failure-seal.json"), self.root
        )
        if seal_source_file != planned_seal_source:
            raise OSError("failure seal publish source differs from planned identity")
        return {"failure_seal": seal_file, "failure_index": index_file}

    def seal_restore_only(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if report.get("restore_only_pass") is not True or report.get("passed") is not True:
            raise R7SuccessInvariantError("passing_restore_only_report_required")
        if report.get("phase_b2_executed") is not False:
            raise R7SuccessInvariantError("restore_only_phase_b2_execution_forbidden")
        if report.get("acceptance_credit") is not False:
            raise R7SuccessInvariantError("restore_only_acceptance_credit_forbidden")
        if report.get("completion_marker_created") is not False:
            raise R7SuccessInvariantError("restore_only_completion_marker_forbidden")
        if report.get("manual_intervention_required") is not False:
            raise R7SuccessInvariantError("restore_only_manual_intervention_forbidden")
        if report.get("residual_pids"):
            raise R7SuccessInvariantError("restore_only_residual_process_forbidden")
        if report.get("mode") != "restore-only":
            raise R7SuccessInvariantError("restore_only_report_mode_required")
        if report.get("decision") != "restore_only_pass":
            raise R7SuccessInvariantError("restore_only_pass_decision_required")
        if report.get("deadline_exceeded") is not False:
            raise R7SuccessInvariantError("restore_only_deadline_must_not_be_exceeded")
        _exact_counts(report.get("call_counts"), RESTORE_LIFECYCLE_COUNTS, "report_call_counts")
        required = tuple(str(item) for item in report.get("required_invariants", ()))
        invariants = _mapping(report.get("success_invariants"), "success_invariants")
        if required != R7_REQUIRED_INVARIANTS:
            raise R7SuccessInvariantError("restore_only_required_invariant_set_mismatch")
        expected_invariant_names = {
            *R7_REQUIRED_INVARIANTS,
            *(stage.value for stage in RESTORE_STAGE_ORDER),
        }
        if set(invariants) != expected_invariant_names:
            raise R7SuccessInvariantError("restore_only_success_invariant_fields_mismatch")
        if any(invariants.get(name) is not True for name in expected_invariant_names):
            raise R7SuccessInvariantError("restore_only_all_required_invariants_required")
        stages = _sequence(report.get("stages"), "restore_stages")
        if len(stages) != len(RESTORE_STAGE_ORDER):
            raise R7SuccessInvariantError("restore_only_stage_count_mismatch")
        stage_fields = {
            "stage",
            "started_at",
            "ended_at",
            "duration_seconds",
            "attempts",
            "max_attempts",
            "passed",
            "retryable_ignored",
            "last_error",
            "manual_intervention_required",
            "residual_pids",
            "invariants",
            "details",
            "deadline_remaining_seconds",
        }
        for expected_stage, raw_stage in zip(RESTORE_STAGE_ORDER, stages, strict=True):
            stage = _mapping(raw_stage, "restore_stage")
            if set(stage) != stage_fields or stage["stage"] != expected_stage.value:
                raise R7SuccessInvariantError("restore_only_stage_schema_or_order_mismatch")
            if (
                stage["passed"] is not True
                or stage["attempts"] != 1
                or isinstance(stage["attempts"], bool)
                or stage["max_attempts"] != 1
                or isinstance(stage["max_attempts"], bool)
                or stage["manual_intervention_required"] is not False
                or stage["residual_pids"]
                or stage["last_error"] is not None
            ):
                raise R7SuccessInvariantError(
                    f"restore_only_stage_not_passing:{expected_stage.value}"
                )
            if not isinstance(stage["retryable_ignored"], bool):
                raise R7SuccessInvariantError(
                    "restore_only_stage_retryable_evidence_boolean_required"
                )
            _nonempty(stage["started_at"], "restore_stage_started_at")
            _nonempty(stage["ended_at"], "restore_stage_ended_at")
            for name in ("duration_seconds", "deadline_remaining_seconds"):
                value = stage[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or float(value) < 0
                ):
                    raise R7SuccessInvariantError(f"restore_only_stage_{name}_invalid")
            _mapping(stage["invariants"], "restore_stage_invariants")
            _mapping(stage["details"], "restore_stage_details")
        report_payload = canonical_json_bytes(dict(report))
        report_file = self.write_bytes("restore-only-report.json", report_payload)
        report_source_file = self._planned_publish_source(
            "restore-only-report.json", report_payload
        )
        index = {
            "schema": "s8-v4-x1-phase-b2-r7s1-restore-only-index/v1",
            "created_at": utc_now(),
            "restore_only_pass": True,
            "acceptance_credit": False,
            "is_phase_b2_success_index": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
            "metadata": dict(metadata or {}),
            "files": [report_source_file, report_file],
        }
        index_file = self.write_json("restore-only-index.json", index)
        return {"restore_only_report": report_file, "restore_only_index": index_file}


R7S1ContractError = R7ContractError
R7S1EvidenceExistsError = R7EvidenceExistsError
R7S1EmergencySealError = R7EmergencySealError
R7S1SuccessInvariantError = R7SuccessInvariantError
R7S1_REQUIRED_INVARIANTS = R7_REQUIRED_INVARIANTS


__all__ = [
    "AIRFLOW_MIGRATION_HEAD",
    "CANONICAL_DOCKER_CLIENT_CONFIG_BYTES",
    "CANONICAL_DOCKER_CLIENT_CONFIG_PATH",
    "CANONICAL_DOCKER_CLIENT_CONFIG_SHA256",
    "CANONICAL_DOCKER_CONTEXT_METADATA_BYTES",
    "CANONICAL_DOCKER_CONTEXT_METADATA_PATH",
    "CANONICAL_DOCKER_CONTEXT_METADATA_SHA256",
    "CANONICAL_DOCKER_CONTEXT_TLS_PATH",
    "CANONICAL_GIT_ATTRIBUTES_BYTES",
    "CANONICAL_GIT_ATTRIBUTES_PATH",
    "CANONICAL_GIT_ATTRIBUTES_SHA256",
    "CANONICAL_GIT_CONFIG_BYTES",
    "CANONICAL_GIT_CONFIG_PATH",
    "CANONICAL_GIT_CONFIG_SHA256",
    "CANONICAL_GIT_INFO_ATTRIBUTES_PATH",
    "CANONICAL_GIT_TOP_ATTRIBUTES_PATH",
    "CANONICAL_OUTPUT_ROOT",
    "CANONICAL_STAGING_ROOT",
    "CANONICAL_KUBERNETES_CLIENT_CONFIG_BYTES",
    "CANONICAL_KUBERNETES_CLIENT_CONFIG_PATH",
    "CANONICAL_KUBERNETES_CLIENT_CONFIG_SHA256",
    "DATABASE_INSTANCES",
    "DOCKER_COMPOSE_EXECUTABLE",
    "DOCKER_CLIENT_CONFIG_POLICY",
    "DOCKER_CONTEXT_ENDPOINT_IDENTITY",
    "DOCKER_CONTAINER_EXECUTION_SCOPE",
    "DOWNSTREAM_COUNTS",
    "EvidenceWriter",
    "EXTERNAL_DECISION_AUTHORITY",
    "GIT_CONFIG_ALLOWED_KEY_NAMES",
    "GIT_CONFIG_ORIGIN_IDENTITY",
    "GIT_ATTRIBUTES_PATTERN_SHA256",
    "GIT_REPOSITORY_CONFIG_POLICY",
    "GIT_REPOSITORY_ATTRIBUTES_POLICY",
    "EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES",
    "EXPECTED_API_BASE_URL",
    "EXPECTED_B0",
    "EXPECTED_GPU_LEASE_PATH",
    "EXPECTED_PROMETHEUS_TARGETS_URL",
    "EXPECTED_X1_RESIDUE_PATHS",
    "FAILED_POD_IDENTITY_FIELDS",
    "HISTORICAL_DECISION_AUTHORITY",
    "HISTORICAL_QUERY_SHA256",
    "HISTORICAL_QUERY_TEXTS",
    "HOST_TOOLCHAIN_ROLES",
    "JOB_SCOPE_CONTRACT",
    "KUBERNETES_CLIENT_CONFIG_POLICY",
    "KUBERNETES_SERVER_IDENTITY",
    "LAUNCHER_COUNTS",
    "LONG_LIVED_SERVICES",
    "LifecycleTimeoutContract",
    "MLFLOW_MIGRATION_HEAD",
    "ONE_SHOT_SERVICES",
    "OBSERVATION_SOURCE_REVISION",
    "PARENT_CHECKPOINT_KINDS",
    "PARENT_CHECKPOINT_ROLES",
    "PARENT_CHECKPOINT_SCHEMAS",
    "PROCESS_CONTAINMENT_CONTRACT",
    "PRESERVED_UNTRACKED_COUNT",
    "ProbeResult",
    "R7ContractError",
    "R7S1ContractError",
    "R7S1EmergencySealError",
    "R7S1EvidenceExistsError",
    "R7S1SuccessInvariantError",
    "R7S1_REQUIRED_INVARIANTS",
    "R7EvidenceExistsError",
    "R7EmergencySealError",
    "R7_REQUIRED_INVARIANTS",
    "R7SuccessInvariantError",
    "RESTORE_COLLECTOR_COUNTS",
    "RESTORE_LIFECYCLE_COUNTS",
    "RESTORE_STAGE_ORDER",
    "RUNTIME_COMPONENTS",
    "ReconcileRestoreHarness",
    "RestoreCheckpoint",
    "RestoreDeadline",
    "RestoreReport",
    "RestoreStage",
    "SCHEMA_VERSION",
    "TimeoutContract",
    "TRUSTED_CHECKPOINT_SCHEMA",
    "UNTRACKED_PATH_SET_ENCODING",
    "WORK_ORDER_ID",
    "LINK_SCAN_SCHEMA",
    "LINK_SCAN_ARGV_SHA256",
    "LINK_SCAN_COMMAND_NAMES",
    "LINK_SCAN_QUERY_SHA256",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_ARGV_SHA256",
    "SNAPSHOT_COMMAND_NAMES",
    "SNAPSHOT_QUERY_SHA256",
    "SNAPSHOT_REPOSITORY",
    "TERMINAL_FENCING_DECISION_SCHEMA",
    "decode_launcher_evidence",
    "canonical_json_bytes",
    "find_verified_decision",
    "git_worktree_blob_oid",
    "r7s1_restore_report",
    "read_parent_checkpoints",
    "parent_map_sha256",
    "sha256_file",
    "validate_external_terminal_fencing",
    "validate_r7s1_manifest",
    "validate_runtime_pins",
    "validate_toolchain_contract",
]
