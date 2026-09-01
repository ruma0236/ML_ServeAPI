from __future__ import annotations

import argparse
import ast
import base64
import copy
import ctypes
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import sys
import urllib.parse
import uuid
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GIT_ROOT = PROJECT_ROOT.parent
CANONICAL_STAGING_BASE = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\staging"
    r"\s8-v4\x1-clock-phase-b2-r7s1-restore"
)
CANONICAL_OUTPUT_BASE = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private"
    r"\s8-v4\x1-clock-phase-b2-r7s1-restore"
)
EXPECTED_B0_UID = "cfdab424-dcc5-4d5f-a46f-ae7530441ef4"
EXPECTED_B0_IMAGE = (
    "enterprise-vision-mlops-efficientnet-serving@"
    "sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a"
)
ETW_AMENDMENT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private"
    r"\s8-v4\x1-clock-phase-b2-failure-seals"
    r"\x1-clock-phase-b2-r3-failure-seal-20260831T135958Z-0a68addf"
    r"\etw-contract-amendment.json"
)
ETW_AMENDMENT_SHA256 = "71ddc50a2a91f707b8183a19c87f490bdad8421ab18446dceb21622bc3439715"
RUNTIME_PATHS = {
    "builder": Path("scripts/dev/prepare_x1_phase_b2_r7s1_bundle.py"),
    "core": Path("src/evm/scale_validation/phase_b2_r7s1.py"),
    "process": Path("src/evm/scale_validation/phase_b2_r7_process.py"),
    "runner": Path("scripts/dev/run_x1_phase_b2_r7s1.py"),
    "validator": Path("scripts/dev/validate_phase_b2_r7s1_bundle.ps1"),
    "docker_compose": Path("docker-compose.yml"),
}
BOOTSTRAP_PROCESS_SHA256 = "031b4c3b3843a0921b4b058ff27453bad513b969fcb4f8789fa0f2df9a7843bd"
BOOTSTRAP_PROCESS_BYTES = 65_486

RESTORE_MODE = "restore-only"
REQUIRED_PARENT_ROLES = (
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
PARENT_KINDS = {role: role for role in REQUIRED_PARENT_ROLES}
PARENT_SCHEMAS = {
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
CONTAINER_NAMES = {
    "airflow-postgres": "evm-airflow-postgres",
    "airflow-scheduler": "evm-airflow-scheduler",
    "airflow-webserver": "evm-airflow-webserver",
    "api": "evm-api",
    "control-panel": "evm-control-panel",
    "control-plane-postgres": "evm-control-plane-postgres",
    "grafana": "evm-grafana",
    "minio": "evm-minio",
    "mlflow": "evm-mlflow",
    "otel-collector": "evm-otel-collector",
    "postgres": "evm-postgres",
    "prometheus": "evm-prometheus",
    "task-queue-worker": "evm-task-queue-worker",
    "airflow-init": "evm-airflow-init",
    "minio-create-buckets": "evm-minio-init",
}
HEALTHCHECK_EXPECTED = {
    name: name
    in {
        "airflow-postgres",
        "airflow-scheduler",
        "airflow-webserver",
        "api",
        "control-panel",
        "control-plane-postgres",
        "mlflow",
        "postgres",
        "task-queue-worker",
    }
    for name in (*LONG_LIVED_SERVICES, *ONE_SHOT_SERVICES)
}
AIRFLOW_MIGRATION_HEAD = "5f2621c13b39"
MLFLOW_MIGRATION_HEAD = "0584bdc529eb"
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
RUNTIME_STATE_SCHEMA = "evm.s8_v4.x1_phase_b2_r7s1_runtime_state_pins.v1"
EXTERNAL_FENCING_PINS_SCHEMA = "evm.s8_v4.x1_phase_b2_r7s1_external_terminal_fencing_pins.v1"
EXTERNAL_FENCING_DECISION_SCHEMA = "s8-v4-x1-phase-b2-r7s1-terminal-fencing-decision/v1"
TRUSTED_CHECKPOINT_SCHEMA = "s8-v4-x1-phase-b2-r7s1-trusted-terminal-fencing-checkpoint/v1"
TOOLCHAIN_PINS_SCHEMA = "evm.s8_v4.x1_phase_b2_r7s1_toolchain_pins.v1"
PYTHON_DISTRIBUTION_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-python-distribution-readback/v1"
WINDOWS_TCB_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-windows-tcb-readback/v1"
WSL_RUNTIME_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-wsl-runtime-readback/v1"
CONTAINER_PSQL_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-container-psql-readback/v1"
GIT_DISTRIBUTION_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-git-distribution-readback/v1"
GIT_REPOSITORY_CONFIG_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-git-repository-config-readback/v1"
GIT_REPOSITORY_ATTRIBUTES_READBACK_SCHEMA = (
    "s8-v4-x1-phase-b2-r7s1-git-repository-attributes-readback/v1"
)
DOCKER_CLIENT_CONFIG_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-docker-client-config-readback/v1"
KUBERNETES_CLIENT_CONFIG_READBACK_SCHEMA = (
    "s8-v4-x1-phase-b2-r7s1-kubernetes-client-config-readback/v1"
)
DOCKER_CONTAINER_EXECUTION_SCOPE = {
    "schema": "s8-v4-x1-phase-b2-r7s1-docker-container-exec-tcb/v1",
    "windows_job_accounting": "docker_cli_and_windows_descendants_only",
    "docker_daemon_container_exec_tcb": True,
    "linux_container_descendants_job_accounted": False,
    "command_policy": "exact_read_only_psql_select_allowlist_no_psqlrc",
    "timeout_or_residual_followup_allowed": False,
}
PROCESS_SCOPE_BOUNDARIES = {
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
}
PYTHON_TREE_ENCODING = (
    "ordinal-relative-posix-utf8-nul-size-nul-sha256-nul;"
    "include=*.exe,*.dll,python*.zip,DLLs/**,Lib/**;"
    "exclude=Lib/site-packages/**,**/__pycache__/**,**/*.pyc,**/*.pyo"
)
PYTHON_INCLUDED_ROOTS = ["*.exe", "*.dll", "python*.zip", "DLLs/**", "Lib/**"]
PYTHON_EXCLUDED_ROOTS = [
    "Lib/site-packages/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
]
HOST_TOOLCHAIN_ROLES = (
    "python",
    "docker",
    "docker_compose",
    "kubectl",
    "wsl",
    "powershell",
    "git",
)
EXPECTED_GIT_PATH = Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
EXPECTED_GIT_CONFIG_PATH = Path(r"C:\Users\mlops\EnterpriseMLOps_Project\.git\config")
EXPECTED_GIT_CONFIG_SHA256 = "aefce0bafe9863032f40ed1f62d91c339a321ea61303b77941ec7e36c30028fa"
EXPECTED_GIT_CONFIG_BYTES = 787
CANONICAL_GIT_REMOTE_URL = "https://github.com/ruma0236/ML_ServeAPI.git"
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
EXPECTED_GIT_ATTRIBUTES_PATH = Path(
    r"C:\Users\mlops\EnterpriseMLOps_Project\enterprise-vision-mlops\.gitattributes"
)
EXPECTED_GIT_ATTRIBUTES_SHA256 = "d7303b6f3a537f1a8382adcf72c0ef49e4aa15261263d8f2c70a475f24f57fa5"
EXPECTED_GIT_ATTRIBUTES_BYTES = 577
EXPECTED_GIT_TOP_ATTRIBUTES_PATH = Path(r"C:\Users\mlops\EnterpriseMLOps_Project\.gitattributes")
EXPECTED_GIT_INFO_ATTRIBUTES_PATH = Path(
    r"C:\Users\mlops\EnterpriseMLOps_Project\.git\info\attributes"
)
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
    "2b60b4c1a1cd70e2f4ade33310be82c61d8a4503ae8d55074fc752bbc9486e11",
    "2ca964ae17fe6f2b7f47f16540a299c0f7b3380f796e7ac8493bfcee7893378a",
    "21eb880d14a0ccc39dd9fc3798fbfb2d8e82101187e434e6020a575662c3c7d0",
    "36313b00defa02c2145da13d795d2d4201ed45a044b952084d5638806c8d429b",
    "d539d33f0ac4e88605ec0ced396b039f8743174ddbed63c54b9792542dc729f3",
)
GIT_REPOSITORY_ATTRIBUTES_POLICY = {
    "schema": "s8-v4-x1-phase-b2-r7s1-git-attributes-policy/v1",
    "rule_count": 15,
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
EXPECTED_DOCKER_CLIENT_CONFIG_PATH = Path(r"C:\Users\opop0\.docker\config.json")
EXPECTED_DOCKER_CLIENT_CONFIG_SHA256 = (
    "7b2ec346b548b5bdf0bcd95923e800fe50ac50f0b2678e874fc18124ac5b22b6"
)
EXPECTED_DOCKER_CLIENT_CONFIG_BYTES = 78
EXPECTED_DOCKER_CONTEXT_METADATA_PATH = Path(
    r"C:\Users\opop0\.docker\contexts\meta"
    r"\fe9c6bd7a66301f49ca9b6a70b217107cd1284598bfc254700c989b916da791e\meta.json"
)
EXPECTED_DOCKER_CONTEXT_METADATA_SHA256 = (
    "162ea41b361225a824608cf6c714d7710d69aa3c645bfbbf98104b4fce06cd09"
)
EXPECTED_DOCKER_CONTEXT_METADATA_BYTES = 318
EXPECTED_DOCKER_CONTEXT_TLS_PATH = Path(
    r"C:\Users\opop0\.docker\contexts\tls"
    r"\fe9c6bd7a66301f49ca9b6a70b217107cd1284598bfc254700c989b916da791e"
)
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
            "DOCKER_CONFIG": str(EXPECTED_DOCKER_CLIENT_CONFIG_PATH.parent),
            "DOCKER_CONTEXT": "desktop-linux",
            "DOCKER_CLI_HINTS": "false",
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "COMPOSE_ANSI": "never",
            "COMPOSE_PROGRESS": "plain",
        },
    },
    "docker_global_arguments": [
        "--config",
        str(EXPECTED_DOCKER_CLIENT_CONFIG_PATH.parent),
        "--context",
        "desktop-linux",
    ],
    "standalone_compose_context_transport": "child_environment_only",
    "standalone_compose_required_argument_names": ["-p", "-f", "--project-directory"],
}
EXPECTED_KUBERNETES_CLIENT_CONFIG_PATH = Path(r"C:\Users\opop0\.kube\config")
EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256 = (
    "0d9a540954fb7b9b1bf016cffd399022d1d19f2bd0617a0562912611edf9d085"
)
EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES = 5_692
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
        "set_variables": {"KUBECONFIG": str(EXPECTED_KUBERNETES_CLIENT_CONFIG_PATH)},
    },
    "required_global_arguments": [
        "--kubeconfig",
        str(EXPECTED_KUBERNETES_CLIENT_CONFIG_PATH),
        "--context",
        "docker-desktop",
        "--request-timeout=8s",
    ],
}
EXPECTED_DOCKER_COMPOSE_PATH = Path(
    r"C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe"
)
EXPECTED_GIT_ROOT = Path(r"C:\Program Files\Git")
GIT_TREE_ENCODING = (
    "ordinal-relative-posix-utf8-nul-size-nul-sha256-nul;all-regular-files;reparse=reject"
)
_GIT_ENVIRONMENT_SCRUB_EXACT = {
    "all_proxy",
    "curl_ca_bundle",
    "editor",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "pager",
    "request_method",
    "ssh_agent_pid",
    "ssh_askpass",
    "ssh_askpass_require",
    "ssh_auth_sock",
    "ssl_cert_dir",
    "ssl_cert_file",
    "visual",
    "xdg_config_home",
}
_GIT_ENVIRONMENT_EXACT = {
    "GCM_INTERACTIVE": "never",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "NUL",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "",
    "GIT_TERMINAL_PROMPT": "0",
}
_VERIFIED_GIT_REPOSITORY_CONFIG: dict[str, Any] | None = None
_VERIFIED_GIT_REPOSITORY_ATTRIBUTES: dict[str, Any] | None = None
HISTORICAL_SNAPSHOT_SCHEMA = "s8-v4-x1-phase-b2-pre-r8-historical-snapshot/v1"
TARGET_LINK_SCAN_SCHEMA = "s8-v4-x1-phase-b2-pre-r8-target-link-scan/v1"
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
    "kubernetes_command": ("484c862112f56fd18fc582894f1f9c76a812b7e9efe2a2d22eb5396c4a694541"),
}
SNAPSHOT_REPOSITORY = r"C:\Users\mlops\EnterpriseMLOps_Project\enterprise-vision-mlops"
EXTERNAL_FENCING_MIN_OBSERVATION_GAP_SECONDS = 30.0
EXTERNAL_FENCING_MAX_AGE_SECONDS = 3600.0
CONTROL_PLANE_LINK_TABLES = {
    "entities",
    "idempotency_keys",
    "lifecycle_claims",
    "side_effect_outbox",
    "task_admission_queue",
    "task_dispatch_effects",
    "s6bm_causal_events",
    "s6bm_route_revisions",
}
AIRFLOW_LINK_TABLES = {
    "dag_run",
    "task_instance",
    "xcom",
    "rendered_task_instance_fields",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MLFLOW_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
NONCE64 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_TOP_LEVEL_KEYS = {
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
SNAPSHOT_OBSERVED_KEYS = {
    "compose_project_containers",
    "control_plane_execution_links",
    "control_plane_history",
    "kubernetes_failed_pods",
    "kubernetes_jobs",
    "mlflow_activity",
    "queue_claims",
    "windows_global_residuals",
    "wsl_global_residuals",
}
MLFLOW_ACTIVITY_KEYS = {
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
LINK_SCAN_TOP_LEVEL_KEYS = {
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


class BundleBuildError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BundleBuildError(f"bundle_file_exists:{path}") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("exclusive bundle write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normcase_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _path_is_within(path: Path, directory: Path) -> bool:
    path_text = _normcase_path(path)
    directory_text = _normcase_path(directory).rstrip("\\/")
    return path_text == directory_text or path_text.startswith(directory_text + os.sep)


def _assert_no_reparse_ancestors(path: Path, *, label: str) -> None:
    """Reject an existing reparse point anywhere in a future path's ancestry."""

    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    candidate = Path(os.path.abspath(str(path)))
    while True:
        try:
            candidate_stat = candidate.stat(follow_symlinks=False)
            if getattr(candidate_stat, "st_file_attributes", 0) & reparse_flag:
                raise BundleBuildError(f"{label}_reparse_ancestor:{candidate}")
        except FileNotFoundError:
            pass
        if candidate.parent == candidate:
            break
        candidate = candidate.parent


def validate_canonical_run_locations(
    *, run_id: str, staging_directory: Path, output_directory: Path
) -> tuple[Path, Path, Path]:
    """Bind a run identity to its only admissible staging/output locations."""

    staging = Path(staging_directory)
    output = Path(output_directory)
    if not staging.is_absolute() or not output.is_absolute():
        raise BundleBuildError("run_locations_absolute_required")
    expected_staging = CANONICAL_STAGING_BASE / run_id
    expected_output = CANONICAL_OUTPUT_BASE / run_id
    expected_emergency = CANONICAL_OUTPUT_BASE / f"{run_id}-emergency-seal"
    if _normcase_path(staging) != _normcase_path(expected_staging):
        raise BundleBuildError("staging_directory_not_canonical_run_location")
    if _normcase_path(output) != _normcase_path(expected_output):
        raise BundleBuildError("output_directory_not_canonical_run_location")
    if staging.name != run_id or output.name != run_id:
        raise BundleBuildError("run_location_leaf_must_equal_run_id")
    _assert_no_reparse_ancestors(staging, label="staging_directory")
    _assert_no_reparse_ancestors(output, label="output_directory")
    _assert_no_reparse_ancestors(expected_emergency, label="emergency_seal_directory")
    resolved_staging = staging.resolve(strict=False)
    resolved_output = output.resolve(strict=False)
    resolved_emergency = expected_emergency.resolve(strict=False)
    if _normcase_path(resolved_staging.parent) != _normcase_path(
        CANONICAL_STAGING_BASE.resolve(strict=False)
    ):
        raise BundleBuildError("staging_directory_resolved_outside_canonical_base")
    if _normcase_path(resolved_output.parent) != _normcase_path(
        CANONICAL_OUTPUT_BASE.resolve(strict=False)
    ):
        raise BundleBuildError("output_directory_resolved_outside_canonical_base")
    if _normcase_path(resolved_emergency.parent) != _normcase_path(
        CANONICAL_OUTPUT_BASE.resolve(strict=False)
    ):
        raise BundleBuildError("emergency_seal_directory_resolved_outside_canonical_base")
    if (
        len(
            {
                _normcase_path(resolved_staging),
                _normcase_path(resolved_output),
                _normcase_path(resolved_emergency),
            }
        )
        != 3
    ):
        raise BundleBuildError("run_locations_must_be_distinct")
    return resolved_staging, resolved_output, resolved_emergency


def path_filesystem_identity(path: Path) -> dict[str, Any]:
    """Read back the Windows volume/filesystem identity without spawning a child."""

    candidate = Path(path)
    while not candidate.exists():
        if candidate.parent == candidate:
            raise BundleBuildError(f"path_existing_ancestor_required:{path}")
        candidate = candidate.parent
    root_buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetVolumePathNameW(  # type: ignore[attr-defined]
        str(candidate), root_buffer, len(root_buffer)
    ):
        raise BundleBuildError(f"path_volume_root_readback_failed:{path}")
    volume_name = ctypes.create_unicode_buffer(32768)
    filesystem_name = ctypes.create_unicode_buffer(32768)
    serial = ctypes.c_ulong()
    maximum_component = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    if not ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
        root_buffer.value,
        volume_name,
        len(volume_name),
        ctypes.byref(serial),
        ctypes.byref(maximum_component),
        ctypes.byref(flags),
        filesystem_name,
        len(filesystem_name),
    ):
        raise BundleBuildError(f"path_filesystem_identity_readback_failed:{path}")
    return {
        "resolved_path": str(Path(path).resolve(strict=False)),
        "volume_root": root_buffer.value,
        "volume_serial": f"{serial.value:08x}",
        "filesystem": filesystem_name.value,
        "device_id": int(candidate.stat().st_dev),
    }


def _run_contained(
    command: list[str],
    *,
    name: str,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    """Run one builder child in the existing no-kill Job Object gate."""

    lexical_project_root = Path(os.path.abspath(__file__)).parents[2]
    lexical_process_path = lexical_project_root / RUNTIME_PATHS["process"]
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    candidate = lexical_process_path
    while True:
        candidate_stat = candidate.stat(follow_symlinks=False)
        if getattr(candidate_stat, "st_file_attributes", 0) & reparse_flag:
            raise BundleBuildError(f"contained_process_reparse_ancestor:{candidate}")
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    process_path = lexical_process_path.resolve()
    if process_path != (PROJECT_ROOT / RUNTIME_PATHS["process"]).resolve():
        raise BundleBuildError("contained_process_outside_isolated_project")
    process_stat = process_path.stat()
    measured_before = sha256_file(process_path)
    if (
        process_stat.st_size != BOOTSTRAP_PROCESS_BYTES
        or measured_before != BOOTSTRAP_PROCESS_SHA256
    ):
        raise BundleBuildError("contained_process_bootstrap_pin_mismatch")
    module_name = "_evm_r7s1_builder_verified_process"
    process_module = sys.modules.get(module_name)
    if process_module is None:
        loader = importlib.machinery.SourceFileLoader(module_name, str(process_path))
        spec = importlib.util.spec_from_file_location(module_name, process_path, loader=loader)
        if spec is None or spec.loader is None:
            raise BundleBuildError("contained_process_module_spec_unavailable")
        process_module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = process_module
        try:
            spec.loader.exec_module(process_module)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
    loaded_file = getattr(process_module, "__file__", None)
    loaded_spec = getattr(process_module, "__spec__", None)
    loaded_origin = getattr(loaded_spec, "origin", None)
    if not isinstance(loaded_file, str) or not isinstance(loaded_origin, str):
        raise BundleBuildError("contained_process_module_identity_unavailable")
    loaded_path = Path(loaded_file).resolve()
    if (
        loaded_path != process_path
        or Path(loaded_origin).resolve() != process_path
        or sha256_file(loaded_path) != BOOTSTRAP_PROCESS_SHA256
        or loaded_path.stat().st_size != BOOTSTRAP_PROCESS_BYTES
    ):
        raise BundleBuildError("contained_process_module_path_or_sha256_mismatch")
    timeout_type = process_module.TimeoutContract
    runner_type = process_module.WindowsJobProcessRunner
    outcome = runner_type(timeout_type()).run(
        command,
        name=name,
        cwd=cwd,
        env=env,
    )
    if (
        outcome.timed_out
        or outcome.cancelled
        or outcome.manual_intervention_required
        or outcome.residual_pids
        or not outcome.active_process_zero
        or not outcome.streams_drained
        or not outcome.identity_coverage_complete
        or not outcome.safe_for_followup
        or outcome.forced_termination_attempts != 0
    ):
        raise BundleBuildError(
            f"contained_child_not_safe_for_followup:{name}:"
            f"{json.dumps(outcome.to_dict(), sort_keys=True)}"
        )
    return outcome


def _parse_git_repository_config(payload: bytes) -> dict[str, str]:
    """Parse the deliberately simple, pinned Git config without invoking Git."""

    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise BundleBuildError("git_repository_config_utf8_invalid") from exc
    if "\x00" in text:
        raise BundleBuildError("git_repository_config_nul_forbidden")
    section_pattern = re.compile(r'^\[([A-Za-z][A-Za-z0-9.-]*)(?:[ \t]+"([^"\\\r\n]+)")?\][ \t]*$')
    key_pattern = re.compile(r"^([A-Za-z][A-Za-z0-9.-]*)[ \t]*=[ \t]*(.*?)[ \t]*$")
    current_section: str | None = None
    entries: dict[str, str] = {}
    for ordinal, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = section_pattern.fullmatch(line)
        if section_match is not None:
            section = section_match.group(1).casefold()
            subsection = section_match.group(2)
            current_section = (
                section if subsection is None else f"{section}.{subsection.casefold()}"
            )
            continue
        key_match = key_pattern.fullmatch(line)
        if current_section is None or key_match is None:
            raise BundleBuildError(f"git_repository_config_syntax_not_canonical:line={ordinal}")
        value = key_match.group(2)
        if not value or value.startswith(("!", '"')) or value.endswith("\\"):
            raise BundleBuildError(f"git_repository_config_value_not_canonical:line={ordinal}")
        name = f"{current_section}.{key_match.group(1).casefold()}"
        if name in entries:
            raise BundleBuildError(f"git_repository_config_duplicate_key:{name}")
        entries[name] = value
    return entries


def _verify_git_repository_config_pin(pin: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read the config that can alter Git helper/executable discovery."""

    if set(pin) != {"path", "sha256", "bytes", "policy", "readback"}:
        raise BundleBuildError("toolchain_git_repository_config_fields_mismatch")
    config_path = _normal_path(pin["path"], "toolchain_git_repository_config")
    expected_path = EXPECTED_GIT_CONFIG_PATH.resolve()
    if config_path != expected_path or config_path != (GIT_ROOT / ".git" / "config").resolve():
        raise BundleBuildError("toolchain_git_repository_config_path_mismatch")
    expected_sha = str(pin["sha256"]).lower()
    expected_bytes = pin["bytes"]
    if (
        expected_sha != EXPECTED_GIT_CONFIG_SHA256
        or isinstance(expected_bytes, bool)
        or expected_bytes != EXPECTED_GIT_CONFIG_BYTES
        or pin["policy"] != GIT_REPOSITORY_CONFIG_POLICY
    ):
        raise BundleBuildError("toolchain_git_repository_config_pin_mismatch")
    _assert_no_reparse_ancestors(config_path, label="toolchain_git_repository_config")
    try:
        payload = config_path.read_bytes()
        identity = config_path.stat(follow_symlinks=False)
    except OSError as exc:
        raise BundleBuildError("toolchain_git_repository_config_unreadable") from exc
    if (
        not stat.S_ISREG(identity.st_mode)
        or identity.st_size != expected_bytes
        or len(payload) != expected_bytes
        or hashlib.sha256(payload).hexdigest() != expected_sha
    ):
        raise BundleBuildError("toolchain_git_repository_config_measured_identity_mismatch")
    config_worktree = config_path.with_name("config.worktree")
    _assert_no_reparse_ancestors(config_worktree, label="toolchain_git_config_worktree")
    if config_worktree.exists():
        raise BundleBuildError("toolchain_git_config_worktree_must_be_absent")

    entries = _parse_git_repository_config(payload)
    key_names = sorted(entries)
    if key_names != list(GIT_CONFIG_ALLOWED_KEY_NAMES):
        raise BundleBuildError("toolchain_git_repository_config_key_policy_mismatch")
    exact_values = {
        "core.repositoryformatversion": "0",
        "core.filemode": "false",
        "core.bare": "false",
        "core.logallrefupdates": "true",
        "core.symlinks": "false",
        "core.ignorecase": "true",
        "extensions.worktreeconfig": "true",
        "remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*",
    }
    for branch in (
        "codex/local-infra-mvp",
        "codex/mac-mini-worker",
        "codex/distributed-scale-validation-plan",
        "codex/x1-resume-results-20260825-215716",
    ):
        exact_values[f"branch.{branch}.remote"] = "origin"
        exact_values[f"branch.{branch}.merge"] = f"refs/heads/{branch}"
    if any(entries.get(name) != value for name, value in exact_values.items()):
        raise BundleBuildError("toolchain_git_repository_config_value_policy_mismatch")
    if not entries.get("user.name") or not entries.get("user.email"):
        raise BundleBuildError("toolchain_git_repository_config_user_fields_empty")
    raw_origin = entries.get("remote.origin.url", "")
    try:
        origin = urllib.parse.urlsplit(raw_origin)
        origin_port = origin.port
    except ValueError as exc:
        raise BundleBuildError("toolchain_git_repository_config_origin_url_invalid") from exc
    origin_identity = {
        "scheme": origin.scheme.casefold(),
        "host": (origin.hostname or "").casefold(),
        "path_sha256": hashlib.sha256(origin.path.encode("utf-8")).hexdigest(),
    }
    if (
        raw_origin != CANONICAL_GIT_REMOTE_URL
        or origin_identity != GIT_CONFIG_ORIGIN_IDENTITY
        or origin.username is not None
        or origin.password is not None
        or origin_port is not None
        or origin.query
        or origin.fragment
    ):
        raise BundleBuildError("toolchain_git_repository_config_origin_policy_mismatch")
    return {
        "path": str(config_path),
        "sha256": expected_sha,
        "bytes": expected_bytes,
        "key_names": key_names,
        "origin_identity": origin_identity,
        "config_worktree_absent": True,
        "policy_sha256": hashlib.sha256(
            canonical_json_bytes(GIT_REPOSITORY_CONFIG_POLICY)
        ).hexdigest(),
    }


def _verify_git_repository_attributes_pin(pin: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read the complete, non-executable repository attributes policy."""

    if set(pin) != {"path", "sha256", "bytes", "policy", "readback"}:
        raise BundleBuildError("toolchain_git_repository_attributes_fields_mismatch")
    attributes_path = _normal_path(pin["path"], "toolchain_git_repository_attributes")
    if (
        attributes_path != EXPECTED_GIT_ATTRIBUTES_PATH.resolve()
        or attributes_path != (PROJECT_ROOT / ".gitattributes").resolve()
        or str(pin["sha256"]).lower() != EXPECTED_GIT_ATTRIBUTES_SHA256
        or isinstance(pin["bytes"], bool)
        or pin["bytes"] != EXPECTED_GIT_ATTRIBUTES_BYTES
        or pin["policy"] != GIT_REPOSITORY_ATTRIBUTES_POLICY
    ):
        raise BundleBuildError("toolchain_git_repository_attributes_pin_mismatch")
    _assert_no_reparse_ancestors(attributes_path, label="toolchain_git_repository_attributes")
    try:
        payload = attributes_path.read_bytes()
        identity = attributes_path.stat(follow_symlinks=False)
    except OSError as exc:
        raise BundleBuildError("toolchain_git_repository_attributes_unreadable") from exc
    if (
        not stat.S_ISREG(identity.st_mode)
        or identity.st_size != EXPECTED_GIT_ATTRIBUTES_BYTES
        or len(payload) != EXPECTED_GIT_ATTRIBUTES_BYTES
        or hashlib.sha256(payload).hexdigest() != EXPECTED_GIT_ATTRIBUTES_SHA256
    ):
        raise BundleBuildError("toolchain_git_repository_attributes_measured_identity_mismatch")
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise BundleBuildError("toolchain_git_repository_attributes_utf8_required") from exc
    rules = [line.strip() for line in text.splitlines() if line.strip()]
    pattern_sha256: list[str] = []
    for rule in rules:
        fields = rule.split()
        if len(fields) != 3 or fields[1:] != ["text", "eol=lf"]:
            raise BundleBuildError("toolchain_git_repository_attributes_rule_policy_mismatch")
        pattern_sha256.append(hashlib.sha256(fields[0].encode("utf-8")).hexdigest())
    if len(rules) != 15 or pattern_sha256 != list(GIT_ATTRIBUTES_PATTERN_SHA256):
        raise BundleBuildError("toolchain_git_repository_attributes_pattern_policy_mismatch")
    for label, absent_path in (
        ("git_top_level_attributes", EXPECTED_GIT_TOP_ATTRIBUTES_PATH.resolve()),
        ("git_info_attributes", EXPECTED_GIT_INFO_ATTRIBUTES_PATH.resolve()),
    ):
        _assert_no_reparse_ancestors(absent_path, label=f"toolchain_{label}")
        if absent_path.exists():
            raise BundleBuildError(f"toolchain_{label}_must_be_absent")
    return {
        "path": str(attributes_path),
        "sha256": EXPECTED_GIT_ATTRIBUTES_SHA256,
        "bytes": EXPECTED_GIT_ATTRIBUTES_BYTES,
        "rule_count": 15,
        "pattern_sha256": pattern_sha256,
        "attribute_tokens": ["text", "eol=lf"],
        "forbidden_attributes_absent": True,
        "git_top_level_attributes_absent": True,
        "git_info_attributes_absent": True,
        "system_attributes_disabled": True,
        "policy_sha256": hashlib.sha256(
            canonical_json_bytes(GIT_REPOSITORY_ATTRIBUTES_POLICY)
        ).hexdigest(),
    }


def _safe_git_environment() -> dict[str, str]:
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.casefold().startswith("git_")
        and key.casefold() not in _GIT_ENVIRONMENT_SCRUB_EXACT
    }
    clean.update(_GIT_ENVIRONMENT_EXACT)
    return clean


def _scrubbed_client_environment(policy: Mapping[str, Any]) -> dict[str, str]:
    contract = policy.get("child_environment")
    if not isinstance(contract, Mapping):
        raise BundleBuildError("client_child_environment_contract_required")
    prefixes = tuple(str(item).casefold() for item in contract.get("scrub_prefixes", ()))
    names = {str(item).casefold() for item in contract.get("scrub_names", ())}
    suffixes = tuple(str(item).casefold() for item in contract.get("scrub_suffixes", ()))
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.casefold().startswith(prefixes)
        and key.casefold() not in names
        and not key.casefold().endswith(suffixes)
    }
    set_variables = contract.get("set_variables")
    if not isinstance(set_variables, Mapping):
        raise BundleBuildError("client_child_environment_set_variables_required")
    clean.update({str(key): str(value) for key, value in set_variables.items()})
    return clean


def _verified_git_config_before_child() -> dict[str, Any]:
    if _VERIFIED_GIT_REPOSITORY_CONFIG is None or _VERIFIED_GIT_REPOSITORY_ATTRIBUTES is None:
        raise BundleBuildError("git_repository_config_not_verified_before_child")
    observed = _verify_git_repository_config_pin(_VERIFIED_GIT_REPOSITORY_CONFIG)
    _verify_git_repository_attributes_pin(_VERIFIED_GIT_REPOSITORY_ATTRIBUTES)
    return observed


def git(repository: Path, *arguments: str) -> str:
    _verified_git_config_before_child()
    outcome = _run_contained(
        [
            str(EXPECTED_GIT_PATH),
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repository),
            *arguments,
        ],
        name="r7s1-builder-git-read",
        cwd=repository,
        env=_safe_git_environment(),
    )
    if outcome.return_code != 0:
        raise BundleBuildError(f"git_command_failed:{arguments}:{outcome.stderr.strip()}")
    return outcome.stdout.strip()


def git_bytes(repository: Path, *arguments: str) -> bytes:
    _verified_git_config_before_child()
    outcome = _run_contained(
        [
            str(EXPECTED_GIT_PATH),
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repository),
            *arguments,
        ],
        name="r7s1-builder-git-bytes-read",
        cwd=repository,
        env=_safe_git_environment(),
    )
    if outcome.return_code != 0:
        raise BundleBuildError(f"git_bytes_command_failed:{arguments}:{outcome.stderr.strip()}")
    return outcome.stdout.encode("utf-8", errors="strict")


def git_remote_revision(branch: str) -> str:
    _verified_git_config_before_child()
    outcome = _run_contained(
        [
            str(EXPECTED_GIT_PATH),
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "ls-remote",
            "--exit-code",
            CANONICAL_GIT_REMOTE_URL,
            f"refs/heads/{branch}",
        ],
        name="r7s1-builder-git-remote-revision-read",
        cwd=EXPECTED_GIT_ROOT.parent,
        env=_safe_git_environment(),
    )
    if outcome.return_code != 0:
        raise BundleBuildError("git_remote_revision_read_failed")
    remote_parts = outcome.stdout.strip().split()
    return remote_parts[0].lower() if len(remote_parts) == 2 else ""


def untracked_identity(repository: Path) -> tuple[int, str]:
    raw = git_bytes(
        repository,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    parts: list[bytes] = []
    for record in records:
        if record.startswith(b"?? "):
            parts.append(record[3:])
        elif len(record) >= 3:
            raise BundleBuildError("tracked_status_present_in_untracked_identity")
        else:
            raise BundleBuildError("git_status_porcelain_record_invalid")
    try:
        paths = [part.decode("utf-8", errors="strict") for part in parts]
    except UnicodeDecodeError as exc:
        raise BundleBuildError("untracked_path_not_utf8") from exc
    if len(paths) != len(set(paths)):
        raise BundleBuildError("untracked_paths_duplicate")
    ordered = sorted(paths)
    digest = hashlib.sha256()
    for path in ordered:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
    return len(ordered), digest.hexdigest()


def git_head_blob_oid(repository: Path, path: Path) -> str:
    relative = path.resolve().relative_to(repository.resolve()).as_posix()
    value = git(repository, "rev-parse", f"HEAD:{relative}")
    if len(value) != 40:
        raise BundleBuildError(f"git_head_blob_oid_invalid:{relative}:{value}")
    return value


def worktree_blob_oid(path: Path) -> str:
    """Hash the exact worktree bytes using Git's blob object encoding."""

    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def git_normalized_worktree_blob_oid(repository: Path, path: Path) -> str:
    """Apply only the pinned Git attribute/EOL policy to worktree bytes."""

    absolute = path.resolve()
    relative = absolute.relative_to(repository.resolve()).as_posix()
    value = git(
        repository,
        "-c",
        "core.autocrlf=true",
        "hash-object",
        f"--path={relative}",
        str(absolute),
    ).lower()
    if len(value) != 40 or not HEX40.fullmatch(value):
        raise BundleBuildError(f"git_normalized_worktree_blob_oid_invalid:{relative}:{value}")
    return value


def source_pin(project_root: Path, path: Path) -> dict[str, Any]:
    absolute = (project_root / path).resolve()
    if not absolute.is_file():
        raise BundleBuildError(f"runtime_source_missing:{path}")
    repository = project_root.parent.resolve()
    head_blob = git_head_blob_oid(repository, absolute)
    normalized_blob = git_normalized_worktree_blob_oid(repository, absolute)
    if normalized_blob != head_blob:
        raise BundleBuildError(f"runtime_source_normalized_blob_mismatch:{path}")
    return {
        "path": str(absolute),
        "sha256": sha256_file(absolute),
        "worktree_blob_oid": worktree_blob_oid(absolute),
        "head_blob_oid": head_blob,
        "bytes": absolute.stat().st_size,
    }


def python_distribution_tree_identity(base_prefix: Path) -> tuple[str, int]:
    base = base_prefix.resolve()
    if not base.is_dir():
        raise BundleBuildError(f"python_distribution_base_prefix_missing:{base}")
    files: dict[str, Path] = {}
    for relative_root in (Path("DLLs"), Path("Lib")):
        root = base / relative_root
        if not root.is_dir():
            raise BundleBuildError(f"python_distribution_root_missing:{relative_root.as_posix()}")
        for candidate in root.rglob("*"):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(base)
            if (
                relative.parts[:2] == ("Lib", "site-packages")
                or "__pycache__" in relative.parts
                or candidate.suffix.lower() in {".pyc", ".pyo"}
            ):
                continue
            files[relative.as_posix()] = candidate
    for pattern in ("*.exe", "*.dll", "python*.zip"):
        for candidate in base.glob(pattern):
            if candidate.is_file() and not candidate.is_symlink():
                files[candidate.relative_to(base).as_posix()] = candidate
    if not files:
        raise BundleBuildError("python_distribution_tree_empty")
    digest = hashlib.sha256()
    for relative in sorted(files):
        candidate = files[relative]
        record = (
            relative.encode("utf-8")
            + b"\0"
            + str(candidate.stat().st_size).encode("ascii")
            + b"\0"
            + sha256_file(candidate).encode("ascii")
            + b"\0"
        )
        digest.update(record)
    return digest.hexdigest(), len(files)


def git_distribution_tree_identity(root: Path) -> tuple[str, int]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise BundleBuildError(f"git_distribution_root_missing:{resolved}")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    files: dict[str, Path] = {}
    stack = [resolved]
    while stack:
        directory = stack.pop()
        if directory.stat(follow_symlinks=False).st_file_attributes & reparse_flag:
            raise BundleBuildError(f"git_distribution_reparse_entry:{directory}")
        with os.scandir(directory) as entries:
            for entry in entries:
                attributes = entry.stat(follow_symlinks=False).st_file_attributes
                if attributes & reparse_flag:
                    raise BundleBuildError(f"git_distribution_reparse_entry:{entry.path}")
                candidate = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(candidate)
                elif entry.is_file(follow_symlinks=False):
                    files[candidate.relative_to(resolved).as_posix()] = candidate
    if not files:
        raise BundleBuildError("git_distribution_tree_empty")
    digest = hashlib.sha256()
    for relative in sorted(files):
        candidate = files[relative]
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(candidate.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(sha256_file(candidate).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest(), len(files)


def _validate_artifact_pin(value: Any, *, label: str, schema: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BundleBuildError(f"{label}_object_required")
    require_exact_keys(value, {"path", "sha256", "schema"}, label)
    path = _normal_path(value["path"], label)
    sha = str(value["sha256"]).lower()
    if value["schema"] != schema:
        raise BundleBuildError(f"{label}_schema_mismatch")
    if not HEX64.fullmatch(sha) or sha256_file(path) != sha:
        raise BundleBuildError(f"{label}_sha256_mismatch")
    payload = read_json_object(path, label=label)
    if payload.get("schema") != schema or payload.get("status") != "verified":
        raise BundleBuildError(f"{label}_not_verified")
    return {"path": str(path), "sha256": sha, "schema": schema}


def _validate_host_tool_pin(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleBuildError(f"toolchain_{label}_object_required")
    require_exact_keys(
        value, {"path", "sha256", "bytes", "version", "signature"}, f"toolchain_{label}"
    )
    path = _normal_path(value["path"], f"toolchain_{label}")
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise BundleBuildError(f"toolchain_{label}_absolute_regular_file_required")
    sha = str(value["sha256"]).lower()
    size = value["bytes"]
    if (
        not HEX64.fullmatch(sha)
        or sha256_file(path) != sha
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size != path.stat().st_size
        or size < 1
    ):
        raise BundleBuildError(f"toolchain_{label}_measured_identity_mismatch")
    if not isinstance(value["version"], str) or not value["version"].strip():
        raise BundleBuildError(f"toolchain_{label}_version_required")
    signature = value["signature"]
    if not isinstance(signature, dict):
        raise BundleBuildError(f"toolchain_{label}_signature_object_required")
    require_exact_keys(
        signature, {"status", "subject", "thumbprint"}, f"toolchain_{label}_signature"
    )
    if (
        signature["status"] != "valid"
        or not isinstance(signature["subject"], str)
        or not signature["subject"].strip()
        or not isinstance(signature["thumbprint"], str)
        or not re.fullmatch(r"[0-9A-Fa-f]{40,128}", signature["thumbprint"])
    ):
        raise BundleBuildError(f"toolchain_{label}_signature_invalid")
    return {
        "path": str(path),
        "sha256": sha,
        "bytes": size,
        "version": value["version"],
        "signature": {
            "status": "valid",
            "subject": signature["subject"],
            "thumbprint": signature["thumbprint"],
        },
    }


def validate_toolchain_pins(path: Path) -> dict[str, Any]:
    global _VERIFIED_GIT_REPOSITORY_ATTRIBUTES, _VERIFIED_GIT_REPOSITORY_CONFIG
    _VERIFIED_GIT_REPOSITORY_CONFIG = None
    _VERIFIED_GIT_REPOSITORY_ATTRIBUTES = None
    document = read_json_object(path, label="toolchain_pins")
    require_exact_keys(document, {"schema_version", "toolchain"}, "toolchain_pins")
    if document["schema_version"] != TOOLCHAIN_PINS_SCHEMA:
        raise BundleBuildError("toolchain_pins_schema_mismatch")
    raw = document["toolchain"]
    if not isinstance(raw, dict):
        raise BundleBuildError("toolchain_object_required")
    required = {
        *HOST_TOOLCHAIN_ROLES,
        "git_repository_config",
        "git_repository_attributes",
        "docker_client_config",
        "kubernetes_client_config",
        "python_distribution",
        "git_distribution",
        "windows_tcb",
        "wsl_runtime",
        "container_psql",
    }
    require_exact_keys(raw, required, "toolchain")
    normalized: dict[str, Any] = {
        role: _validate_host_tool_pin(raw[role], label=role) for role in HOST_TOOLCHAIN_ROLES
    }
    if Path(normalized["git"]["path"]) != EXPECTED_GIT_PATH.resolve():
        raise BundleBuildError("toolchain_git_mingw64_binary_required")
    if Path(normalized["docker_compose"]["path"]) != EXPECTED_DOCKER_COMPOSE_PATH.resolve():
        raise BundleBuildError("toolchain_docker_compose_standalone_binary_required")
    compose_version = _run_contained(
        [str(EXPECTED_DOCKER_COMPOSE_PATH), "version", "--short"],
        name="r7s1-builder-docker-compose-version-readback",
        cwd=PROJECT_ROOT,
        env=_scrubbed_client_environment(DOCKER_CLIENT_CONFIG_POLICY),
    )
    if compose_version.return_code != 0:
        raise BundleBuildError("toolchain_docker_compose_version_readback_failed")
    if compose_version.stdout.strip() != str(normalized["docker_compose"]["version"]).strip():
        raise BundleBuildError("toolchain_docker_compose_version_readback_mismatch")
    git_config = raw["git_repository_config"]
    if not isinstance(git_config, dict):
        raise BundleBuildError("toolchain_git_repository_config_object_required")
    git_config_observed = _verify_git_repository_config_pin(git_config)
    git_config_readback = _validate_artifact_pin(
        git_config["readback"],
        label="toolchain_git_repository_config_readback",
        schema=GIT_REPOSITORY_CONFIG_READBACK_SCHEMA,
    )
    git_config_payload = read_json_object(
        Path(git_config_readback["path"]), label="toolchain_git_repository_config_readback"
    )
    require_exact_keys(
        git_config_payload,
        {
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
        },
        "toolchain_git_repository_config_readback",
    )
    _parse_utc_timestamp(
        git_config_payload["captured_at"],
        "toolchain_git_repository_config_readback_captured_at",
    )
    if any(
        git_config_payload[key] != git_config_observed[key]
        for key in (
            "path",
            "sha256",
            "bytes",
            "key_names",
            "origin_identity",
            "config_worktree_absent",
            "policy_sha256",
        )
    ):
        raise BundleBuildError("toolchain_git_repository_config_readback_projection_mismatch")
    normalized["git_repository_config"] = {
        "path": git_config_observed["path"],
        "sha256": git_config_observed["sha256"],
        "bytes": git_config_observed["bytes"],
        "policy": dict(GIT_REPOSITORY_CONFIG_POLICY),
        "readback": git_config_readback,
    }
    git_attributes = raw["git_repository_attributes"]
    if not isinstance(git_attributes, dict):
        raise BundleBuildError("toolchain_git_repository_attributes_object_required")
    git_attributes_observed = _verify_git_repository_attributes_pin(git_attributes)
    git_attributes_readback = _validate_artifact_pin(
        git_attributes["readback"],
        label="toolchain_git_repository_attributes_readback",
        schema=GIT_REPOSITORY_ATTRIBUTES_READBACK_SCHEMA,
    )
    git_attributes_payload = read_json_object(
        Path(git_attributes_readback["path"]),
        label="toolchain_git_repository_attributes_readback",
    )
    require_exact_keys(
        git_attributes_payload,
        {
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
        },
        "toolchain_git_repository_attributes_readback",
    )
    _parse_utc_timestamp(
        git_attributes_payload["captured_at"],
        "toolchain_git_repository_attributes_readback_captured_at",
    )
    if any(
        git_attributes_payload[key] != git_attributes_observed[key]
        for key in (
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
        )
    ):
        raise BundleBuildError("toolchain_git_repository_attributes_readback_projection_mismatch")
    normalized["git_repository_attributes"] = {
        "path": git_attributes_observed["path"],
        "sha256": git_attributes_observed["sha256"],
        "bytes": git_attributes_observed["bytes"],
        "policy": copy.deepcopy(GIT_REPOSITORY_ATTRIBUTES_POLICY),
        "readback": git_attributes_readback,
    }
    docker_client_config = raw["docker_client_config"]
    if not isinstance(docker_client_config, dict):
        raise BundleBuildError("toolchain_docker_client_config_object_required")
    require_exact_keys(
        docker_client_config,
        {"path", "sha256", "bytes", "context_metadata", "policy", "readback"},
        "toolchain_docker_client_config",
    )
    docker_config_path = _normal_path(
        docker_client_config["path"], "toolchain_docker_client_config"
    )
    context_metadata = docker_client_config["context_metadata"]
    if not isinstance(context_metadata, dict):
        raise BundleBuildError("toolchain_docker_context_metadata_object_required")
    require_exact_keys(
        context_metadata, {"path", "sha256", "bytes"}, "toolchain_docker_context_metadata"
    )
    context_metadata_path = _normal_path(
        context_metadata["path"], "toolchain_docker_context_metadata"
    )
    if (
        docker_config_path != EXPECTED_DOCKER_CLIENT_CONFIG_PATH.resolve()
        or docker_client_config["sha256"] != EXPECTED_DOCKER_CLIENT_CONFIG_SHA256
        or docker_client_config["bytes"] != EXPECTED_DOCKER_CLIENT_CONFIG_BYTES
        or context_metadata_path != EXPECTED_DOCKER_CONTEXT_METADATA_PATH.resolve()
        or context_metadata["sha256"] != EXPECTED_DOCKER_CONTEXT_METADATA_SHA256
        or context_metadata["bytes"] != EXPECTED_DOCKER_CONTEXT_METADATA_BYTES
        or docker_client_config["policy"] != DOCKER_CLIENT_CONFIG_POLICY
    ):
        raise BundleBuildError("toolchain_docker_client_config_pin_mismatch")
    for label, candidate, expected_sha, expected_bytes in (
        (
            "docker_client_config",
            docker_config_path,
            EXPECTED_DOCKER_CLIENT_CONFIG_SHA256,
            EXPECTED_DOCKER_CLIENT_CONFIG_BYTES,
        ),
        (
            "docker_context_metadata",
            context_metadata_path,
            EXPECTED_DOCKER_CONTEXT_METADATA_SHA256,
            EXPECTED_DOCKER_CONTEXT_METADATA_BYTES,
        ),
    ):
        _assert_no_reparse_ancestors(candidate, label=f"toolchain_{label}")
        if (
            not candidate.is_file()
            or candidate.stat().st_size != expected_bytes
            or sha256_file(candidate) != expected_sha
        ):
            raise BundleBuildError(f"toolchain_{label}_measured_identity_mismatch")
    _assert_no_reparse_ancestors(
        EXPECTED_DOCKER_CONTEXT_TLS_PATH, label="toolchain_docker_context_tls"
    )
    if EXPECTED_DOCKER_CONTEXT_TLS_PATH.exists():
        raise BundleBuildError("toolchain_docker_context_tls_material_directory_must_be_absent")
    docker_readback = _validate_artifact_pin(
        docker_client_config["readback"],
        label="toolchain_docker_client_config_readback",
        schema=DOCKER_CLIENT_CONFIG_READBACK_SCHEMA,
    )
    docker_payload = read_json_object(
        Path(docker_readback["path"]), label="toolchain_docker_client_config_readback"
    )
    require_exact_keys(
        docker_payload,
        {
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
        },
        "toolchain_docker_client_config_readback",
    )
    _parse_utc_timestamp(
        docker_payload["captured_at"], "toolchain_docker_client_config_readback_captured_at"
    )
    expected_docker_projection = {
        "path": str(docker_config_path),
        "sha256": EXPECTED_DOCKER_CLIENT_CONFIG_SHA256,
        "bytes": EXPECTED_DOCKER_CLIENT_CONFIG_BYTES,
        "top_level_keys": DOCKER_CLIENT_CONFIG_POLICY["top_level_keys"],
        "auth_entries": 0,
        "credential_store_present": True,
        "credential_store_value_exposed": False,
        "current_context": "desktop-linux",
        "context_metadata": {
            "path": str(context_metadata_path),
            "sha256": EXPECTED_DOCKER_CONTEXT_METADATA_SHA256,
            "bytes": EXPECTED_DOCKER_CONTEXT_METADATA_BYTES,
        },
        "endpoint_identity": dict(DOCKER_CONTEXT_ENDPOINT_IDENTITY),
        "tls_material_directory_absent": True,
        "policy_sha256": hashlib.sha256(
            canonical_json_bytes(DOCKER_CLIENT_CONFIG_POLICY)
        ).hexdigest(),
    }
    if any(docker_payload[key] != value for key, value in expected_docker_projection.items()):
        raise BundleBuildError("toolchain_docker_client_config_readback_projection_mismatch")
    normalized["docker_client_config"] = {
        "path": str(docker_config_path),
        "sha256": EXPECTED_DOCKER_CLIENT_CONFIG_SHA256,
        "bytes": EXPECTED_DOCKER_CLIENT_CONFIG_BYTES,
        "context_metadata": expected_docker_projection["context_metadata"],
        "policy": copy.deepcopy(DOCKER_CLIENT_CONFIG_POLICY),
        "readback": docker_readback,
    }

    kubernetes_client_config = raw["kubernetes_client_config"]
    if not isinstance(kubernetes_client_config, dict):
        raise BundleBuildError("toolchain_kubernetes_client_config_object_required")
    require_exact_keys(
        kubernetes_client_config,
        {"path", "sha256", "bytes", "policy", "readback"},
        "toolchain_kubernetes_client_config",
    )
    kubernetes_config_path = _normal_path(
        kubernetes_client_config["path"], "toolchain_kubernetes_client_config"
    )
    if (
        kubernetes_config_path != EXPECTED_KUBERNETES_CLIENT_CONFIG_PATH.resolve()
        or kubernetes_client_config["sha256"] != EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256
        or kubernetes_client_config["bytes"] != EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES
        or kubernetes_client_config["policy"] != KUBERNETES_CLIENT_CONFIG_POLICY
    ):
        raise BundleBuildError("toolchain_kubernetes_client_config_pin_mismatch")
    _assert_no_reparse_ancestors(kubernetes_config_path, label="toolchain_kubernetes_client_config")
    if (
        not kubernetes_config_path.is_file()
        or kubernetes_config_path.stat().st_size != EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES
        or sha256_file(kubernetes_config_path) != EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256
    ):
        raise BundleBuildError("toolchain_kubernetes_client_config_measured_identity_mismatch")
    kubernetes_readback = _validate_artifact_pin(
        kubernetes_client_config["readback"],
        label="toolchain_kubernetes_client_config_readback",
        schema=KUBERNETES_CLIENT_CONFIG_READBACK_SCHEMA,
    )
    kubernetes_payload = read_json_object(
        Path(kubernetes_readback["path"]), label="toolchain_kubernetes_client_config_readback"
    )
    require_exact_keys(
        kubernetes_payload,
        {
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
        },
        "toolchain_kubernetes_client_config_readback",
    )
    _parse_utc_timestamp(
        kubernetes_payload["captured_at"],
        "toolchain_kubernetes_client_config_readback_captured_at",
    )
    expected_kubernetes_projection = {
        "path": str(kubernetes_config_path),
        "sha256": EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256,
        "bytes": EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES,
        "current_context": "docker-desktop",
        "object_counts": KUBERNETES_CLIENT_CONFIG_POLICY["object_counts"],
        "context_identity": KUBERNETES_CLIENT_CONFIG_POLICY["context_identity"],
        "cluster_identity": KUBERNETES_CLIENT_CONFIG_POLICY["cluster_identity"],
        "user_identity": KUBERNETES_CLIENT_CONFIG_POLICY["user_identity"],
        "forbidden_fields_absent": KUBERNETES_CLIENT_CONFIG_POLICY["forbidden_fields_absent"],
        "multiple_config_merge_forbidden": True,
        "embedded_material_presence": KUBERNETES_CLIENT_CONFIG_POLICY["embedded_material_presence"],
        "policy_sha256": hashlib.sha256(
            canonical_json_bytes(KUBERNETES_CLIENT_CONFIG_POLICY)
        ).hexdigest(),
    }
    if any(
        kubernetes_payload[key] != value for key, value in expected_kubernetes_projection.items()
    ):
        raise BundleBuildError("toolchain_kubernetes_client_config_readback_projection_mismatch")
    normalized["kubernetes_client_config"] = {
        "path": str(kubernetes_config_path),
        "sha256": EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256,
        "bytes": EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES,
        "policy": copy.deepcopy(KUBERNETES_CLIENT_CONFIG_POLICY),
        "readback": kubernetes_readback,
    }
    distribution = raw["python_distribution"]
    if not isinstance(distribution, dict):
        raise BundleBuildError("toolchain_python_distribution_object_required")
    require_exact_keys(
        distribution,
        {
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
        },
        "toolchain_python_distribution",
    )
    base_prefix = _normal_path(distribution["base_prefix"], "toolchain_python_distribution")
    tree_sha, file_count = python_distribution_tree_identity(base_prefix)
    if (
        distribution["tree_encoding"] != PYTHON_TREE_ENCODING
        or distribution["included_roots"] != PYTHON_INCLUDED_ROOTS
        or distribution["excluded_roots"] != PYTHON_EXCLUDED_ROOTS
        or distribution["distribution_tree_sha256"] != tree_sha
        or distribution["file_count"] != file_count
        or any(
            not isinstance(distribution[key], str) or not distribution[key].strip()
            for key in ("implementation", "name", "version")
        )
    ):
        raise BundleBuildError("toolchain_python_distribution_identity_mismatch")
    normalized["python_distribution"] = {
        **{key: distribution[key] for key in ("implementation", "name", "version")},
        "base_prefix": str(base_prefix),
        "distribution_tree_sha256": tree_sha,
        "file_count": file_count,
        "tree_encoding": PYTHON_TREE_ENCODING,
        "included_roots": list(PYTHON_INCLUDED_ROOTS),
        "excluded_roots": list(PYTHON_EXCLUDED_ROOTS),
        "evidence": _validate_artifact_pin(
            distribution["evidence"],
            label="toolchain_python_distribution_evidence",
            schema=PYTHON_DISTRIBUTION_READBACK_SCHEMA,
        ),
    }
    git_distribution = raw["git_distribution"]
    if not isinstance(git_distribution, dict):
        raise BundleBuildError("toolchain_git_distribution_object_required")
    require_exact_keys(
        git_distribution,
        {"root", "distribution_tree_sha256", "file_count", "tree_encoding", "evidence"},
        "toolchain_git_distribution",
    )
    git_root = _normal_path(git_distribution["root"], "toolchain_git_distribution")
    if git_root != EXPECTED_GIT_ROOT.resolve():
        raise BundleBuildError("toolchain_git_distribution_root_mismatch")
    git_tree_sha, git_file_count = git_distribution_tree_identity(git_root)
    if (
        git_distribution["distribution_tree_sha256"] != git_tree_sha
        or git_distribution["file_count"] != git_file_count
        or git_distribution["tree_encoding"] != GIT_TREE_ENCODING
    ):
        raise BundleBuildError("toolchain_git_distribution_identity_mismatch")
    git_evidence = _validate_artifact_pin(
        git_distribution["evidence"],
        label="toolchain_git_distribution_evidence",
        schema=GIT_DISTRIBUTION_READBACK_SCHEMA,
    )
    git_payload = read_json_object(Path(git_evidence["path"]), label="git_distribution_evidence")
    require_exact_keys(
        git_payload,
        {
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
        },
        "git_distribution_evidence",
    )
    _parse_utc_timestamp(git_payload["captured_at"], "git_distribution_evidence_captured_at")
    if (
        git_payload["root"] != str(git_root)
        or git_payload["distribution_tree_sha256"] != git_tree_sha
        or git_payload["file_count"] != git_file_count
        or git_payload["tree_encoding"] != GIT_TREE_ENCODING
        or git_payload["reparse_entries"] != 0
        or any(
            not isinstance(git_payload[key], str) or not git_payload[key].strip()
            for key in ("volume_identity", "filesystem_identity")
        )
    ):
        raise BundleBuildError("toolchain_git_distribution_evidence_mismatch")
    normalized["git_distribution"] = {
        "root": str(git_root),
        "distribution_tree_sha256": git_tree_sha,
        "file_count": git_file_count,
        "tree_encoding": GIT_TREE_ENCODING,
        "evidence": git_evidence,
    }
    windows = raw["windows_tcb"]
    if not isinstance(windows, dict):
        raise BundleBuildError("toolchain_windows_tcb_object_required")
    require_exact_keys(
        windows, {"build", "system32_path", "kernel", "evidence"}, "toolchain_windows_tcb"
    )
    system32 = _normal_path(windows["system32_path"], "toolchain_windows_system32")
    kernel = _validate_host_tool_pin(windows["kernel"], label="windows_kernel")
    if not isinstance(windows["build"], str) or not windows["build"].strip():
        raise BundleBuildError("toolchain_windows_build_required")
    normalized["windows_tcb"] = {
        "build": windows["build"],
        "system32_path": str(system32),
        "kernel": kernel,
        "evidence": _validate_artifact_pin(
            windows["evidence"],
            label="toolchain_windows_tcb_evidence",
            schema=WINDOWS_TCB_READBACK_SCHEMA,
        ),
    }
    wsl = raw["wsl_runtime"]
    if not isinstance(wsl, dict):
        raise BundleBuildError("toolchain_wsl_runtime_object_required")
    require_exact_keys(
        wsl,
        {"distro", "kernel_release", "rootfs_identity", "python3", "readback"},
        "toolchain_wsl_runtime",
    )
    python3 = wsl["python3"]
    if not isinstance(python3, dict):
        raise BundleBuildError("toolchain_wsl_python3_object_required")
    require_exact_keys(python3, {"realpath", "sha256", "bytes", "version"}, "toolchain_wsl_python3")
    if (
        not str(python3["realpath"]).startswith("/")
        or not HEX64.fullmatch(str(python3["sha256"]))
        or isinstance(python3["bytes"], bool)
        or not isinstance(python3["bytes"], int)
        or python3["bytes"] < 1
        or any(
            not isinstance(wsl[key], str) or not wsl[key].strip()
            for key in ("distro", "kernel_release", "rootfs_identity")
        )
        or not isinstance(python3["version"], str)
        or not python3["version"].strip()
    ):
        raise BundleBuildError("toolchain_wsl_runtime_identity_invalid")
    normalized["wsl_runtime"] = {
        **{key: wsl[key] for key in ("distro", "kernel_release", "rootfs_identity")},
        "python3": dict(python3),
        "readback": _validate_artifact_pin(
            wsl["readback"], label="toolchain_wsl_readback", schema=WSL_RUNTIME_READBACK_SCHEMA
        ),
    }
    psql = raw["container_psql"]
    if not isinstance(psql, dict):
        raise BundleBuildError("toolchain_container_psql_object_required")
    require_exact_keys(
        psql,
        {
            "container_name",
            "image_digest",
            "realpath",
            "sha256",
            "bytes",
            "version",
            "execution_scope",
            "readback",
        },
        "toolchain_container_psql",
    )
    if (
        not isinstance(psql["container_name"], str)
        or not psql["container_name"].strip()
        or not IMAGE_ID.fullmatch(str(psql["image_digest"]))
        or not str(psql["realpath"]).startswith("/")
        or not HEX64.fullmatch(str(psql["sha256"]))
        or isinstance(psql["bytes"], bool)
        or not isinstance(psql["bytes"], int)
        or psql["bytes"] < 1
        or not isinstance(psql["version"], str)
        or not psql["version"].strip()
    ):
        raise BundleBuildError("toolchain_container_psql_identity_invalid")
    if psql["execution_scope"] != DOCKER_CONTAINER_EXECUTION_SCOPE:
        raise BundleBuildError("toolchain_container_psql_execution_scope_mismatch")
    psql_readback = _validate_artifact_pin(
        psql["readback"],
        label="toolchain_container_psql_readback",
        schema=CONTAINER_PSQL_READBACK_SCHEMA,
    )
    psql_payload = read_json_object(
        Path(psql_readback["path"]), label="toolchain_container_psql_readback"
    )
    require_exact_keys(
        psql_payload,
        {
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
        },
        "toolchain_container_psql_readback",
    )
    _parse_utc_timestamp(
        psql_payload["captured_at"], "toolchain_container_psql_readback_captured_at"
    )
    psql_projection_keys = (
        "container_name",
        "image_digest",
        "realpath",
        "sha256",
        "bytes",
        "version",
        "execution_scope",
    )
    if {key: psql_payload[key] for key in psql_projection_keys} != {
        key: psql[key] for key in psql_projection_keys
    }:
        raise BundleBuildError("toolchain_container_psql_readback_projection_mismatch")
    normalized["container_psql"] = {
        **{
            key: psql[key]
            for key in ("container_name", "image_digest", "realpath", "sha256", "bytes", "version")
        },
        "execution_scope": dict(DOCKER_CONTAINER_EXECUTION_SCOPE),
        "readback": psql_readback,
    }
    _VERIFIED_GIT_REPOSITORY_CONFIG = dict(normalized["git_repository_config"])
    _VERIFIED_GIT_REPOSITORY_ATTRIBUTES = dict(normalized["git_repository_attributes"])
    return normalized


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise BundleBuildError(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleBuildError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise BundleBuildError(f"{label}_object_required:{path}")
    return value


def require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise BundleBuildError(
            f"{label}_keys_mismatch:missing={sorted(expected - actual)}:extra={sorted(actual - expected)}"
        )


def contains_scalar(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, Mapping):
        return any(contains_scalar(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains_scalar(item, expected) for item in value)
    return False


def parse_parent_specs(specs: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for spec in specs:
        role, separator, raw_path = spec.partition("=")
        if not separator or role not in REQUIRED_PARENT_ROLES or not raw_path:
            raise BundleBuildError(f"parent_spec_invalid:{spec}")
        if role in parsed:
            raise BundleBuildError(f"parent_role_duplicate:{role}")
        parsed[role] = Path(raw_path).resolve()
    missing = set(REQUIRED_PARENT_ROLES) - set(parsed)
    extra = set(parsed) - set(REQUIRED_PARENT_ROLES)
    if missing or extra or len(specs) != len(REQUIRED_PARENT_ROLES):
        raise BundleBuildError(
            f"parent_role_set_mismatch:missing={sorted(missing)}:extra={sorted(extra)}"
        )
    if len({os.path.normcase(str(path)) for path in parsed.values()}) != len(parsed):
        raise BundleBuildError("parent_paths_must_be_distinct")
    return parsed


def build_parent_checkpoints(
    parent_paths: Mapping[str, Path],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    payloads = {
        role: read_json_object(parent_paths[role], label=f"parent_{role}")
        for role in REQUIRED_PARENT_ROLES
    }

    def named_values(value: Any, names: set[str]) -> set[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in names and isinstance(item, str) and item.strip():
                    found.add(item)
                found.update(named_values(item, names))
        elif isinstance(value, list):
            for item in value:
                found.update(named_values(item, names))
        return found

    anchor_roles = {
        "r5": "r5_failure_seal",
        "r6": "r6_compose_rca",
        "r7": "r7_failure_seal",
    }
    anchors: dict[str, str] = {}
    for generation, role in anchor_roles.items():
        candidates = named_values(payloads[role], {"run_id", "run_identity"})
        if len(candidates) != 1:
            raise BundleBuildError(f"parent_chain_run_identity_ambiguous:{role}")
        anchors[generation] = candidates.pop()
    generation_by_role = {
        **{role: "r5" for role in REQUIRED_PARENT_ROLES[:2]},
        **{role: "r6" for role in REQUIRED_PARENT_ROLES[2:7]},
        **{role: "r7" for role in REQUIRED_PARENT_ROLES[7:]},
    }
    measured_sha = {role: sha256_file(parent_paths[role]) for role in REQUIRED_PARENT_ROLES}
    required_links = {
        "r5_failure_index": ("r5_failure_seal",),
        "r6_failure_seal_amendment": ("r6_compose_rca",),
        "r6_final_index": ("r6_failure_seal_amendment",),
        "post_manual_on_index": ("r6_final_index", "post_manual_on_readback"),
        "r7_failure_seal": ("r7_failure_index",),
        "r7_post_seal_residual_amendment": ("r7_failure_seal",),
    }
    for role, linked_roles in required_links.items():
        for linked_role in linked_roles:
            if not contains_scalar(payloads[role], measured_sha[linked_role]):
                raise BundleBuildError(f"parent_cross_link_missing:{role}:{linked_role}")

    entries: list[dict[str, Any]] = []
    for role in REQUIRED_PARENT_ROLES:
        path = parent_paths[role]
        payload = payloads[role]
        schema = payload.get("schema", payload.get("schema_version"))
        if schema != PARENT_SCHEMAS[role]:
            raise BundleBuildError(f"parent_schema_mismatch:{role}")
        run_id = anchors[generation_by_role[role]]
        if (
            any(
                str(payload.get(key, "")).lower() in {"pass", "passed", "success", "completed"}
                for key in ("decision", "outcome", "result", "classification", "phase_b2_status")
            )
            or payload.get("completion_marker_created") is True
        ):
            raise BundleBuildError(f"parent_no_credit_contract_mismatch:{role}")
        entries.append(
            {
                "role": role,
                "path": str(path),
                "sha256": measured_sha[role],
                "kind": PARENT_KINDS[role],
                "schema": schema,
                "run_id": run_id,
                "immutable": True,
                "must_not_execute": True,
            }
        )
    return entries, payloads


def parent_map_sha256(parent_checkpoints: list[Mapping[str, Any]]) -> str:
    entries = {str(item["role"]): item for item in parent_checkpoints}
    if tuple(entries) != REQUIRED_PARENT_ROLES:
        raise BundleBuildError("parent_map_role_order_mismatch")
    projected = {
        role: {
            "path": str(Path(str(entries[role]["path"])).resolve()),
            "sha256": str(entries[role]["sha256"]),
            "schema": str(entries[role]["schema"]),
            "run_id": str(entries[role]["run_id"]),
        }
        for role in REQUIRED_PARENT_ROLES
    }
    return _canonical_object_sha256(projected)


def source_schema_versions(project_root: Path) -> list[str]:
    path = project_root / "src" / "evm" / "control_panel" / "transactional_store.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise BundleBuildError("schema_versions_source_parse_failed") from exc
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            isinstance(getattr(node, "target", None), ast.Name)
            and getattr(node, "target").id == "SCHEMA_VERSIONS"
            or isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SCHEMA_VERSIONS"
                for target in node.targets
            )
        )
    ]
    if len(matches) != 1:
        raise BundleBuildError(f"schema_versions_assignment_count:{len(matches)}")
    node = matches[0]
    literal = ast.literal_eval(node.value)  # type: ignore[arg-type]
    if (
        not isinstance(literal, tuple)
        or not literal
        or not all(isinstance(item, str) for item in literal)
    ):
        raise BundleBuildError("schema_versions_literal_tuple_required")
    versions = list(literal)
    if len(versions) != len(set(versions)) or versions != sorted(versions):
        raise BundleBuildError("schema_versions_must_be_unique_sorted")
    return versions


def verify_source_identity(
    project_root: Path,
    branch: str,
    expected_untracked: int,
    expected_untracked_digest: str,
) -> dict[str, Any]:
    repository = project_root.parent
    revision = git(repository, "rev-parse", "HEAD").lower()
    tree = git(repository, "rev-parse", "HEAD^{tree}").lower()
    actual_branch = git(repository, "branch", "--show-current")
    origin = git(repository, "rev-parse", f"origin/{branch}").lower()
    remote = git_remote_revision(branch)
    tracked = git(repository, "status", "--porcelain=v1", "--untracked-files=no")
    untracked, untracked_digest = untracked_identity(repository)
    if actual_branch != branch:
        raise BundleBuildError(f"branch_mismatch:{actual_branch}")
    if not revision or revision != origin or revision != remote:
        raise BundleBuildError(f"local_origin_remote_mismatch:{revision}:{origin}:{remote}")
    if tracked:
        raise BundleBuildError("tracked_changes_present")
    if untracked != expected_untracked:
        raise BundleBuildError(f"untracked_count_mismatch:{untracked}")
    expected_digest = expected_untracked_digest.lower()
    if not HEX64.fullmatch(expected_digest):
        raise BundleBuildError("expected_untracked_digest_invalid")
    if untracked_digest != expected_digest:
        raise BundleBuildError(f"untracked_digest_mismatch:{untracked_digest}")
    return {
        "revision": revision,
        "tree": tree,
        "branch": actual_branch,
        "origin_revision": origin,
        "remote_revision": remote,
        "tracked": 0,
        "untracked": untracked,
        "untracked_path_encoding": "utf-8",
        "untracked_sort": "ordinal",
        "untracked_separator": "nul",
        "untracked_path_digest_sha256": untracked_digest,
    }


def _normal_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BundleBuildError(f"{label}_path_required")
    return Path(value).resolve()


HISTORICAL_IDENTITY_KEYS = {
    "control_plane_task_entity_statuses": {"entity_id", "created_at", "updated_at"},
    "mlflow_running_rows": {
        "run_id",
        "status",
        "lifecycle_stage",
        "start_time",
        "end_time",
    },
    "kubernetes_terminal_failed_objects": {
        "uid",
        "namespace",
        "name",
        "owner_uid",
        "owner_kind",
        "owner_name",
        "owner_controller",
        "reason",
        "reason_source",
    },
}


def _validate_historical_attestation(
    *,
    path: Path,
    sha256: str,
    source: str,
    expected_counts: Mapping[str, int],
    expected_classification: str,
    expected_kubernetes_uids: set[str],
    proof_paths: set[Path],
) -> None:
    if sha256_file(path) != sha256:
        raise BundleBuildError(f"historical_attestation_sha_mismatch:{source}")
    payload = read_json_object(path, label=f"historical_attestation_{source}")
    require_exact_keys(
        payload,
        {"source", "captured_at", "query_sha256", "counts", "classification", "records"},
        f"historical_attestation_{source}",
    )
    if payload["source"] != source:
        raise BundleBuildError(f"historical_attestation_source_mismatch:{source}")
    captured_at = payload["captured_at"]
    if not isinstance(captured_at, str) or not captured_at.strip() or not captured_at.endswith("Z"):
        raise BundleBuildError(f"historical_attestation_captured_at_required:{source}")
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleBuildError(f"historical_attestation_captured_at_invalid:{source}") from exc
    if payload["query_sha256"] != HISTORICAL_QUERY_SHA256[source]:
        raise BundleBuildError(f"historical_attestation_query_sha_invalid:{source}")
    counts = payload["counts"]
    if not isinstance(counts, dict):
        raise BundleBuildError(f"historical_attestation_counts_object_required:{source}")
    required_counts = {
        "observed_count",
        "executing_count",
        "historical_count",
        "unproven_count",
    }
    require_exact_keys(counts, required_counts, f"historical_attestation_counts_{source}")
    if counts != dict(expected_counts):
        raise BundleBuildError(f"historical_attestation_counts_mismatch:{source}")
    if payload["classification"] != expected_classification:
        raise BundleBuildError(f"historical_attestation_classification_mismatch:{source}")
    records = payload["records"]
    if not isinstance(records, list) or len(records) != counts["observed_count"]:
        raise BundleBuildError(f"historical_attestation_record_count_mismatch:{source}")
    classified_counts = {"executing": 0, "historical_nonexecuting": 0, "unproven": 0}
    identities: set[str] = set()
    kubernetes_uids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise BundleBuildError(
                f"historical_attestation_record_object_required:{source}:{index}"
            )
        canonical_record_keys = {"identity", "observed_state", "classification"}
        legacy_record_keys = canonical_record_keys | {"execution_proof"}
        if source == "mlflow_running_rows":
            if set(record) not in (canonical_record_keys, legacy_record_keys):
                raise BundleBuildError(
                    f"historical_attestation_record_mlflow_keys_mismatch:{index}"
                )
        else:
            require_exact_keys(
                record,
                legacy_record_keys,
                f"historical_attestation_record_{source}_{index}",
            )
        identity = record["identity"]
        if not isinstance(identity, dict):
            raise BundleBuildError(
                f"historical_attestation_identity_object_required:{source}:{index}"
            )
        require_exact_keys(
            identity, HISTORICAL_IDENTITY_KEYS[source], f"historical_identity_{source}_{index}"
        )
        if source == "mlflow_running_rows":
            if (
                identity["status"] != "RUNNING"
                or identity["end_time"] != ""
                or not all(
                    isinstance(identity[key], str) and identity[key].strip()
                    for key in ("run_id", "status", "lifecycle_stage", "start_time")
                )
            ):
                raise BundleBuildError(f"historical_attestation_mlflow_identity_invalid:{index}")
        elif not all(
            isinstance(identity[key], str) and identity[key].strip()
            for key in set(identity) - {"owner_controller"}
        ) or (
            source == "kubernetes_terminal_failed_objects"
            and identity["owner_controller"] is not True
        ):
            raise BundleBuildError(
                f"historical_attestation_identity_value_required:{source}:{index}"
            )
        if source == "kubernetes_terminal_failed_objects":
            if not UUID.fullmatch(identity["uid"]) or not UUID.fullmatch(identity["owner_uid"]):
                raise BundleBuildError(f"historical_attestation_identity_uid_invalid:{index}")
            kubernetes_uids.add(identity["uid"])
        identity_key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        if identity_key in identities:
            raise BundleBuildError(f"historical_attestation_identity_duplicate:{source}:{index}")
        identities.add(identity_key)
        if not isinstance(record["observed_state"], str) or not record["observed_state"].strip():
            raise BundleBuildError(
                f"historical_attestation_observed_state_required:{source}:{index}"
            )
        classification = record["classification"]
        if classification not in classified_counts:
            raise BundleBuildError(
                f"historical_attestation_record_classification_invalid:{source}:{index}"
            )
        classified_counts[classification] += 1
        if source == "mlflow_running_rows" and "execution_proof" not in record:
            continue
        proof = record["execution_proof"]
        if not isinstance(proof, dict):
            raise BundleBuildError(
                f"historical_attestation_execution_proof_object_required:{source}:{index}"
            )
        require_exact_keys(
            proof,
            {
                "inactivity_proven",
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
                "evidence",
            },
            f"historical_execution_proof_{source}_{index}",
        )
        for count_name in (
            "active_job_count",
            "active_claim_count",
            "active_lease_count",
            "outcome_unknown_count",
        ):
            count = proof[count_name]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise BundleBuildError(f"historical_execution_proof_count_invalid:{source}:{index}")
        if not isinstance(proof["inactivity_proven"], bool):
            raise BundleBuildError(f"historical_execution_proof_boolean_required:{source}:{index}")
        evidence = proof["evidence"]
        if not isinstance(evidence, dict):
            raise BundleBuildError(
                f"historical_execution_proof_evidence_object_required:{source}:{index}"
            )
        require_exact_keys(evidence, {"path", "sha256"}, "historical_execution_proof_evidence")
        evidence_path = _normal_path(evidence["path"], "historical_execution_proof_evidence")
        evidence_sha = str(evidence["sha256"]).lower()
        if evidence_path == path or evidence_path in proof_paths:
            raise BundleBuildError("historical_attestation_proof_paths_must_be_distinct")
        proof_paths.add(evidence_path)
        if not HEX64.fullmatch(evidence_sha) or sha256_file(evidence_path) != evidence_sha:
            raise BundleBuildError(
                f"historical_execution_proof_evidence_sha_mismatch:{source}:{index}"
            )
        proof_payload = read_json_object(
            evidence_path, label=f"historical_execution_proof_payload_{source}_{index}"
        )
        require_exact_keys(
            proof_payload,
            {
                "source",
                "identity",
                "observed_state",
                "captured_at",
                "query_sha256",
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
                "inactivity_decision",
                "decision_authority",
            },
            f"historical_execution_proof_payload_{source}_{index}",
        )
        if proof_payload["source"] != source or proof_payload["identity"] != identity:
            raise BundleBuildError(f"historical_execution_proof_identity_mismatch:{source}:{index}")
        if proof_payload["observed_state"] != record["observed_state"]:
            raise BundleBuildError(f"historical_execution_proof_state_mismatch:{source}:{index}")
        proof_captured_at = proof_payload["captured_at"]
        if not isinstance(proof_captured_at, str) or not proof_captured_at.endswith("Z"):
            raise BundleBuildError(f"historical_execution_proof_timestamp_invalid:{source}:{index}")
        try:
            datetime.fromisoformat(proof_captured_at[:-1] + "+00:00")
        except ValueError as exc:
            raise BundleBuildError(
                f"historical_execution_proof_timestamp_invalid:{source}:{index}"
            ) from exc
        if proof_payload["query_sha256"] != payload["query_sha256"]:
            raise BundleBuildError(f"historical_execution_proof_query_mismatch:{source}:{index}")
        for count_name in (
            "active_job_count",
            "active_claim_count",
            "active_lease_count",
            "outcome_unknown_count",
        ):
            if proof_payload[count_name] != proof[count_name]:
                raise BundleBuildError(
                    f"historical_execution_proof_count_mismatch:{source}:{index}"
                )
        expected_decision = (
            "proven_inactive"
            if proof["inactivity_proven"] is True
            else "executing"
            if sum(
                proof[name]
                for name in (
                    "active_job_count",
                    "active_claim_count",
                    "active_lease_count",
                    "outcome_unknown_count",
                )
            )
            else "unproven"
        )
        if proof_payload["inactivity_decision"] != expected_decision:
            raise BundleBuildError(f"historical_execution_proof_decision_mismatch:{source}:{index}")
        if proof_payload["decision_authority"] != HISTORICAL_DECISION_AUTHORITY:
            raise BundleBuildError(
                f"historical_execution_proof_authority_mismatch:{source}:{index}"
            )
        active_total = sum(
            proof[name]
            for name in (
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
            )
        )
        if classification == "historical_nonexecuting" and (
            proof["inactivity_proven"] is not True or active_total != 0
        ):
            raise BundleBuildError(f"historical_execution_proof_insufficient:{source}:{index}")
        if classification == "unproven" and proof["inactivity_proven"] is True:
            raise BundleBuildError(f"historical_unproven_record_claims_proof:{source}:{index}")
    if (
        classified_counts["executing"] != counts["executing_count"]
        or classified_counts["historical_nonexecuting"] != counts["historical_count"]
        or classified_counts["unproven"] != counts["unproven_count"]
    ):
        raise BundleBuildError(
            f"historical_attestation_record_classification_count_mismatch:{source}"
        )
    if (
        source == "kubernetes_terminal_failed_objects"
        and kubernetes_uids != expected_kubernetes_uids
    ):
        raise BundleBuildError("historical_attestation_kubernetes_identity_set_mismatch")


def _validate_job_scope(value: Any, *, expected_kubernetes_uids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleBuildError("job_scope_contract_object_required")
    require_exact_keys(
        value,
        {
            "canonical_active_jobs",
            "historical_observations",
            "historical_classifications",
        },
        "job_scope_contract",
    )
    expected_canonical = {
        "sources": ["kubernetes_job_status_active", "manifest_active_job_file_markers"],
        "required_count": 0,
    }
    expected_historical = {
        "sources": [
            "control_plane_task_entity_statuses",
            "mlflow_running_rows",
            "kubernetes_terminal_failed_objects",
        ],
        "separate_from_canonical_active_jobs": True,
        "unknown_or_unproven_blocks_restore": True,
        "deletion_required": False,
    }
    if value["canonical_active_jobs"] != expected_canonical:
        raise BundleBuildError("job_scope_canonical_active_jobs_mismatch")
    if value["historical_observations"] != expected_historical:
        raise BundleBuildError("job_scope_historical_observations_mismatch")
    classifications = value["historical_classifications"]
    expected_sources = expected_historical["sources"]
    if not isinstance(classifications, list) or len(classifications) != len(expected_sources):
        raise BundleBuildError("historical_classification_count_mismatch")
    attestation_paths: set[Path] = set()
    proof_paths: set[Path] = set()
    for expected_source, item in zip(expected_sources, classifications, strict=True):
        if not isinstance(item, dict):
            raise BundleBuildError("historical_classification_object_required")
        require_exact_keys(
            item,
            {
                "source",
                "observed_count",
                "executing_count",
                "historical_count",
                "unproven_count",
                "classification",
                "attestation",
            },
            f"historical_classification_{expected_source}",
        )
        if item["source"] != expected_source:
            raise BundleBuildError("historical_classification_source_mismatch")
        counts = []
        for key in ("observed_count", "executing_count", "historical_count", "unproven_count"):
            count = item[key]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise BundleBuildError(
                    f"historical_classification_count_invalid:{expected_source}:{key}"
                )
            counts.append(count)
        observed, executing, historical, unproven = counts
        if observed != executing + historical + unproven:
            raise BundleBuildError(
                f"historical_classification_count_sum_mismatch:{expected_source}"
            )
        expected_label = (
            "unproven" if unproven else "executing" if executing else "historical_nonexecuting"
        )
        if item["classification"] != expected_label:
            raise BundleBuildError(f"historical_classification_label_mismatch:{expected_source}")
        if expected_source == "kubernetes_terminal_failed_objects" and observed != 14:
            raise BundleBuildError("historical_failed_pod_classification_count_mismatch")
        attestation = item["attestation"]
        if not isinstance(attestation, dict):
            raise BundleBuildError("historical_classification_attestation_object_required")
        require_exact_keys(attestation, {"path", "sha256"}, "historical_classification_attestation")
        attestation_path = _normal_path(
            attestation["path"], "historical_classification_attestation"
        )
        if attestation_path in attestation_paths:
            raise BundleBuildError("historical_attestation_paths_must_be_distinct")
        attestation_paths.add(attestation_path)
        attestation_sha = str(attestation["sha256"]).lower()
        if not HEX64.fullmatch(attestation_sha) or sha256_file(attestation_path) != attestation_sha:
            raise BundleBuildError(
                f"historical_classification_attestation_sha_mismatch:{expected_source}"
            )
        _validate_historical_attestation(
            path=attestation_path,
            sha256=attestation_sha,
            source=expected_source,
            expected_counts={
                "observed_count": observed,
                "executing_count": executing,
                "historical_count": historical,
                "unproven_count": unproven,
            },
            expected_classification=expected_label,
            expected_kubernetes_uids=expected_kubernetes_uids,
            proof_paths=proof_paths,
        )
    if attestation_paths & proof_paths:
        raise BundleBuildError("historical_attestation_and_proof_paths_must_be_distinct")
    return value


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BundleBuildError(f"{label}_utc_timestamp_required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BundleBuildError(f"{label}_utc_timestamp_invalid") from exc
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise BundleBuildError(f"{label}_utc_timestamp_invalid")
    return parsed


def _canonical_object_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def _validate_target_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleBuildError("external_fencing_target_identity_object_required")
    keys = {"run_id", "status", "lifecycle_stage", "start_time", "end_time"}
    require_exact_keys(value, keys, "external_fencing_target_identity")
    if not MLFLOW_RUN_ID.fullmatch(str(value["run_id"])):
        raise BundleBuildError("external_fencing_target_run_id_invalid")
    if value["status"] != "RUNNING" or value["lifecycle_stage"] != "active":
        raise BundleBuildError("external_fencing_target_state_mismatch")
    if not isinstance(value["start_time"], str) or not value["start_time"].isdigit():
        raise BundleBuildError("external_fencing_target_start_time_invalid")
    if value["end_time"] != "":
        raise BundleBuildError("external_fencing_target_end_time_must_be_empty")
    return dict(value)


def _validate_external_artifact_pin(
    value: Any,
    *,
    expected_kind: str,
    expected_ordinal: int,
    expected_identity_sha256: str,
    expected_schema: str,
) -> tuple[dict[str, Any], Path, dict[str, Any], datetime]:
    if not isinstance(value, dict):
        raise BundleBuildError(f"external_fencing_{expected_kind}_pin_object_required")
    require_exact_keys(
        value,
        {
            "kind",
            "ordinal",
            "path",
            "sha256",
            "captured_at",
            "schema",
            "source_revision",
            "target_identity_sha256",
            "decision_authority",
        },
        f"external_fencing_{expected_kind}_pin",
    )
    if value["kind"] != expected_kind or value["ordinal"] != expected_ordinal:
        raise BundleBuildError(f"external_fencing_{expected_kind}_pin_identity_mismatch")
    if value["schema"] != expected_schema or not HEX40.fullmatch(str(value["source_revision"])):
        raise BundleBuildError(f"external_fencing_{expected_kind}_source_identity_mismatch")
    if value["target_identity_sha256"] != expected_identity_sha256:
        raise BundleBuildError(f"external_fencing_{expected_kind}_pin_target_mismatch")
    if value["decision_authority"] != EXTERNAL_DECISION_AUTHORITY:
        raise BundleBuildError(f"external_fencing_{expected_kind}_pin_authority_mismatch")
    captured_at = _parse_utc_timestamp(
        value["captured_at"], f"external_fencing_{expected_kind}_{expected_ordinal}"
    )
    path = _normal_path(value["path"], f"external_fencing_{expected_kind}")
    sha256 = str(value["sha256"]).lower()
    if not HEX64.fullmatch(sha256) or sha256_file(path) != sha256:
        raise BundleBuildError(f"external_fencing_{expected_kind}_sha_mismatch")
    payload = read_json_object(
        path, label=f"external_fencing_{expected_kind}_{expected_ordinal}_payload"
    )
    if (
        payload.get("schema") != value["schema"]
        or payload.get("captured_at") != value["captured_at"]
        or payload.get("ordinal") != value["ordinal"]
        or payload.get("source_revision") != value["source_revision"]
    ):
        raise BundleBuildError(f"external_fencing_{expected_kind}_pin_payload_mismatch")
    return dict(value), path, payload, captured_at


def _validate_snapshot_payload(
    payload: Mapping[str, Any],
    *,
    ordinal: int,
    captured_at: str,
    target_identity: Mapping[str, Any],
) -> dict[str, Any]:
    require_exact_keys(payload, SNAPSHOT_TOP_LEVEL_KEYS, "external_fencing_snapshot")
    if payload.get("schema") != HISTORICAL_SNAPSHOT_SCHEMA:
        raise BundleBuildError("external_fencing_snapshot_schema_mismatch")
    if payload.get("all_commands_safe") is not True:
        raise BundleBuildError("external_fencing_snapshot_commands_not_safe")
    if payload.get("ordinal") != ordinal or payload.get("captured_at") != captured_at:
        raise BundleBuildError("external_fencing_snapshot_metadata_mismatch")
    observed = payload.get("observed")
    if not isinstance(observed, dict):
        raise BundleBuildError("external_fencing_snapshot_observed_object_required")
    require_exact_keys(observed, SNAPSHOT_OBSERVED_KEYS, "external_fencing_snapshot_observed")
    if (
        payload["read_only"] is not True
        or payload["service_mutation_count"] != 0
        or payload["automatic_retry_count"] != 0
    ):
        raise BundleBuildError("external_fencing_snapshot_safety_contract_mismatch")
    rows = observed["mlflow_activity"]
    if not isinstance(rows, list):
        raise BundleBuildError("external_fencing_snapshot_mlflow_rows_required")
    target_rows = [
        item
        for item in rows
        if isinstance(item, dict) and item.get("run_id") == target_identity["run_id"]
    ]
    if len(target_rows) != 1:
        raise BundleBuildError("external_fencing_snapshot_target_exactly_once_required")
    activity = target_rows[0]
    require_exact_keys(activity, MLFLOW_ACTIVITY_KEYS, "external_fencing_snapshot_activity")
    observed_identity = _validate_target_identity({key: activity[key] for key in target_identity})
    if observed_identity != dict(target_identity):
        raise BundleBuildError("external_fencing_snapshot_target_identity_mismatch")
    if (
        not isinstance(activity["last_metric_timestamp"], str)
        or not activity["last_metric_timestamp"].isdigit()
    ):
        raise BundleBuildError("external_fencing_snapshot_last_metric_timestamp_invalid")
    for key in ("metric_count", "parameter_count", "tag_count"):
        item = activity[key]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise BundleBuildError(f"external_fencing_snapshot_count_invalid:{key}")
    return dict(activity)


def _validate_zero_link_payload(
    payload: Mapping[str, Any],
    *,
    ordinal: int,
    captured_at: str,
    target_run_id: str,
) -> dict[str, int]:
    require_exact_keys(payload, LINK_SCAN_TOP_LEVEL_KEYS, "external_fencing_link_scan")
    if payload.get("schema") != TARGET_LINK_SCAN_SCHEMA:
        raise BundleBuildError("external_fencing_link_scan_schema_mismatch")
    if payload.get("all_commands_safe") is not True:
        raise BundleBuildError("external_fencing_link_scan_commands_not_safe")
    if payload.get("ordinal") != ordinal or payload.get("captured_at") != captured_at:
        raise BundleBuildError("external_fencing_link_scan_metadata_mismatch")
    if payload.get("target_run_id") != target_run_id:
        raise BundleBuildError("external_fencing_link_scan_target_mismatch")
    if payload.get("all_exact_links_zero") is not True:
        raise BundleBuildError("external_fencing_link_scan_zero_not_proven")
    if (
        payload["read_only"] is not True
        or payload["service_mutation_count"] != 0
        or payload["automatic_retry_count"] != 0
        or payload["forced_termination_attempts"] != 0
    ):
        raise BundleBuildError("external_fencing_link_scan_safety_contract_mismatch")
    observed = payload.get("observed")
    if not isinstance(observed, dict):
        raise BundleBuildError("external_fencing_link_scan_observed_object_required")
    require_exact_keys(
        observed,
        {
            "control_plane_run_links",
            "airflow_run_links",
            "docker_run_links",
            "kubernetes_run_links",
            "windows_run_links",
            "wsl_run_links",
        },
        "external_fencing_link_scan_observed",
    )
    for group_name, expected_keys in (
        ("control_plane_run_links", {"table", "identity_matches", "payload_matches"}),
        (
            "airflow_run_links",
            {"table", "identity_matches", "payload_matches", "active_matches"},
        ),
    ):
        group = observed[group_name]
        if not isinstance(group, list):
            raise BundleBuildError(f"external_fencing_link_scan_group_required:{group_name}")
        tables: list[str] = []
        for index, record in enumerate(group):
            if not isinstance(record, dict):
                raise BundleBuildError(
                    f"external_fencing_link_scan_record_required:{group_name}:{index}"
                )
            require_exact_keys(
                record,
                expected_keys,
                f"external_fencing_link_scan_{group_name}_{index}",
            )
            if not isinstance(record["table"], str) or not record["table"].strip():
                raise BundleBuildError(
                    f"external_fencing_link_scan_table_required:{group_name}:{index}"
                )
            tables.append(record["table"])
            for count_name in expected_keys - {"table"}:
                count = record[count_name]
                if isinstance(count, bool) or not isinstance(count, int) or count != 0:
                    raise BundleBuildError(
                        f"external_fencing_link_scan_nonzero:{group_name}:{index}:{count_name}"
                    )
        expected_tables = (
            CONTROL_PLANE_LINK_TABLES
            if group_name == "control_plane_run_links"
            else AIRFLOW_LINK_TABLES
        )
        if len(tables) != len(expected_tables) or set(tables) != expected_tables:
            raise BundleBuildError(f"external_fencing_link_scan_table_set_mismatch:{group_name}")
    observed_counts: dict[str, int] = {}
    for group_name in ("docker_run_links", "kubernetes_run_links"):
        group = observed[group_name]
        if not isinstance(group, dict):
            raise BundleBuildError(f"external_fencing_link_scan_group_required:{group_name}")
        require_exact_keys(
            group,
            {"observed_count", "matching_count", "matches"},
            f"external_fencing_link_scan_{group_name}",
        )
        observed_count = group["observed_count"]
        if (
            isinstance(observed_count, bool)
            or not isinstance(observed_count, int)
            or observed_count < 0
        ):
            raise BundleBuildError(
                f"external_fencing_link_scan_observed_count_invalid:{group_name}"
            )
        if group["matching_count"] != 0 or group["matches"] != []:
            raise BundleBuildError(f"external_fencing_link_scan_nonzero:{group_name}")
        observed_counts[group_name] = observed_count
    for group_name in ("windows_run_links", "wsl_run_links"):
        group = observed[group_name]
        if not isinstance(group, dict):
            raise BundleBuildError(f"external_fencing_link_scan_group_required:{group_name}")
        require_exact_keys(
            group,
            {"matching_count", "matches"},
            f"external_fencing_link_scan_{group_name}",
        )
        if group["matching_count"] != 0 or group["matches"] != []:
            raise BundleBuildError(f"external_fencing_link_scan_nonzero:{group_name}")
    return observed_counts


def validate_external_terminal_fencing(
    path: Path,
    *,
    expected_successor_binding: Mapping[str, Any],
    expected_trusted_checkpoint_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    document = read_json_object(path, label="external_terminal_fencing_pins")
    require_exact_keys(
        document,
        {
            "schema",
            "target_source",
            "decision_authority",
            "target_identity",
            "target_identity_sha256",
            "successor_binding",
            "terminal_decision",
            "trusted_checkpoint",
            "snapshots",
            "exact_link_scans",
        },
        "external_terminal_fencing_pins",
    )
    if document["schema"] != EXTERNAL_FENCING_PINS_SCHEMA:
        raise BundleBuildError("external_fencing_pins_schema_mismatch")
    if document["target_source"] != "mlflow_running_rows":
        raise BundleBuildError("external_fencing_pins_source_mismatch")
    if document["decision_authority"] != EXTERNAL_DECISION_AUTHORITY:
        raise BundleBuildError("external_fencing_pins_authority_mismatch")
    successor_binding = document["successor_binding"]
    if not isinstance(successor_binding, dict):
        raise BundleBuildError("external_fencing_successor_binding_object_required")
    require_exact_keys(
        successor_binding,
        {
            "run_id",
            "attempt_id",
            "commit",
            "tree",
            "nonce",
            "parent_map_sha256",
            "staging_path",
            "output_path",
            "emergency_seal_path",
        },
        "external_fencing_successor_binding",
    )
    if successor_binding != dict(expected_successor_binding):
        raise BundleBuildError("external_fencing_successor_binding_mismatch")
    attempt_id = successor_binding["attempt_id"]
    try:
        canonical_attempt_id = str(uuid.UUID(attempt_id)) if isinstance(attempt_id, str) else ""
    except (ValueError, AttributeError) as exc:
        raise BundleBuildError("external_fencing_successor_attempt_id_invalid") from exc
    if (
        not isinstance(successor_binding["run_id"], str)
        or "r7s1" not in successor_binding["run_id"].lower()
        or canonical_attempt_id != attempt_id
        or attempt_id == successor_binding["run_id"]
        or attempt_id == successor_binding["nonce"]
        or not HEX40.fullmatch(str(successor_binding["commit"]))
        or not HEX40.fullmatch(str(successor_binding["tree"]))
        or not NONCE64.fullmatch(str(successor_binding["nonce"]))
        or not HEX64.fullmatch(str(successor_binding["parent_map_sha256"]))
        or not Path(str(successor_binding["staging_path"])).is_absolute()
        or not Path(str(successor_binding["output_path"])).is_absolute()
        or not Path(str(successor_binding["emergency_seal_path"])).is_absolute()
    ):
        raise BundleBuildError("external_fencing_successor_binding_invalid")
    trusted_expected = expected_trusted_checkpoint_sha256.lower()
    if not HEX64.fullmatch(trusted_expected):
        raise BundleBuildError("trusted_checkpoint_expected_sha256_invalid")
    target_identity = _validate_target_identity(document["target_identity"])
    identity_sha256 = _canonical_object_sha256(target_identity)
    if document["target_identity_sha256"] != identity_sha256:
        raise BundleBuildError("external_fencing_target_identity_sha_mismatch")

    snapshots = document["snapshots"]
    link_scans = document["exact_link_scans"]
    if not isinstance(snapshots, list) or len(snapshots) != 2:
        raise BundleBuildError("external_fencing_exactly_two_snapshots_required")
    if not isinstance(link_scans, list) or len(link_scans) != 2:
        raise BundleBuildError("external_fencing_exactly_two_link_scans_required")

    source_pins: list[dict[str, Any]] = []
    source_paths: set[Path] = set()
    source_times: list[datetime] = []
    snapshot_times: list[datetime] = []
    snapshot_activity: list[dict[str, Any]] = []
    scan_times: list[datetime] = []
    scan_observed_counts: list[dict[str, int]] = []
    for ordinal, raw_pin in enumerate(snapshots, start=1):
        pin, artifact_path, payload, captured = _validate_external_artifact_pin(
            raw_pin,
            expected_kind="historical_snapshot",
            expected_ordinal=ordinal,
            expected_identity_sha256=identity_sha256,
            expected_schema=HISTORICAL_SNAPSHOT_SCHEMA,
        )
        if artifact_path in source_paths:
            raise BundleBuildError("external_fencing_source_paths_must_be_distinct")
        source_paths.add(artifact_path)
        snapshot_activity.append(
            _validate_snapshot_payload(
                payload,
                ordinal=ordinal,
                captured_at=pin["captured_at"],
                target_identity=target_identity,
            )
        )
        source_pins.append(pin)
        source_times.append(captured)
        snapshot_times.append(captured)
    for ordinal, raw_pin in enumerate(link_scans, start=1):
        pin, artifact_path, payload, captured = _validate_external_artifact_pin(
            raw_pin,
            expected_kind="exact_link_scan",
            expected_ordinal=ordinal,
            expected_identity_sha256=identity_sha256,
            expected_schema=TARGET_LINK_SCAN_SCHEMA,
        )
        if artifact_path in source_paths:
            raise BundleBuildError("external_fencing_source_paths_must_be_distinct")
        source_paths.add(artifact_path)
        observed_counts = _validate_zero_link_payload(
            payload,
            ordinal=ordinal,
            captured_at=pin["captured_at"],
            target_run_id=str(target_identity["run_id"]),
        )
        source_pins.append(pin)
        source_times.append(captured)
        scan_times.append(captured)
        scan_observed_counts.append(observed_counts)
    if (snapshot_times[1] - snapshot_times[0]).total_seconds() < (
        EXTERNAL_FENCING_MIN_OBSERVATION_GAP_SECONDS
    ):
        raise BundleBuildError("external_fencing_snapshot_gap_too_short")
    if snapshot_activity[0] != snapshot_activity[1]:
        raise BundleBuildError("external_fencing_snapshot_activity_not_stable")
    if (scan_times[1] - scan_times[0]).total_seconds() < (
        EXTERNAL_FENCING_MIN_OBSERVATION_GAP_SECONDS
    ):
        raise BundleBuildError("external_fencing_link_scan_gap_too_short")
    if scan_observed_counts[0] != scan_observed_counts[1]:
        raise BundleBuildError("external_fencing_link_scan_scope_not_stable")
    if len({str(pin["source_revision"]) for pin in source_pins}) != 1:
        raise BundleBuildError("external_fencing_source_revision_not_stable")

    decision_pin = document["terminal_decision"]
    if not isinstance(decision_pin, dict):
        raise BundleBuildError("external_fencing_terminal_decision_pin_object_required")
    require_exact_keys(
        decision_pin, {"path", "sha256", "schema"}, "external_fencing_terminal_decision_pin"
    )
    if decision_pin["schema"] != EXTERNAL_FENCING_DECISION_SCHEMA:
        raise BundleBuildError("external_fencing_terminal_decision_pin_schema_mismatch")
    decision_path = _normal_path(decision_pin["path"], "external_fencing_terminal_decision")
    decision_sha = str(decision_pin["sha256"]).lower()
    if not HEX64.fullmatch(decision_sha) or sha256_file(decision_path) != decision_sha:
        raise BundleBuildError("external_fencing_terminal_decision_sha_mismatch")
    decision = read_json_object(decision_path, label="external_terminal_fencing_decision")
    if decision_path in source_paths:
        raise BundleBuildError("external_fencing_decision_path_must_be_distinct")
    require_exact_keys(
        decision,
        {
            "schema",
            "target_source",
            "issued_at",
            "decision_authority",
            "decision",
            "future_dispatch_fenced",
            "target_identity",
            "successor_binding",
            "supporting_sha256",
        },
        "external_terminal_fencing_decision",
    )
    if decision["schema"] != EXTERNAL_FENCING_DECISION_SCHEMA:
        raise BundleBuildError("external_fencing_decision_schema_mismatch")
    if decision["target_source"] != "mlflow_running_rows":
        raise BundleBuildError("external_fencing_decision_source_mismatch")
    issued_at = _parse_utc_timestamp(decision["issued_at"], "external_fencing_decision")
    if decision["decision_authority"] != EXTERNAL_DECISION_AUTHORITY:
        raise BundleBuildError("external_fencing_decision_authority_mismatch")
    if decision["decision"] != "proven_terminal_fenced":
        raise BundleBuildError("external_fencing_terminal_decision_required")
    if decision["future_dispatch_fenced"] is not True:
        raise BundleBuildError("external_fencing_future_dispatch_fence_required")
    if decision["target_identity"] != target_identity:
        raise BundleBuildError("external_fencing_decision_target_mismatch")
    if decision["successor_binding"] != successor_binding:
        raise BundleBuildError("external_fencing_decision_successor_binding_mismatch")
    expected_supporting = {
        "historical_snapshot_1": source_pins[0]["sha256"],
        "historical_snapshot_2": source_pins[1]["sha256"],
        "exact_link_scan_1": source_pins[2]["sha256"],
        "exact_link_scan_2": source_pins[3]["sha256"],
        "historical_snapshot_1_target_activity_sha256": _canonical_object_sha256(
            snapshot_activity[0]
        ),
        "historical_snapshot_2_target_activity_sha256": _canonical_object_sha256(
            snapshot_activity[1]
        ),
        "successor_binding_sha256": _canonical_object_sha256(successor_binding),
    }
    if decision["supporting_sha256"] != expected_supporting:
        raise BundleBuildError("external_fencing_decision_source_chain_mismatch")
    latest_source = max(source_times)
    decision_gap = (issued_at - latest_source).total_seconds()
    if decision_gap < EXTERNAL_FENCING_MIN_OBSERVATION_GAP_SECONDS:
        raise BundleBuildError("external_fencing_decision_observation_gap_too_short")
    if decision_gap > EXTERNAL_FENCING_MAX_AGE_SECONDS:
        raise BundleBuildError("external_fencing_decision_stale_at_issue")

    checkpoint_pin = document["trusted_checkpoint"]
    if not isinstance(checkpoint_pin, dict):
        raise BundleBuildError("trusted_checkpoint_pin_object_required")
    require_exact_keys(checkpoint_pin, {"path", "sha256", "schema"}, "trusted_checkpoint_pin")
    checkpoint_sha = str(checkpoint_pin["sha256"]).lower()
    if checkpoint_pin["schema"] != TRUSTED_CHECKPOINT_SCHEMA:
        raise BundleBuildError("trusted_checkpoint_pin_schema_mismatch")
    if checkpoint_sha != trusted_expected:
        raise BundleBuildError("trusted_checkpoint_out_of_band_sha256_mismatch")
    checkpoint_path = _normal_path(checkpoint_pin["path"], "trusted_checkpoint")
    if checkpoint_path in source_paths or checkpoint_path == decision_path:
        raise BundleBuildError("trusted_checkpoint_path_must_be_distinct")
    if sha256_file(checkpoint_path) != checkpoint_sha:
        raise BundleBuildError("trusted_checkpoint_sha256_mismatch")
    checkpoint = read_json_object(checkpoint_path, label="trusted_checkpoint")
    require_exact_keys(
        checkpoint,
        {
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
        },
        "trusted_checkpoint",
    )
    if (
        checkpoint["schema"] != TRUSTED_CHECKPOINT_SCHEMA
        or checkpoint["decision_authority"] != EXTERNAL_DECISION_AUTHORITY
        or checkpoint["successor_binding"] != successor_binding
        or checkpoint["target_source"] != "mlflow_running_rows"
        or checkpoint["target_identity_sha256"] != identity_sha256
        or checkpoint["decision_sha256"] != decision_sha
        or checkpoint["supporting_sha256"] != expected_supporting
    ):
        raise BundleBuildError("trusted_checkpoint_binding_mismatch")
    approval = checkpoint["independent_approval"]
    if not isinstance(approval, dict):
        raise BundleBuildError("trusted_checkpoint_independent_approval_object_required")
    require_exact_keys(
        approval,
        {"source", "reviewer_identity", "approval_id"},
        "trusted_checkpoint_independent_approval",
    )
    if any(not isinstance(value, str) or not value.strip() for value in approval.values()):
        raise BundleBuildError("trusted_checkpoint_independent_approval_invalid")
    fence = checkpoint["fence_readback"]
    if not isinstance(fence, dict):
        raise BundleBuildError("trusted_checkpoint_fence_readback_object_required")
    require_exact_keys(
        fence,
        {"target_run_id", "future_dispatch_fenced", "fence_state", "read_back_at"},
        "trusted_checkpoint_fence_readback",
    )
    fence_at = _parse_utc_timestamp(fence["read_back_at"], "trusted_checkpoint_fence_readback")
    if (
        fence["target_run_id"] != target_identity["run_id"]
        or fence["future_dispatch_fenced"] is not True
        or fence["fence_state"] != "fenced"
        or fence_at <= latest_source
    ):
        raise BundleBuildError("trusted_checkpoint_fence_readback_mismatch")
    checkpoint_issued_at = _parse_utc_timestamp(
        checkpoint["checkpointed_at"], "trusted_checkpoint_checkpointed_at"
    )
    expires_at = _parse_utc_timestamp(checkpoint["expires_at"], "trusted_checkpoint_expires_at")
    if checkpoint_issued_at < max(issued_at, fence_at):
        raise BundleBuildError("trusted_checkpoint_must_follow_decision")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise BundleBuildError("external_fencing_validation_now_must_be_aware")
    if checkpoint_issued_at > current:
        raise BundleBuildError("external_fencing_decision_from_future")
    if expires_at <= checkpoint_issued_at or current > expires_at:
        raise BundleBuildError("trusted_checkpoint_expired_or_invalid")
    if (current - latest_source).total_seconds() > EXTERNAL_FENCING_MAX_AGE_SECONDS:
        raise BundleBuildError("external_fencing_decision_stale")
    manifest_source_pins = [
        {
            key: pin[key]
            for key in ("path", "sha256", "schema", "captured_at", "ordinal", "source_revision")
        }
        for pin in source_pins
    ]
    return {
        "target_source": "mlflow_running_rows",
        "target_identity": target_identity,
        "successor_binding": successor_binding,
        "decision_authority": EXTERNAL_DECISION_AUTHORITY,
        "snapshots": manifest_source_pins[:2],
        "exact_link_scans": manifest_source_pins[2:],
        "terminal_decision": {
            "path": decision_pin["path"],
            "sha256": decision_pin["sha256"],
            "schema": decision_pin["schema"],
        },
        "trusted_checkpoint": {
            "path": checkpoint_pin["path"],
            "sha256": checkpoint_pin["sha256"],
            "schema": checkpoint_pin["schema"],
        },
    }


def cross_validate_external_fencing_job_scope(
    job_scope: Mapping[str, Any], external_terminal_fencing: Mapping[str, Any]
) -> None:
    classifications = job_scope.get("historical_classifications")
    if not isinstance(classifications, list):
        raise BundleBuildError("external_fencing_job_scope_classifications_required")
    mlflow_items = [
        item
        for item in classifications
        if isinstance(item, dict) and item.get("source") == "mlflow_running_rows"
    ]
    if len(mlflow_items) != 1:
        raise BundleBuildError("external_fencing_exact_mlflow_classification_required")
    item = mlflow_items[0]
    if (
        item.get("executing_count") != 0
        or item.get("unproven_count") != 0
        or item.get("classification") != "historical_nonexecuting"
    ):
        raise BundleBuildError("external_fencing_mlflow_terminal_classification_required")
    attestation = item.get("attestation")
    if not isinstance(attestation, dict):
        raise BundleBuildError("external_fencing_mlflow_attestation_required")
    path = _normal_path(attestation.get("path"), "external_fencing_mlflow_attestation")
    sha256 = str(attestation.get("sha256", "")).lower()
    if not HEX64.fullmatch(sha256) or sha256_file(path) != sha256:
        raise BundleBuildError("external_fencing_mlflow_attestation_sha_mismatch")
    payload = read_json_object(path, label="external_fencing_mlflow_attestation")
    records = payload.get("records")
    if not isinstance(records, list):
        raise BundleBuildError("external_fencing_mlflow_attestation_records_required")
    target = external_terminal_fencing["target_identity"]
    matches = [
        record
        for record in records
        if isinstance(record, dict) and record.get("identity") == target
    ]
    if len(matches) != 1:
        raise BundleBuildError("external_fencing_mlflow_target_exactly_once_required")
    match = matches[0]
    if (
        match.get("observed_state") != "RUNNING"
        or match.get("classification") != "historical_nonexecuting"
    ):
        raise BundleBuildError("external_fencing_mlflow_attestation_decision_mismatch")


def validate_runtime_state_pins(
    path: Path,
    *,
    project_root: Path,
    source_identity: Mapping[str, Any],
    parent_entries: list[dict[str, Any]],
    parent_payloads: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = read_json_object(path, label="runtime_state_pins")
    require_exact_keys(
        value,
        {
            "schema_version",
            "source_evidence",
            "compose",
            "api",
            "database",
            "kubernetes",
            "job_scope_contract",
        },
        "runtime_state_pins",
    )
    if value["schema_version"] != RUNTIME_STATE_SCHEMA:
        raise BundleBuildError("runtime_state_schema_mismatch")
    parents = {entry["role"]: entry for entry in parent_entries}
    source_evidence = value["source_evidence"]
    if not isinstance(source_evidence, dict):
        raise BundleBuildError("runtime_state_source_evidence_object_required")
    require_exact_keys(
        source_evidence, {"post_manual_on_readback", "post_manual_on_index"}, "source_evidence"
    )
    for role in ("post_manual_on_readback", "post_manual_on_index"):
        pin = source_evidence[role]
        if not isinstance(pin, dict):
            raise BundleBuildError(f"source_evidence_{role}_object_required")
        require_exact_keys(pin, {"path", "sha256"}, f"source_evidence_{role}")
        if _normal_path(pin["path"], role) != Path(parents[role]["path"]):
            raise BundleBuildError(f"source_evidence_{role}_path_mismatch")
        if str(pin["sha256"]).lower() != parents[role]["sha256"]:
            raise BundleBuildError(f"source_evidence_{role}_sha_mismatch")

    compose = value["compose"]
    if not isinstance(compose, dict):
        raise BundleBuildError("runtime_state_compose_object_required")
    require_exact_keys(
        compose,
        {
            "project_name",
            "config_path",
            "config_sha256",
            "long_lived_services",
            "one_shot_services",
            "service_pins",
            "stability",
        },
        "runtime_state_compose",
    )
    config_path = _normal_path(compose["config_path"], "compose_config")
    expected_config = (project_root / "docker-compose.yml").resolve()
    if config_path != expected_config or not config_path.is_file():
        raise BundleBuildError("compose_config_path_mismatch")
    if compose["project_name"] != "enterprise-vision-mlops":
        raise BundleBuildError("compose_project_name_mismatch")
    if str(compose["config_sha256"]).lower() != sha256_file(config_path):
        raise BundleBuildError("compose_config_sha_mismatch")
    if compose["long_lived_services"] != list(LONG_LIVED_SERVICES):
        raise BundleBuildError("compose_long_lived_services_mismatch")
    if compose["one_shot_services"] != list(ONE_SHOT_SERVICES):
        raise BundleBuildError("compose_one_shot_services_mismatch")
    service_pins = compose["service_pins"]
    if not isinstance(service_pins, dict) or set(service_pins) != set(LONG_LIVED_SERVICES):
        raise BundleBuildError("compose_service_pin_set_mismatch")
    container_ids: set[str] = set()
    for service in LONG_LIVED_SERVICES:
        pin = service_pins[service]
        if not isinstance(pin, dict):
            raise BundleBuildError(f"compose_service_pin_object_required:{service}")
        require_exact_keys(
            pin,
            {"container_name", "container_id", "image_id", "healthcheck_expected"},
            f"compose_service_pin_{service}",
        )
        if pin["container_name"] != CONTAINER_NAMES[service]:
            raise BundleBuildError(f"compose_container_name_mismatch:{service}")
        if not isinstance(pin["container_id"], str) or not HEX64.fullmatch(pin["container_id"]):
            raise BundleBuildError(f"compose_container_id_invalid:{service}")
        if pin["container_id"] in container_ids:
            raise BundleBuildError("compose_container_ids_must_be_distinct")
        container_ids.add(pin["container_id"])
        if not isinstance(pin["image_id"], str) or not IMAGE_ID.fullmatch(pin["image_id"]):
            raise BundleBuildError(f"compose_image_id_invalid:{service}")
        if pin["healthcheck_expected"] is not HEALTHCHECK_EXPECTED[service]:
            raise BundleBuildError(f"compose_healthcheck_contract_mismatch:{service}")
    if compose["stability"] != {
        "duration_seconds": 300,
        "interval_seconds": 5,
        "samples": 61,
        "restart_delta": 0,
    }:
        raise BundleBuildError("compose_stability_contract_mismatch")

    api = value["api"]
    if not isinstance(api, dict):
        raise BundleBuildError("runtime_state_api_object_required")
    require_exact_keys(
        api,
        {
            "base_url",
            "api_container_name",
            "worker_container_name",
            "image_id",
            "image_attestation",
            "source_revision",
            "source_tree",
        },
        "runtime_state_api",
    )
    if api["base_url"] != "http://127.0.0.1:8000":
        raise BundleBuildError("api_base_url_mismatch")
    if (
        api["api_container_name"] != "evm-api"
        or api["worker_container_name"] != "evm-task-queue-worker"
    ):
        raise BundleBuildError("api_container_name_mismatch")
    if not isinstance(api["image_id"], str) or not IMAGE_ID.fullmatch(api["image_id"]):
        raise BundleBuildError("api_image_id_invalid")
    if (
        api["source_revision"] != source_identity["revision"]
        or api["source_tree"] != source_identity["tree"]
    ):
        raise BundleBuildError("api_source_identity_mismatch")
    image_attestation = api["image_attestation"]
    if not isinstance(image_attestation, dict):
        raise BundleBuildError("api_image_attestation_object_required")
    require_exact_keys(image_attestation, {"path", "sha256"}, "api_image_attestation")
    attestation_path = _normal_path(image_attestation["path"], "api_image_attestation")
    attestation_sha = str(image_attestation["sha256"]).lower()
    if not HEX64.fullmatch(attestation_sha) or sha256_file(attestation_path) != attestation_sha:
        raise BundleBuildError("api_image_attestation_sha_mismatch")
    attestation = read_json_object(attestation_path, label="api_image_attestation")
    for scalar in (api["image_id"], source_identity["revision"], source_identity["tree"]):
        if not contains_scalar(attestation, str(scalar)):
            raise BundleBuildError(f"api_image_attestation_identity_missing:{scalar}")

    database = value["database"]
    if not isinstance(database, dict):
        raise BundleBuildError("runtime_state_database_object_required")
    require_exact_keys(
        database,
        {
            "control_plane_schema_versions",
            "airflow_migration_head",
            "mlflow_migration_head",
            "instances",
        },
        "runtime_state_database",
    )
    canonical_versions = source_schema_versions(project_root)
    if database["control_plane_schema_versions"] != canonical_versions:
        raise BundleBuildError("control_plane_schema_versions_source_mismatch")
    if database["airflow_migration_head"] != AIRFLOW_MIGRATION_HEAD:
        raise BundleBuildError("airflow_migration_head_mismatch")
    if database["mlflow_migration_head"] != MLFLOW_MIGRATION_HEAD:
        raise BundleBuildError("mlflow_migration_head_mismatch")
    expected_instances = {
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
    if database["instances"] != expected_instances:
        raise BundleBuildError("database_instances_mismatch")

    kubernetes = value["kubernetes"]
    if not isinstance(kubernetes, dict):
        raise BundleBuildError("runtime_state_kubernetes_object_required")
    require_exact_keys(
        kubernetes,
        {"allowed_historical_failed_pods", "health_confirmation_samples", "residual_selectors"},
        "runtime_state_kubernetes",
    )
    if kubernetes["health_confirmation_samples"] != 2:
        raise BundleBuildError("kubernetes_health_confirmation_samples_mismatch")
    if kubernetes["residual_selectors"] != ["evm.openai.local/scenario=s8-v4-x1"]:
        raise BundleBuildError("kubernetes_residual_selectors_mismatch")
    allowlist = kubernetes["allowed_historical_failed_pods"]
    if not isinstance(allowlist, list) or len(allowlist) != 14:
        raise BundleBuildError("kubernetes_failed_pod_allowlist_required")
    identities: list[tuple[str, str, str]] = []
    taxonomy = {
        "pod.status.reason": 0,
        "owner_job.status.conditions[type=Failed].reason": 0,
    }
    for index, item in enumerate(allowlist):
        if not isinstance(item, dict):
            raise BundleBuildError(f"kubernetes_allowlist_object_required:{index}")
        require_exact_keys(
            item,
            {
                "uid",
                "name",
                "namespace",
                "reason",
                "reason_source",
                "owner_uid",
                "owner_kind",
                "owner_name",
                "owner_controller",
            },
            f"kubernetes_allowlist_{index}",
        )
        if not UUID.fullmatch(str(item["uid"])) or not UUID.fullmatch(str(item["owner_uid"])):
            raise BundleBuildError(f"kubernetes_allowlist_uid_invalid:{index}")
        if not all(
            isinstance(item[key], str) and item[key].strip()
            for key in (
                "name",
                "namespace",
                "reason",
                "reason_source",
                "owner_kind",
                "owner_name",
            )
        ):
            raise BundleBuildError(f"kubernetes_allowlist_text_invalid:{index}")
        if item["owner_controller"] is not True:
            raise BundleBuildError(f"kubernetes_allowlist_controller_owner_required:{index}")
        b0_terminal = (
            item["namespace"] == "evm-production"
            and item["name"].startswith("evm-b0-production-")
            and item["reason"] == "UnexpectedAdmissionError"
            and item["reason_source"] == "pod.status.reason"
            and item["owner_kind"] == "ReplicaSet"
        )
        training_terminal = (
            item["namespace"] == "evm-training"
            and item["name"].startswith("evm-lifecycle-train-")
            and item["reason"] == "BackoffLimitExceeded"
            and item["reason_source"] == "owner_job.status.conditions[type=Failed].reason"
            and item["owner_kind"] == "Job"
        )
        if not (b0_terminal or training_terminal):
            raise BundleBuildError(f"kubernetes_allowlist_identity_mismatch:{index}")
        taxonomy[str(item["reason_source"])] += 1
        identities.append((item["namespace"], item["name"], item["uid"]))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise BundleBuildError("kubernetes_allowlist_must_be_unique_sorted")
    if taxonomy != {
        "pod.status.reason": 11,
        "owner_job.status.conditions[type=Failed].reason": 3,
    }:
        raise BundleBuildError("kubernetes_allowlist_taxonomy_mismatch")

    job_scope = _validate_job_scope(
        value["job_scope_contract"],
        expected_kubernetes_uids={str(item["uid"]) for item in allowlist},
    )
    expected_runtime_state = {
        "compose": compose,
        "api": api,
        "database": database,
        "kubernetes": kubernetes,
        "job_scope_contract": job_scope,
    }
    readback = parent_payloads["post_manual_on_readback"]
    if readback.get("runtime_state") != expected_runtime_state:
        raise BundleBuildError("post_manual_on_readback_runtime_state_mismatch")
    readback_parent = parents["post_manual_on_readback"]
    index = parent_payloads["post_manual_on_index"]
    if not contains_scalar(index, readback_parent["path"]) or not contains_scalar(
        index, readback_parent["sha256"]
    ):
        raise BundleBuildError("post_manual_on_index_readback_link_missing")
    return expected_runtime_state, value


def build_manifest(
    *,
    run_id: str,
    attempt_id: str,
    successor_nonce: str,
    source_identity: Mapping[str, Any],
    project_root: Path,
    staging_directory: Path,
    output_directory: Path,
    emergency_seal_directory: Path,
    python_path: Path,
    runtime: Mapping[str, Any],
    parent_checkpoints: list[dict[str, Any]],
    expected_state: Mapping[str, Any],
    external_terminal_fencing: Mapping[str, Any],
    expected_trusted_checkpoint_sha256: str,
    toolchain: Mapping[str, Any],
) -> dict[str, Any]:
    resolved_project_root = project_root.resolve()
    expected_compose_path = (resolved_project_root / RUNTIME_PATHS["docker_compose"]).resolve()
    runtime_compose = runtime.get("docker_compose")
    if not isinstance(runtime_compose, Mapping):
        raise BundleBuildError("runtime_docker_compose_pin_required")
    runtime_compose_path = Path(str(runtime_compose.get("path", ""))).resolve()
    if runtime_compose_path != expected_compose_path or not expected_compose_path.is_file():
        raise BundleBuildError("runtime_docker_compose_project_subdir_path_mismatch")
    runtime_compose_sha = str(runtime_compose.get("sha256", "")).lower()
    if (
        not HEX64.fullmatch(runtime_compose_sha)
        or sha256_file(expected_compose_path) != runtime_compose_sha
    ):
        raise BundleBuildError("runtime_docker_compose_sha_mismatch")
    expected_compose = expected_state.get("compose")
    if not isinstance(expected_compose, Mapping):
        raise BundleBuildError("expected_state_compose_pin_required")
    if Path(str(expected_compose.get("config_path", ""))).resolve() != expected_compose_path:
        raise BundleBuildError("expected_state_docker_compose_project_subdir_path_mismatch")
    if str(expected_compose.get("config_sha256", "")).lower() != runtime_compose_sha:
        raise BundleBuildError("expected_state_docker_compose_sha_mismatch")
    parent_map_digest = parent_map_sha256(parent_checkpoints)
    binding = external_terminal_fencing.get("successor_binding")
    if not isinstance(binding, Mapping) or binding != {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "commit": source_identity["revision"],
        "tree": source_identity["tree"],
        "nonce": successor_nonce,
        "parent_map_sha256": parent_map_digest,
        "staging_path": str(staging_directory.resolve()),
        "output_path": str(output_directory.resolve()),
        "emergency_seal_path": str(emergency_seal_directory.resolve(strict=False)),
    }:
        raise BundleBuildError("manifest_external_successor_binding_mismatch")
    trusted_pin = external_terminal_fencing.get("trusted_checkpoint")
    if (
        not isinstance(trusted_pin, Mapping)
        or str(trusted_pin.get("sha256", "")).lower() != expected_trusted_checkpoint_sha256.lower()
    ):
        raise BundleBuildError("manifest_trusted_checkpoint_sha256_mismatch")
    manifest = {
        "schema_version": "evm.s8_v4.x1_phase_b2_r7s1_restore_work_order.v1",
        "work_order_id": "s8-v4-x1-phase-b2-r7s1-restore-only-validation",
        "bundle_id": run_id,
        "execution_mode": RESTORE_MODE,
        "created_at": utc_now(),
        "canonical_revision": source_identity["revision"],
        "canonical_tree": source_identity["tree"],
        "bundle": {
            "path": str(staging_directory.resolve()),
        },
        "repository": {
            "preserved_untracked_count": source_identity["untracked"],
            "untracked_path_set_sha256": source_identity["untracked_path_digest_sha256"],
            "untracked_path_set_encoding": ("ordinal-sorted UTF-8 paths, each NUL-terminated"),
            "tracked_changes": 0,
        },
        "parent_checkpoints": parent_checkpoints,
        "output": {
            "path": str(output_directory.resolve()),
            "must_not_exist_before_runner": True,
            "write_mode": "create-exclusive",
        },
        "timeout_contract": {
            "kubectl_timeout_seconds": 8.0,
            "wrapper_timeout_seconds": 15.0,
            "restore_deadline_seconds": 600.0,
            "residual_repoll_seconds": 120.0,
            "stream_drain_seconds": 5.0,
        },
        "lifecycle_timeout_contract": {
            "compose_internal_seconds": 120.0,
            "compose_wrapper_seconds": 150.0,
            "desktop_internal_seconds": 300.0,
            "desktop_wrapper_seconds": 330.0,
            "sampler_internal_seconds": 180.0,
            "sampler_wrapper_seconds": 210.0,
            "attempt_deadline_seconds": 1200.0,
        },
        "process_containment": {
            "provider": "windows_job_object",
            "create_suspended": True,
            "assign_before_resume": True,
            "breakaway_allowed": False,
            "kill_on_job_close": False,
            "terminate_job_object_allowed": False,
            "job_accounting_authoritative": True,
            "stdio_drain_before_followup": True,
            "residual_repoll_seconds": 120,
            "force_termination_attempts": 0,
            "wsl_run_uuid_and_process_group": True,
            "wsl_proc_residual_check": True,
            "scope_boundaries": {
                name: dict(value) for name, value in PROCESS_SCOPE_BOUNDARIES.items()
            },
        },
        "probe_max_attempts": 1,
        "call_contract": {
            "restore-only": {
                "docker_off_probe": 0,
                "compose_stop": 0,
                "desktop_stop": 0,
                "wsl_shutdown": 0,
                "desktop_start": 0,
                "compose_start": 0,
            },
            "launcher": {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
            "collectors": {
                "windows_fresh_collector": 0,
                "wsl_fresh_collector": 0,
            },
            "downstream": {
                "full_stack_3180": 0,
                "q0": 0,
                "calibration_54": 0,
                "matrix_78": 0,
                "integrated_v4": 0,
                "etw": 0,
            },
        },
        "expected_state": {
            "compose": dict(expected_state["compose"]),
            "api": dict(expected_state["api"]),
            "database": dict(expected_state["database"]),
            "kubernetes": dict(expected_state["kubernetes"]),
            "compose_services": list(LONG_LIVED_SERVICES),
            "api_base_url": expected_state["api"]["base_url"],
            "b0": {
                "uid": EXPECTED_B0_UID,
                "uid_basis": (
                    "tracked canonical status evidence predating r4 and immutable deployment identity"
                ),
                "image": EXPECTED_B0_IMAGE,
                "ready_url": "http://127.0.0.1:30800/ready",
                "predict_url": "http://127.0.0.1:30800/predict",
                "sample_image_uri": (
                    "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
                ),
            },
            "prometheus_jobs": [
                "evm-api",
                "evm-b0-production",
                "evm-otel-collector",
                "evm-task-queue-worker",
                "prometheus",
            ],
            "prometheus_targets_url": "http://127.0.0.1:9090/api/v1/targets",
            "gpu_lease_path": (
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json"
            ),
            "active_job_roots": [],
            "active_claim_roots": [],
            "x1_residue_paths": [
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
                "prometheus-targets/s8-v4-x1-triton.json",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
                "prometheus-targets/s8-v4-x1-api.json",
            ],
            "x1_docker_name_filter": "name=evm-x1",
            "x1_ports": [31120, 31121, 31122],
            "x1_kubernetes_selectors": list(expected_state["kubernetes"]["residual_selectors"]),
        },
        "job_scope_contract": dict(expected_state["job_scope_contract"]),
        "external_terminal_fencing": dict(external_terminal_fencing),
        "etw_contract": {
            "decision": (
                "existing_pinned_etw_evidence_is_admissible;"
                "fresh_capture_not_a_phase_b2_go_invariant"
            ),
            "amendment_path": str(ETW_AMENDMENT),
            "amendment_sha256": ETW_AMENDMENT_SHA256,
            "fresh_capture_required_for_phase_b2_go": False,
            "fresh_invocations": 0,
        },
        "evidence": {
            "write_mode": "create-exclusive",
            "failure_creates_completion_marker": False,
            "failure_index_is_not_success_index": True,
            "restore_only_creates_completion_marker": False,
            "success_requires_all_invariants": True,
        },
        "runtime": dict(runtime),
        "toolchain": dict(toolchain),
    }
    expected_core_path = (resolved_project_root / RUNTIME_PATHS["core"]).resolve()
    runtime_core = runtime.get("core")
    if not isinstance(runtime_core, Mapping):
        raise BundleBuildError("runtime_core_pin_required")
    runtime_core_path = Path(str(runtime_core.get("path", ""))).resolve()
    if runtime_core_path != expected_core_path:
        raise BundleBuildError("runtime_core_path_mismatch")
    runtime_core_sha = str(runtime_core.get("sha256", "")).lower()
    if not HEX64.fullmatch(runtime_core_sha) or sha256_file(expected_core_path) != runtime_core_sha:
        raise BundleBuildError("runtime_core_sha_mismatch")
    if not python_path.is_file():
        raise BundleBuildError(f"python_missing_for_core_validation:{python_path}")
    core_probe = (
        "import inspect,json,pathlib,sys\n"
        "root=pathlib.Path(sys.argv[1]).resolve()\n"
        "expected_core=pathlib.Path(sys.argv[2]).resolve()\n"
        "sys.path.insert(0,str(root/'src'))\n"
        "import evm.scale_validation.phase_b2_r7s1 as core\n"
        "actual_core=pathlib.Path(core.__file__).resolve()\n"
        "assert actual_core==expected_core,(actual_core,expected_core)\n"
        "contract=json.loads(sys.argv[5])\n"
        "assert core.HISTORICAL_QUERY_SHA256==contract['query_sha256']\n"
        "assert core.HISTORICAL_DECISION_AUTHORITY==contract['decision_authority']\n"
        "import base64,zlib\n"
        "manifest=json.loads(zlib.decompress(base64.b64decode(sys.argv[7])).decode('ascii'))\n"
        "kwargs={'expected_revision':sys.argv[3],"
        "'expected_untracked_path_set_sha256':sys.argv[4]}\n"
        "if 'verify_attestations' in inspect.signature(core.validate_r7s1_manifest).parameters:"
        " kwargs['verify_attestations']=True\n"
        "if 'expected_trusted_checkpoint_sha256' in inspect.signature(core.validate_r7s1_manifest).parameters:"
        " kwargs['expected_trusted_checkpoint_sha256']=sys.argv[6]\n"
        "core.validate_r7s1_manifest(manifest,**kwargs)\n"
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    manifest_argument = base64.b64encode(
        zlib.compress(canonical_json_bytes(manifest), level=9)
    ).decode("ascii")
    result = _run_contained(
        [
            str(python_path.resolve()),
            "-I",
            "-S",
            "-B",
            "-c",
            core_probe,
            str(resolved_project_root),
            str(expected_core_path),
            str(source_identity["revision"]),
            str(source_identity["untracked_path_digest_sha256"]),
            json.dumps(
                {
                    "query_sha256": HISTORICAL_QUERY_SHA256,
                    "decision_authority": HISTORICAL_DECISION_AUTHORITY,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            expected_trusted_checkpoint_sha256.lower(),
            manifest_argument,
        ],
        name="r7s1-builder-core-manifest-validation",
        cwd=resolved_project_root,
        env=environment,
    )
    if result.return_code != 0:
        raise BundleBuildError(
            "core_manifest_validation_failed:"
            + (result.stderr.strip() or result.stdout.strip() or str(result.return_code))
        )
    return manifest


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_powershell_entry_guard_function() -> str:
    return r"""function Assert-CanonicalPowerShellEntry(
  [string]$ExpectedPowerShellPath,[string]$ExpectedPowerShellSha256,
  [int64]$ExpectedPowerShellBytes,[string]$ExpectedScriptPath,
  [string]$ExpectedOuterSha256,[string]$ExpectedTrustedCheckpointSha256,
  [string]$ExpectedOutputDirectory
) {
  $argv = [Environment]::GetCommandLineArgs()
  foreach ($argument in $argv) {
    if ($argument -imatch '^-(?:c|command|e|encodedcommand)$') {
      throw 'canonical_powershell_entry_command_or_encoded_command_rejected'
    }
  }
  $expected = [string[]]@(
    $ExpectedPowerShellPath,'-NoProfile','-NonInteractive','-File',$ExpectedScriptPath,
    '-ExpectedOuterSha256',$ExpectedOuterSha256,
    '-ExpectedTrustedCheckpointSha256',$ExpectedTrustedCheckpointSha256,
    '-OutputDirectory',$ExpectedOutputDirectory
  )
  if ($argv.Count -ne $expected.Count) {
    throw "canonical_powershell_entry_argv_count_mismatch:$($argv.Count)"
  }
  foreach ($index in 0..($expected.Count - 1)) {
    if ($index -in @(0,4,10)) {
      if (-not [IO.Path]::GetFullPath($argv[$index]).Equals(
          [IO.Path]::GetFullPath($expected[$index]),
          [StringComparison]::OrdinalIgnoreCase)) {
        throw "canonical_powershell_entry_path_mismatch:$index"
      }
    } elseif ($argv[$index] -cne $expected[$index]) {
      throw "canonical_powershell_entry_argument_mismatch:$index"
    }
  }
  $process = [Diagnostics.Process]::GetCurrentProcess()
  $entryHostPath = [IO.Path]::GetFullPath([string]$process.MainModule.FileName)
  if (-not $entryHostPath.Equals(
      [IO.Path]::GetFullPath($ExpectedPowerShellPath),
      [StringComparison]::OrdinalIgnoreCase) -or
      (Get-Sha256 $entryHostPath) -cne $ExpectedPowerShellSha256 -or
      [IO.FileInfo]::new($entryHostPath).Length -ne $ExpectedPowerShellBytes) {
    throw 'canonical_powershell_entry_host_identity_mismatch'
  }
}"""


def _render_location_fence_functions() -> str:
    return r"""function Get-BoundPathVolumeIdentity([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path)
  $root = [IO.Path]::GetPathRoot($full)
  $device = $root.TrimEnd([char]92,[char]47)
  $escapedDevice = $device.Replace("'","''")
  $disk = CimCmdlets\\Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$escapedDevice'" -ErrorAction Stop
  if ($null -eq $disk -or [string]::IsNullOrWhiteSpace([string]$disk.FileSystem) -or
      [string]::IsNullOrWhiteSpace([string]$disk.VolumeSerialNumber)) {
    throw "bound_path_volume_identity_unavailable:$full"
  }
  [ordered]@{
    volume_root=$root
    volume_serial=([string]$disk.VolumeSerialNumber).Replace('-','').ToLowerInvariant()
    filesystem=[string]$disk.FileSystem
  }
}
function Assert-BoundRunLocation(
  [string]$Path,[string]$ExpectedPath,[string]$ExpectedVolumeRoot,
  [string]$ExpectedVolumeSerial,[string]$ExpectedFilesystem,
  [bool]$ExpectedExists,[string]$Label
) {
  $full = [IO.Path]::GetFullPath($Path)
  if (-not $full.Equals([IO.Path]::GetFullPath($ExpectedPath),[StringComparison]::OrdinalIgnoreCase)) {
    throw "bound_run_location_path_mismatch:$Label"
  }
  $candidate = $full
  while (-not [string]::IsNullOrWhiteSpace($candidate)) {
    $item = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    if ($null -ne $item) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "bound_run_location_reparse_ancestor:${Label}:$candidate"
      }
    }
    $trimmed = $candidate.TrimEnd([char]92,[char]47)
    $parent = [IO.Path]::GetDirectoryName($trimmed)
    if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) { break }
    $candidate = $parent
  }
  if ((Test-Path -LiteralPath $full) -ne $ExpectedExists) {
    throw "bound_run_location_existence_mismatch:$Label"
  }
  $identity = Get-BoundPathVolumeIdentity $full
  if (-not ([string]$identity.volume_root).Equals(
      $ExpectedVolumeRoot,[StringComparison]::OrdinalIgnoreCase) -or
      [string]$identity.volume_serial -cne $ExpectedVolumeSerial -or
      -not ([string]$identity.filesystem).Equals(
        $ExpectedFilesystem,[StringComparison]::OrdinalIgnoreCase)) {
    throw "bound_run_location_volume_filesystem_mismatch:$Label"
  }
}"""


def _render_git_config_guard_function() -> str:
    return r"""function Assert-GitRepositoryConfigPin(
  [string]$Path,[string]$ExpectedSha256,[int64]$ExpectedBytes
) {
  $full = [IO.Path]::GetFullPath($Path)
  $candidate = $full
  while (-not [string]::IsNullOrWhiteSpace($candidate)) {
    if ([IO.File]::Exists($candidate) -or [IO.Directory]::Exists($candidate)) {
      $attributes = [IO.File]::GetAttributes($candidate)
      if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "git_repository_config_reparse_ancestor:$candidate"
      }
    }
    $trimmed = $candidate.TrimEnd([char]92,[char]47)
    $parent = [IO.Path]::GetDirectoryName($trimmed)
    if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) { break }
    $candidate = $parent
  }
  if (-not [IO.File]::Exists($full) -or
      [IO.FileInfo]::new($full).Length -ne $ExpectedBytes -or
      (Get-Sha256 $full) -cne $ExpectedSha256) {
    throw 'git_repository_config_identity_mismatch'
  }
  $worktree = [IO.Path]::Combine([IO.Path]::GetDirectoryName($full),'config.worktree')
  if ([IO.File]::Exists($worktree) -or [IO.Directory]::Exists($worktree)) {
    throw 'git_repository_config_worktree_must_be_absent'
  }
}
function Assert-GitRepositoryAttributesPin(
  [string]$Path,[string]$ExpectedSha256,[int64]$ExpectedBytes,
  [string]$GitTopAttributesPath,[string]$GitInfoAttributesPath
) {
  foreach ($rawPath in @($Path,$GitTopAttributesPath,$GitInfoAttributesPath)) {
    $candidate = [IO.Path]::GetFullPath($rawPath)
    while (-not [string]::IsNullOrWhiteSpace($candidate)) {
      if ([IO.File]::Exists($candidate) -or [IO.Directory]::Exists($candidate)) {
        if (([IO.File]::GetAttributes($candidate) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
          throw "git_repository_attributes_reparse_ancestor:$candidate"
        }
      }
      $trimmed = $candidate.TrimEnd([char]92,[char]47)
      $parent = [IO.Path]::GetDirectoryName($trimmed)
      if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) { break }
      $candidate = $parent
    }
  }
  $full = [IO.Path]::GetFullPath($Path)
  if (-not [IO.File]::Exists($full) -or
      [IO.FileInfo]::new($full).Length -ne $ExpectedBytes -or
      (Get-Sha256 $full) -cne $ExpectedSha256) {
    throw 'git_repository_attributes_identity_mismatch'
  }
  foreach ($absentPath in @($GitTopAttributesPath,$GitInfoAttributesPath)) {
    if ([IO.File]::Exists($absentPath) -or [IO.Directory]::Exists($absentPath)) {
      throw "external_git_attributes_must_be_absent:$absentPath"
    }
  }
}"""


def _render_client_config_guard_function() -> str:
    return r"""function Assert-PinnedClientFile(
  [string]$Path,[string]$ExpectedSha256,[int64]$ExpectedBytes,[string]$Label
) {
  $full = [IO.Path]::GetFullPath($Path)
  $candidate = $full
  while (-not [string]::IsNullOrWhiteSpace($candidate)) {
    if ([IO.File]::Exists($candidate) -or [IO.Directory]::Exists($candidate)) {
      if (([IO.File]::GetAttributes($candidate) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "client_configuration_reparse_ancestor:${Label}:$candidate"
      }
    }
    $trimmed = $candidate.TrimEnd([char]92,[char]47)
    $parent = [IO.Path]::GetDirectoryName($trimmed)
    if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) { break }
    $candidate = $parent
  }
  if (-not [IO.File]::Exists($full) -or
      [IO.FileInfo]::new($full).Length -ne $ExpectedBytes -or
      (Get-Sha256 $full) -cne $ExpectedSha256) {
    throw "client_configuration_identity_mismatch:$Label"
  }
}
function Assert-ClientConfigurationPins {
  Assert-PinnedClientFile $PinnedDockerClientConfigPath $PinnedDockerClientConfigSha256 $PinnedDockerClientConfigBytes 'docker_config'
  Assert-PinnedClientFile $PinnedDockerContextMetadataPath $PinnedDockerContextMetadataSha256 $PinnedDockerContextMetadataBytes 'docker_context_metadata'
  Assert-PinnedClientFile $PinnedKubernetesClientConfigPath $PinnedKubernetesClientConfigSha256 $PinnedKubernetesClientConfigBytes 'kubernetes_config'
  if ([IO.File]::Exists($PinnedDockerContextTlsPath) -or
      [IO.Directory]::Exists($PinnedDockerContextTlsPath)) {
    throw 'docker_context_tls_material_directory_must_be_absent'
  }
}"""


def render_outer(
    *,
    bridge_sha256: str,
    run_id: str,
    trusted_checkpoint_sha256: str,
    toolchain: Mapping[str, Any],
    successor_binding: Mapping[str, Any],
) -> str:
    powershell_pin = toolchain["powershell"]
    python_distribution = toolchain["python_distribution"]
    git_distribution = toolchain["git_distribution"]
    git_repository_config = toolchain["git_repository_config"]
    git_repository_attributes = toolchain["git_repository_attributes"]
    docker_client_config = toolchain["docker_client_config"]
    kubernetes_client_config = toolchain["kubernetes_client_config"]
    assert isinstance(powershell_pin, Mapping)
    assert isinstance(python_distribution, Mapping)
    assert isinstance(git_distribution, Mapping)
    assert isinstance(git_repository_config, Mapping)
    assert isinstance(git_repository_attributes, Mapping)
    assert isinstance(docker_client_config, Mapping)
    assert isinstance(kubernetes_client_config, Mapping)
    docker_context_metadata = docker_client_config["context_metadata"]
    assert isinstance(docker_context_metadata, Mapping)
    git_attributes_path = Path(str(git_repository_attributes["path"])).resolve()
    git_top_attributes_path = git_attributes_path.parent.parent / ".gitattributes"
    git_info_attributes_path = git_attributes_path.parent.parent / ".git" / "info" / "attributes"
    staging_identity = path_filesystem_identity(Path(str(successor_binding["staging_path"])))
    output_identity = path_filesystem_identity(Path(str(successor_binding["output_path"])))
    emergency_identity = path_filesystem_identity(
        Path(str(successor_binding["emergency_seal_path"]))
    )
    location_volume_projection = {
        (
            str(identity["volume_root"]).casefold(),
            str(identity["volume_serial"]).casefold(),
            str(identity["filesystem"]).casefold(),
        )
        for identity in (staging_identity, output_identity, emergency_identity)
    }
    if len(location_volume_projection) != 1:
        raise BundleBuildError("run_locations_same_filesystem_required")
    location_functions = _render_location_fence_functions()
    git_config_guard_function = _render_git_config_guard_function()
    client_config_guard_function = _render_client_config_guard_function()
    entry_guard_function = _render_powershell_entry_guard_function()
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedTrustedCheckpointSha256,
  [Parameter(Mandatory = $true)][string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedBridgeSha256 = '{bridge_sha256}'
$PinnedRunId = {_ps_literal(run_id)}
$PinnedTrustedCheckpointSha256 = '{trusted_checkpoint_sha256.lower()}'
$PinnedPowerShellPath = {_ps_literal(str(powershell_pin["path"]))}
$PinnedPowerShellSha256 = '{powershell_pin["sha256"]}'
$PinnedPowerShellBytes = {int(powershell_pin["bytes"])}
$PinnedPythonDistributionRoot = {_ps_literal(str(python_distribution["base_prefix"]))}
$PinnedPythonDistributionSha256 = '{python_distribution["distribution_tree_sha256"]}'
$PinnedPythonDistributionFileCount = {int(python_distribution["file_count"])}
$PinnedGitDistributionRoot = {_ps_literal(str(git_distribution["root"]))}
$PinnedGitDistributionSha256 = '{git_distribution["distribution_tree_sha256"]}'
$PinnedGitDistributionFileCount = {int(git_distribution["file_count"])}
$PinnedGitRepositoryConfigPath = {_ps_literal(str(git_repository_config["path"]))}
$PinnedGitRepositoryConfigSha256 = '{git_repository_config["sha256"]}'
$PinnedGitRepositoryConfigBytes = {int(git_repository_config["bytes"])}
$PinnedGitRepositoryAttributesPath = {_ps_literal(str(git_repository_attributes["path"]))}
$PinnedGitRepositoryAttributesSha256 = '{git_repository_attributes["sha256"]}'
$PinnedGitRepositoryAttributesBytes = {int(git_repository_attributes["bytes"])}
$PinnedGitTopAttributesPath = {_ps_literal(str(git_top_attributes_path.resolve()))}
$PinnedGitInfoAttributesPath = {_ps_literal(str(git_info_attributes_path.resolve()))}
$PinnedDockerClientConfigPath = {_ps_literal(str(docker_client_config["path"]))}
$PinnedDockerClientConfigSha256 = '{docker_client_config["sha256"]}'
$PinnedDockerClientConfigBytes = {int(docker_client_config["bytes"])}
$PinnedDockerContextMetadataPath = {_ps_literal(str(docker_context_metadata["path"]))}
$PinnedDockerContextMetadataSha256 = '{docker_context_metadata["sha256"]}'
$PinnedDockerContextMetadataBytes = {int(docker_context_metadata["bytes"])}
$PinnedDockerContextTlsPath = {_ps_literal(str(EXPECTED_DOCKER_CONTEXT_TLS_PATH.resolve()))}
$PinnedKubernetesClientConfigPath = {_ps_literal(str(kubernetes_client_config["path"]))}
$PinnedKubernetesClientConfigSha256 = '{kubernetes_client_config["sha256"]}'
$PinnedKubernetesClientConfigBytes = {int(kubernetes_client_config["bytes"])}
$PinnedStagingPath = {_ps_literal(str(successor_binding["staging_path"]))}
$PinnedOutputPath = {_ps_literal(str(successor_binding["output_path"]))}
$PinnedEmergencySealPath = {_ps_literal(str(successor_binding["emergency_seal_path"]))}
$PinnedLocationVolumeRoot = {_ps_literal(str(staging_identity["volume_root"]))}
$PinnedLocationVolumeSerial = {_ps_literal(str(staging_identity["volume_serial"]))}
$PinnedLocationFilesystem = {_ps_literal(str(staging_identity["filesystem"]))}

{location_functions}

{git_config_guard_function}

{client_config_guard_function}

function Get-Sha256([string]$Path) {{
  $hashStream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
  $hasher = [Security.Cryptography.SHA256]::Create()
  try {{
    ([BitConverter]::ToString($hasher.ComputeHash($hashStream))).Replace('-','').ToLowerInvariant()
  }} finally {{ $hasher.Dispose(); $hashStream.Dispose() }}
}}
{entry_guard_function}
function Get-TokenElevationTypeValue([IntPtr]$Token) {{
  $assemblyName = [Reflection.AssemblyName]::new("R7S1TokenNative_$PID")
  $assembly = [Reflection.Emit.AssemblyBuilder]::DefineDynamicAssembly(
    $assemblyName,[Reflection.Emit.AssemblyBuilderAccess]::Run
  )
  $module = $assembly.DefineDynamicModule($assemblyName.Name)
  $type = $module.DefineType(
    'R7S1TokenNative',[Reflection.TypeAttributes]'Public,Abstract,Sealed'
  )
  $parameterTypes = [Type[]]@(
    [IntPtr],[int],[IntPtr],[int],[int].MakeByRefType()
  )
  $method = $type.DefinePInvokeMethod(
    'GetTokenInformation','advapi32.dll',
    [Reflection.MethodAttributes]'Public,Static',
    [Reflection.CallingConventions]::Standard,
    [bool],$parameterTypes,
    [Runtime.InteropServices.CallingConvention]::Winapi,
    [Runtime.InteropServices.CharSet]::Unicode
  )
  $method.SetImplementationFlags(
    $method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig
  )
  $native = $type.CreateType()
  $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(4)
  try {{
    $arguments = [object[]]@($Token,18,$buffer,4,0)
    $ok = [bool]$native.GetMethod('GetTokenInformation').Invoke($null,$arguments)
    if (-not $ok) {{ throw 'get_token_information_failed' }}
    [Runtime.InteropServices.Marshal]::ReadInt32($buffer)
  }} finally {{
    [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer)
  }}
}}
function Get-CurrentProcessIdentity {{
  $process = [Diagnostics.Process]::GetCurrentProcess()
  $cim = CimCmdlets\\Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
  $path = [IO.Path]::GetFullPath([string]$process.MainModule.FileName)
  [ordered]@{{
    pid=[int]$process.Id
    ppid=[int]$cim.ParentProcessId
    session_id=[int]$process.SessionId
    creation_filetime=[int64]$process.StartTime.ToFileTimeUtc()
    path=$path
    path_sha256=Get-Sha256 $path
  }}
}}
function Get-DistributionTreeIdentity([string]$Root,[ValidateSet('python','git')][string]$Kind) {{
  $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd([char]92,[char]47)
  if (-not [IO.Directory]::Exists($rootPath)) {{ throw "distribution_root_missing:$Kind" }}
  $rootItem = Get-Item -LiteralPath $rootPath -Force
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
    throw "distribution_root_reparse_rejected:$Kind"
  }}
  $files = [Collections.Generic.List[string]]::new()
  $directories = [Collections.Generic.Queue[string]]::new()
  if ($Kind -eq 'git') {{
    $directories.Enqueue($rootPath)
  }} else {{
    foreach ($entry in [IO.Directory]::EnumerateFiles($rootPath,'*',[IO.SearchOption]::TopDirectoryOnly)) {{
      $name = [IO.Path]::GetFileName($entry)
      if ($name -like '*.exe' -or $name -like '*.dll' -or $name -like 'python*.zip') {{
        $files.Add([IO.Path]::GetFullPath($entry))
      }}
    }}
    foreach ($leaf in @('DLLs','Lib')) {{
      $candidate = Join-Path $rootPath $leaf
      if ([IO.Directory]::Exists($candidate)) {{ $directories.Enqueue($candidate) }}
    }}
  }}
  while ($directories.Count -gt 0) {{
    $directory = $directories.Dequeue()
    $relativeDirectory = if ($directory.Length -eq $rootPath.Length) {{ '' }} else {{
      $directory.Substring($rootPath.Length + 1).Replace('\','/')
    }}
    if ($Kind -eq 'python' -and (
      $relativeDirectory -ceq 'Lib/site-packages' -or
      $relativeDirectory.StartsWith('Lib/site-packages/',[StringComparison]::Ordinal) -or
      $relativeDirectory.Split('/') -contains '__pycache__'
    )) {{ continue }}
    $attributes = [IO.File]::GetAttributes($directory)
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
    throw "distribution_directory_reparse_rejected:${{Kind}}:$relativeDirectory"
    }}
    foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($directory)) {{
      $entryAttributes = [IO.File]::GetAttributes($entry)
      if (($entryAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
        throw "distribution_entry_reparse_rejected:${{Kind}}:$entry"
      }}
      if (($entryAttributes -band [IO.FileAttributes]::Directory) -ne 0) {{
        $directories.Enqueue($entry)
      }} else {{
        $extension = [IO.Path]::GetExtension($entry)
        if ($Kind -eq 'python' -and $extension -in @('.pyc','.pyo')) {{ continue }}
        $files.Add([IO.Path]::GetFullPath($entry))
      }}
    }}
  }}
  $relative = [string[]]@($files | ForEach-Object {{
    $_.Substring($rootPath.Length + 1).Replace('\','/')
  }})
  [Array]::Sort($relative,[StringComparer]::Ordinal)
  $hasher = [Security.Cryptography.SHA256]::Create()
  try {{
    foreach ($item in $relative) {{
      $absolute = Join-Path $rootPath ($item.Replace('/',[IO.Path]::DirectorySeparatorChar))
      $size = [IO.FileInfo]::new($absolute).Length
      $record = "$item`0$size`0$(Get-Sha256 $absolute)`0"
      $recordBytes = [Text.UTF8Encoding]::new($false).GetBytes($record)
      [void]$hasher.TransformBlock($recordBytes,0,$recordBytes.Length,$recordBytes,0)
    }}
    [void]$hasher.TransformFinalBlock([byte[]]::new(0),0,0)
    $digest = ([BitConverter]::ToString($hasher.Hash)).Replace('-','').ToLowerInvariant()
  }} finally {{ $hasher.Dispose() }}
  [ordered]@{{sha256=$digest;file_count=$relative.Count}}
}}
$outerPath = $PSCommandPath
$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$trustedCheckpointExpected = $ExpectedTrustedCheckpointSha256.ToLowerInvariant()
if ($trustedCheckpointExpected -ne $PinnedTrustedCheckpointSha256) {{ throw 'trusted_checkpoint_out_of_band_sha256_mismatch' }}
# R7S1_CANONICAL_POWERSHELL_ENTRY_OUTER
Assert-CanonicalPowerShellEntry $PinnedPowerShellPath $PinnedPowerShellSha256 $PinnedPowerShellBytes $outerPath $outerExpected $trustedCheckpointExpected $OutputDirectory
# R7S1_GIT_CONFIG_FENCE_OUTER_PREWRITE
Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath $PinnedGitRepositoryConfigSha256 $PinnedGitRepositoryConfigBytes
# R7S1_GIT_ATTRIBUTES_FENCE_OUTER_PREWRITE
Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath $PinnedGitRepositoryAttributesSha256 $PinnedGitRepositoryAttributesBytes $PinnedGitTopAttributesPath $PinnedGitInfoAttributesPath
# R7S1_CLIENT_CONFIG_FENCE_OUTER_PREWRITE
Assert-ClientConfigurationPins
$hostIdentity = Get-CurrentProcessIdentity
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$groupSids = @($identity.Groups | ForEach-Object {{ $_.Value }})
$integrity = if ($groupSids -contains 'S-1-16-16384') {{ 'System' }} elseif (
  $groupSids -contains 'S-1-16-12288'
) {{ 'High' }} else {{ 'Other' }}
$elevationValue = Get-TokenElevationTypeValue $identity.Token
$elevationType = if ($elevationValue -eq 2) {{ 'Full' }} else {{ "NotFull:$elevationValue" }}
if (-not ($administrator -and $integrity -in @('High','System') -and $elevationType -eq 'Full')) {{
  [ordered]@{{
    decision='administrator_token_required'
    administrator=$administrator
    integrity=$integrity
    token_elevation_type=$elevationType
    token_elevation_type_value=$elevationValue
  }} | ConvertTo-Json -Compress
  exit 3
}}
if ([IO.Path]::GetFullPath($hostIdentity.path) -ne [IO.Path]::GetFullPath($PinnedPowerShellPath) -or
    $hostIdentity.path_sha256 -ne $PinnedPowerShellSha256 -or
    [IO.FileInfo]::new($hostIdentity.path).Length -ne $PinnedPowerShellBytes) {{
  throw 'powershell_host_toolchain_mismatch_at_outer'
}}
$pythonDistribution = Get-DistributionTreeIdentity $PinnedPythonDistributionRoot 'python'
if ($pythonDistribution.sha256 -ne $PinnedPythonDistributionSha256 -or
    $pythonDistribution.file_count -ne $PinnedPythonDistributionFileCount) {{
  throw 'python_distribution_tree_mismatch_at_outer'
}}
$gitDistribution = Get-DistributionTreeIdentity $PinnedGitDistributionRoot 'git'
if ($gitDistribution.sha256 -ne $PinnedGitDistributionSha256 -or
    $gitDistribution.file_count -ne $PinnedGitDistributionFileCount) {{
  throw 'git_distribution_tree_mismatch_at_outer'
}}
$toolchainObservation = [ordered]@{{
  python_distribution=[ordered]@{{
    distribution_tree_sha256=$pythonDistribution.sha256
    file_count=[int]$pythonDistribution.file_count
    tree_encoding={_ps_literal(PYTHON_TREE_ENCODING)}
  }}
  git_distribution=[ordered]@{{
    distribution_tree_sha256=$gitDistribution.sha256
    file_count=[int]$gitDistribution.file_count
    tree_encoding={_ps_literal(GIT_TREE_ENCODING)}
  }}
}}
$toolchainObservationBase64 = [Convert]::ToBase64String(
  [Text.UTF8Encoding]::new($false).GetBytes(
    ($toolchainObservation | ConvertTo-Json -Depth 8 -Compress)
  )
)
$outerObserved = Get-Sha256 $outerPath
if ($outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch' }}
$bridgePath = Join-Path $PSScriptRoot 'invoke-x1-phase-b2-r7s1-bridge.ps1'
if (-not (Test-Path -LiteralPath $bridgePath -PathType Leaf)) {{ throw 'bridge_missing' }}
$bridgeObserved = Get-Sha256 $bridgePath
if ($bridgeObserved -ne $ExpectedBridgeSha256) {{ throw 'bridge_sha256_mismatch' }}
if (Test-Path -LiteralPath $OutputDirectory) {{ throw 'output_directory_exists' }}
$nonceBytes = [byte[]]::new(32)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {{ $rng.GetBytes($nonceBytes) }} finally {{ $rng.Dispose() }}
$invocationNonce = ([BitConverter]::ToString($nonceBytes)).Replace('-','').ToLowerInvariant()
$currentCim = CimCmdlets\\Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
$codexCim = $null
$ancestor = $currentCim
for ($depth=0; $depth -lt 16 -and $null -ne $ancestor; $depth++) {{
  if ([string]$ancestor.Name -ieq 'codex.exe') {{ $codexCim=$ancestor; break }}
  if ([int]$ancestor.ParentProcessId -le 0) {{ break }}
  $ancestor = CimCmdlets\\Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$ancestor.ParentProcessId)" -ErrorAction SilentlyContinue
}}
if ($null -eq $codexCim) {{ throw 'codex_ancestor_not_found' }}
$codexProcess = [Diagnostics.Process]::GetProcessById([int]$codexCim.ProcessId)
$codexPath = [IO.Path]::GetFullPath([string]$codexProcess.MainModule.FileName)
$commandLineBytes = [Text.UTF8Encoding]::new($false).GetBytes([string]$codexCim.CommandLine)
$commandLineHasher = [Security.Cryptography.SHA256]::Create()
try {{
  $commandLineSha256 = ([BitConverter]::ToString(
    $commandLineHasher.ComputeHash($commandLineBytes)
  )).Replace('-','').ToLowerInvariant()
}} finally {{ $commandLineHasher.Dispose() }}
$tokenEvidence = [ordered]@{{
  captured_at=[DateTime]::UtcNow.ToString('o')
  administrator=$administrator
  integrity=$integrity
  token_elevation_type=$elevationType
  token_elevation_type_value=$elevationValue
  invocation_nonce=$invocationNonce
  execution_powershell=[ordered]@{{
    pid=$hostIdentity.pid
    ppid=$hostIdentity.ppid
    session_id=$hostIdentity.session_id
    creation_filetime=$hostIdentity.creation_filetime
    path=$hostIdentity.path
    path_sha256=$hostIdentity.path_sha256
  }}
  codex=[ordered]@{{
    pid=[int]$codexCim.ProcessId
    ppid=[int]$codexCim.ParentProcessId
    session_id=[int]$codexCim.SessionId
    creation_filetime=[int64]$codexProcess.StartTime.ToFileTimeUtc()
    path=$codexPath
    path_sha256=Get-Sha256 $codexPath
    command_line_sha256=$commandLineSha256
  }}
}}
$tokenEvidenceBase64 = [Convert]::ToBase64String(
  [Text.UTF8Encoding]::new($false).GetBytes(
    ($tokenEvidence | ConvertTo-Json -Depth 12 -Compress)
  )
)
# R7S1_PATH_FENCE_OUTER_PREWRITE
Assert-BoundRunLocation $PSScriptRoot $PinnedStagingPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $true 'staging'
Assert-BoundRunLocation $OutputDirectory $PinnedOutputPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'output'
Assert-BoundRunLocation $PinnedEmergencySealPath $PinnedEmergencySealPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'emergency_seal'

$reservation = Join-Path $PSScriptRoot 'r7s1-outer-invocation-reservation.json'
$reservationValue = [ordered]@{{
  schema='s8-v4-x1-phase-b2-r7s1-outer-reservation/v1'
  created_at=[DateTime]::UtcNow.ToString('o')
  invocation_nonce=$invocationNonce
  pid=$hostIdentity.pid
  ppid=$hostIdentity.ppid
  session_id=$hostIdentity.session_id
  creation_filetime=$hostIdentity.creation_filetime
  process_path=$hostIdentity.path
  process_path_sha256=$hostIdentity.path_sha256
  run_id=$PinnedRunId
  mode='restore-only'
  output_directory=[IO.Path]::GetFullPath($OutputDirectory)
}}
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($reservationValue | ConvertTo-Json -Depth 8 -Compress) + [Environment]::NewLine)
$stream = [IO.File]::Open($reservation,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try {{ $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }} finally {{ $stream.Dispose() }}

# R7S1_PATH_FENCE_OUTER_FINAL
Assert-BoundRunLocation $PSScriptRoot $PinnedStagingPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $true 'staging'
Assert-BoundRunLocation $OutputDirectory $PinnedOutputPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'output'
Assert-BoundRunLocation $PinnedEmergencySealPath $PinnedEmergencySealPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'emergency_seal'
# R7S1_GIT_CONFIG_FENCE_OUTER_FINAL
Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath $PinnedGitRepositoryConfigSha256 $PinnedGitRepositoryConfigBytes
# R7S1_GIT_ATTRIBUTES_FENCE_OUTER_FINAL
Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath $PinnedGitRepositoryAttributesSha256 $PinnedGitRepositoryAttributesBytes $PinnedGitTopAttributesPath $PinnedGitInfoAttributesPath
# R7S1_CLIENT_CONFIG_FENCE_OUTER_FINAL
Assert-ClientConfigurationPins

# Re-read both executable leaves after the reservation write.  These are the
# values handed to the bridge and are the final operations before invocation.
  $outerObserved = Get-Sha256 $outerPath
if ($outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch_immediate' }}
  $bridgeObserved = Get-Sha256 $bridgePath
if ($bridgeObserved -ne $ExpectedBridgeSha256) {{ throw 'bridge_sha256_mismatch_immediate' }}

# R7S1_BRIDGE_INVOKE_EXACTLY_ONCE
& $bridgePath -ExpectedOuterSha256 $outerExpected -ObservedOuterSha256 $outerObserved -ExpectedBridgeSha256FromOuter $ExpectedBridgeSha256 -ObservedBridgeSha256 $bridgeObserved -ExpectedTrustedCheckpointSha256FromOuter $trustedCheckpointExpected -InvocationNonce $invocationNonce -TokenEvidenceBase64 $tokenEvidenceBase64 -ToolchainObservationBase64 $toolchainObservationBase64 -OuterLauncherPath $outerPath -OutputDirectory $OutputDirectory
exit $LASTEXITCODE
"""


def render_bridge(
    *,
    manifest_sha256: str,
    manifest: Mapping[str, Any],
    runtime: Mapping[str, Mapping[str, Any]],
    project_root: Path,
    source_identity: Mapping[str, Any],
    python_path: Path,
) -> str:
    revision = str(manifest["canonical_revision"])
    tree = str(manifest["canonical_tree"])
    repository = str(project_root.resolve().parent)
    resolved_project = str(project_root.resolve())
    branch = str(source_identity["branch"])
    untracked = int(manifest["repository"]["preserved_untracked_count"])  # type: ignore[index]
    untracked_digest = str(manifest["repository"]["untracked_path_set_sha256"])  # type: ignore[index]
    run_id = str(manifest["bundle_id"])
    external = manifest["external_terminal_fencing"]
    assert isinstance(external, Mapping)
    trusted_checkpoint = external["trusted_checkpoint"]
    successor_binding = external["successor_binding"]
    assert isinstance(trusted_checkpoint, Mapping)
    assert isinstance(successor_binding, Mapping)
    trusted_checkpoint_sha256 = str(trusted_checkpoint["sha256"])
    parent_map_digest = str(successor_binding["parent_map_sha256"])
    staging_path = Path(str(successor_binding["staging_path"])).resolve(strict=False)
    output_path = Path(str(successor_binding["output_path"])).resolve(strict=False)
    emergency_path = Path(str(successor_binding["emergency_seal_path"])).resolve(strict=False)
    location_identities = [
        path_filesystem_identity(path) for path in (staging_path, output_path, emergency_path)
    ]
    if (
        len(
            {
                (
                    str(identity["volume_root"]).casefold(),
                    str(identity["volume_serial"]).casefold(),
                    str(identity["filesystem"]).casefold(),
                )
                for identity in location_identities
            }
        )
        != 1
    ):
        raise BundleBuildError("run_locations_same_filesystem_required")
    location_identity = location_identities[0]
    location_functions = _render_location_fence_functions()
    git_config_guard_function = _render_git_config_guard_function()
    client_config_guard_function = _render_client_config_guard_function()
    entry_guard_function = _render_powershell_entry_guard_function()
    toolchain = manifest["toolchain"]
    assert isinstance(toolchain, Mapping)
    powershell_pin = toolchain["powershell"]
    python_pin = toolchain["python"]
    python_distribution = toolchain["python_distribution"]
    git_distribution = toolchain["git_distribution"]
    git_repository_config = toolchain["git_repository_config"]
    git_repository_attributes = toolchain["git_repository_attributes"]
    docker_client_config = toolchain["docker_client_config"]
    kubernetes_client_config = toolchain["kubernetes_client_config"]
    assert isinstance(powershell_pin, Mapping)
    assert isinstance(python_pin, Mapping)
    assert isinstance(python_distribution, Mapping)
    assert isinstance(git_distribution, Mapping)
    assert isinstance(git_repository_config, Mapping)
    assert isinstance(git_repository_attributes, Mapping)
    assert isinstance(docker_client_config, Mapping)
    assert isinstance(kubernetes_client_config, Mapping)
    docker_context_metadata = docker_client_config["context_metadata"]
    assert isinstance(docker_context_metadata, Mapping)
    git_attributes_path = Path(str(git_repository_attributes["path"])).resolve()
    git_top_attributes_path = git_attributes_path.parent.parent / ".gitattributes"
    git_info_attributes_path = git_attributes_path.parent.parent / ".git" / "info" / "attributes"
    component_variables = {
        "builder": "Builder",
        "core": "Core",
        "process": "Process",
        "runner": "Runner",
        "validator": "Validator",
        "docker_compose": "DockerCompose",
    }
    declarations: list[str] = []
    guards: list[str] = []
    chain_entries: list[str] = []
    for name, variable in component_variables.items():
        declarations.extend(
            (
                f"${variable}Path = {_ps_literal(str(runtime[name]['path']))}",
                f"$Expected{variable}Sha256 = '{runtime[name]['sha256']}'",
            )
        )
        guards.append(
            f"if ((Get-Sha256 ${variable}Path) -ne $Expected{variable}Sha256) "
            f"{{ throw '{name}_sha256_mismatch' }}"
        )
        chain_entries.append(f"{name}=Get-Sha256 ${variable}Path")
    parent_roles = ",".join(_ps_literal(role) for role in REQUIRED_PARENT_ROLES)
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ObservedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedBridgeSha256FromOuter,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ObservedBridgeSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedTrustedCheckpointSha256FromOuter,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{{64}}$')][string]$InvocationNonce,
  [Parameter(Mandatory = $true)][string]$TokenEvidenceBase64,
  [Parameter(Mandatory = $true)][string]$ToolchainObservationBase64,
  [Parameter(Mandatory = $true)][string]$OuterLauncherPath,
  [Parameter(Mandatory = $true)][string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedManifestSha256 = '{manifest_sha256}'
$PinnedRevision = '{revision}'
$PinnedTree = '{tree}'
$PinnedRunId = {_ps_literal(run_id)}
$RepositoryRoot = {_ps_literal(repository)}
$ProjectRoot = {_ps_literal(resolved_project)}
$ExpectedBranch = {_ps_literal(branch)}
$ExpectedUntrackedCount = {untracked}
$ExpectedUntrackedDigestSha256 = '{untracked_digest}'
$PinnedTrustedCheckpointSha256 = '{trusted_checkpoint_sha256}'
$PinnedParentMapSha256 = '{parent_map_digest}'
$PinnedPowerShellPath = {_ps_literal(str(powershell_pin["path"]))}
$PinnedPowerShellSha256 = '{powershell_pin["sha256"]}'
$PinnedPowerShellBytes = {int(powershell_pin["bytes"])}
$PinnedPythonSha256 = '{python_pin["sha256"]}'
$PinnedPythonBytes = {int(python_pin["bytes"])}
$PinnedPythonDistributionRoot = {_ps_literal(str(python_distribution["base_prefix"]))}
$PinnedPythonDistributionSha256 = '{python_distribution["distribution_tree_sha256"]}'
$PinnedPythonDistributionFileCount = {int(python_distribution["file_count"])}
$PinnedPythonTreeEncoding = {_ps_literal(str(python_distribution["tree_encoding"]))}
$PinnedGitDistributionRoot = {_ps_literal(str(git_distribution["root"]))}
$PinnedGitDistributionSha256 = '{git_distribution["distribution_tree_sha256"]}'
$PinnedGitDistributionFileCount = {int(git_distribution["file_count"])}
$PinnedGitTreeEncoding = {_ps_literal(str(git_distribution["tree_encoding"]))}
$PinnedGitRepositoryConfigPath = {_ps_literal(str(git_repository_config["path"]))}
$PinnedGitRepositoryConfigSha256 = '{git_repository_config["sha256"]}'
$PinnedGitRepositoryConfigBytes = {int(git_repository_config["bytes"])}
$PinnedGitRepositoryAttributesPath = {_ps_literal(str(git_repository_attributes["path"]))}
$PinnedGitRepositoryAttributesSha256 = '{git_repository_attributes["sha256"]}'
$PinnedGitRepositoryAttributesBytes = {int(git_repository_attributes["bytes"])}
$PinnedGitTopAttributesPath = {_ps_literal(str(git_top_attributes_path.resolve()))}
$PinnedGitInfoAttributesPath = {_ps_literal(str(git_info_attributes_path.resolve()))}
$PinnedDockerClientConfigPath = {_ps_literal(str(docker_client_config["path"]))}
$PinnedDockerClientConfigSha256 = '{docker_client_config["sha256"]}'
$PinnedDockerClientConfigBytes = {int(docker_client_config["bytes"])}
$PinnedDockerContextMetadataPath = {_ps_literal(str(docker_context_metadata["path"]))}
$PinnedDockerContextMetadataSha256 = '{docker_context_metadata["sha256"]}'
$PinnedDockerContextMetadataBytes = {int(docker_context_metadata["bytes"])}
$PinnedDockerContextTlsPath = {_ps_literal(str(EXPECTED_DOCKER_CONTEXT_TLS_PATH.resolve()))}
$PinnedKubernetesClientConfigPath = {_ps_literal(str(kubernetes_client_config["path"]))}
$PinnedKubernetesClientConfigSha256 = '{kubernetes_client_config["sha256"]}'
$PinnedKubernetesClientConfigBytes = {int(kubernetes_client_config["bytes"])}
$PinnedStagingPath = {_ps_literal(str(staging_path))}
$PinnedOutputPath = {_ps_literal(str(output_path))}
$PinnedEmergencySealPath = {_ps_literal(str(emergency_path))}
$PinnedLocationVolumeRoot = {_ps_literal(str(location_identity["volume_root"]))}
$PinnedLocationVolumeSerial = {_ps_literal(str(location_identity["volume_serial"]))}
$PinnedLocationFilesystem = {_ps_literal(str(location_identity["filesystem"]))}
$ExpectedParentRoles = @({parent_roles})
$ManifestPath = Join-Path $PSScriptRoot 'phase-b2-r7s1-work-order.json'
$PythonPath = {_ps_literal(str(python_path.resolve()))}
{chr(10).join(declarations)}

function Get-Sha256([string]$Path) {{
  $hashStream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
  $hasher = [Security.Cryptography.SHA256]::Create()
  try {{
    ([BitConverter]::ToString($hasher.ComputeHash($hashStream))).Replace('-','').ToLowerInvariant()
  }} finally {{ $hasher.Dispose(); $hashStream.Dispose() }}
}}
{entry_guard_function}
{location_functions}

{git_config_guard_function}

{client_config_guard_function}
function Get-DistributionTreeIdentity([string]$Root,[ValidateSet('python','git')][string]$Kind) {{
  $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd([char]92,[char]47)
  if (-not [IO.Directory]::Exists($rootPath)) {{ throw "distribution_root_missing:$Kind" }}
  $rootItem = Get-Item -LiteralPath $rootPath -Force
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
    throw "distribution_root_reparse_rejected:$Kind"
  }}
  $files = [Collections.Generic.List[string]]::new()
  $directories = [Collections.Generic.Queue[string]]::new()
  if ($Kind -eq 'git') {{
    $directories.Enqueue($rootPath)
  }} else {{
    foreach ($entry in [IO.Directory]::EnumerateFiles($rootPath,'*',[IO.SearchOption]::TopDirectoryOnly)) {{
      $name = [IO.Path]::GetFileName($entry)
      if ($name -like '*.exe' -or $name -like '*.dll' -or $name -like 'python*.zip') {{
        $files.Add([IO.Path]::GetFullPath($entry))
      }}
    }}
    foreach ($leaf in @('DLLs','Lib')) {{
      $candidate = Join-Path $rootPath $leaf
      if ([IO.Directory]::Exists($candidate)) {{ $directories.Enqueue($candidate) }}
    }}
  }}
  while ($directories.Count -gt 0) {{
    $directory = $directories.Dequeue()
    $relativeDirectory = if ($directory.Length -eq $rootPath.Length) {{ '' }} else {{
      $directory.Substring($rootPath.Length + 1).Replace('\','/')
    }}
    if ($Kind -eq 'python' -and (
      $relativeDirectory -ceq 'Lib/site-packages' -or
      $relativeDirectory.StartsWith('Lib/site-packages/',[StringComparison]::Ordinal) -or
      $relativeDirectory.Split('/') -contains '__pycache__'
    )) {{ continue }}
    $attributes = [IO.File]::GetAttributes($directory)
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
      throw "distribution_directory_reparse_rejected:${{Kind}}:$relativeDirectory"
    }}
    foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($directory)) {{
      $entryAttributes = [IO.File]::GetAttributes($entry)
      if (($entryAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
        throw "distribution_entry_reparse_rejected:${{Kind}}:$entry"
      }}
      if (($entryAttributes -band [IO.FileAttributes]::Directory) -ne 0) {{
        $directories.Enqueue($entry)
      }} else {{
        $extension = [IO.Path]::GetExtension($entry)
        if ($Kind -eq 'python' -and $extension -in @('.pyc','.pyo')) {{ continue }}
        $files.Add([IO.Path]::GetFullPath($entry))
      }}
    }}
  }}
  $relative = [string[]]@($files | ForEach-Object {{
    $_.Substring($rootPath.Length + 1).Replace('\','/')
  }})
  [Array]::Sort($relative,[StringComparer]::Ordinal)
  $hasher = [Security.Cryptography.SHA256]::Create()
  try {{
    foreach ($item in $relative) {{
      $absolute = Join-Path $rootPath ($item.Replace('/',[IO.Path]::DirectorySeparatorChar))
      $size = [IO.FileInfo]::new($absolute).Length
      $record = "$item`0$size`0$(Get-Sha256 $absolute)`0"
      $recordBytes = [Text.UTF8Encoding]::new($false).GetBytes($record)
      [void]$hasher.TransformBlock($recordBytes,0,$recordBytes.Length,$recordBytes,0)
    }}
    [void]$hasher.TransformFinalBlock([byte[]]::new(0),0,0)
    $digest = ([BitConverter]::ToString($hasher.Hash)).Replace('-','').ToLowerInvariant()
  }} finally {{ $hasher.Dispose() }}
  [ordered]@{{sha256=$digest;file_count=$relative.Count}}
}}
function Write-CreateNewJson([string]$Path,[object]$Value) {{
  $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 20 -Compress) + [Environment]::NewLine)
  $stream = [IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
  try {{ $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }} finally {{ $stream.Dispose() }}
}}
function Get-CurrentProcessIdentity {{
  $process = [Diagnostics.Process]::GetCurrentProcess()
  $cim = CimCmdlets\\Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
  $path = [IO.Path]::GetFullPath([string]$process.MainModule.FileName)
  [ordered]@{{
    pid=[int]$process.Id
    ppid=[int]$cim.ParentProcessId
    session_id=[int]$process.SessionId
    creation_filetime=[int64]$process.StartTime.ToFileTimeUtc()
    process_path=$path
    process_path_sha256=Get-Sha256 $path
  }}
}}

$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$outerObserved = $ObservedOuterSha256.ToLowerInvariant()
$bridgeExpected = $ExpectedBridgeSha256FromOuter.ToLowerInvariant()
$bridgeObserved = $ObservedBridgeSha256.ToLowerInvariant()
$trustedCheckpointExpected = $ExpectedTrustedCheckpointSha256FromOuter.ToLowerInvariant()
if ($trustedCheckpointExpected -ne $PinnedTrustedCheckpointSha256) {{ throw 'trusted_checkpoint_out_of_band_sha256_mismatch_at_bridge' }}
if ($InvocationNonce -notmatch '^[0-9a-f]{{64}}$') {{ throw 'invocation_nonce_invalid_at_bridge' }}
# R7S1_CANONICAL_POWERSHELL_ENTRY_BRIDGE
Assert-CanonicalPowerShellEntry $PinnedPowerShellPath $PinnedPowerShellSha256 $PinnedPowerShellBytes $OuterLauncherPath $outerExpected $trustedCheckpointExpected $OutputDirectory
# R7S1_GIT_CONFIG_FENCE_BRIDGE_PREWRITE
Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath $PinnedGitRepositoryConfigSha256 $PinnedGitRepositoryConfigBytes
# R7S1_GIT_ATTRIBUTES_FENCE_BRIDGE_PREWRITE
Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath $PinnedGitRepositoryAttributesSha256 $PinnedGitRepositoryAttributesBytes $PinnedGitTopAttributesPath $PinnedGitInfoAttributesPath
# R7S1_CLIENT_CONFIG_FENCE_BRIDGE_PREWRITE
Assert-ClientConfigurationPins
$hostIdentity = Get-CurrentProcessIdentity
if ([IO.Path]::GetFullPath($hostIdentity.process_path) -ne [IO.Path]::GetFullPath($PinnedPowerShellPath) -or
    $hostIdentity.process_path_sha256 -ne $PinnedPowerShellSha256 -or
    [IO.FileInfo]::new($hostIdentity.process_path).Length -ne $PinnedPowerShellBytes) {{
  throw 'powershell_host_toolchain_mismatch_at_bridge'
}}
if ((Get-Sha256 $PythonPath) -ne $PinnedPythonSha256 -or
    [IO.FileInfo]::new($PythonPath).Length -ne $PinnedPythonBytes) {{
  throw 'python_host_toolchain_mismatch_at_bridge'
}}
if ((Get-Sha256 $OuterLauncherPath) -ne $outerExpected -or $outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch_at_bridge' }}
if ((Get-Sha256 $PSCommandPath) -ne $bridgeExpected -or $bridgeObserved -ne $bridgeExpected) {{ throw 'bridge_sha256_mismatch' }}
if ((Get-Sha256 $ManifestPath) -ne $ExpectedManifestSha256) {{ throw 'manifest_sha256_mismatch' }}
{chr(10).join(guards)}
if (Test-Path -LiteralPath $OutputDirectory) {{ throw 'output_directory_exists' }}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.execution_mode -ne 'restore-only') {{ throw 'manifest_execution_mode_mismatch' }}
if ([string]$manifest.bundle_id -ne $PinnedRunId) {{ throw 'manifest_run_id_mismatch' }}
if ([IO.Path]::GetFullPath([string]$manifest.bundle.path) -ne [IO.Path]::GetFullPath($PSScriptRoot)) {{ throw 'manifest_bundle_path_mismatch' }}
if ([string]$manifest.output.path -ne [IO.Path]::GetFullPath($OutputDirectory)) {{ throw 'manifest_output_path_mismatch' }}

$manifestParents = @($manifest.parent_checkpoints)
if ($manifestParents.Count -ne $ExpectedParentRoles.Count) {{ throw 'parent_checkpoint_count_mismatch' }}
$parentShaChain = [ordered]@{{}}
foreach ($role in $ExpectedParentRoles) {{
  $matches = @($manifestParents | Where-Object {{ [string]$_.role -ceq $role }})
  if ($matches.Count -ne 1) {{ throw "parent_checkpoint_role_mismatch:$role" }}
  $parent = $matches[0]
  $parentPath = [IO.Path]::GetFullPath([string]$parent.path)
  if (-not (Test-Path -LiteralPath $parentPath -PathType Leaf)) {{ throw "parent_checkpoint_missing:$role" }}
  if ($parent.immutable -ne $true -or $parent.must_not_execute -ne $true) {{ throw "parent_checkpoint_mutability_mismatch:$role" }}
  $parentSha = (Get-Sha256 $parentPath)
  if ($parentSha -ne [string]$parent.sha256) {{ throw "parent_checkpoint_sha256_mismatch:$role" }}
  $bundlePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([char]92,[char]47) + [IO.Path]::DirectorySeparatorChar
  if ($parentPath.StartsWith($bundlePrefix,[StringComparison]::OrdinalIgnoreCase)) {{ throw "parent_checkpoint_inside_bundle:$role" }}
  $parentShaChain[$role] = $parentSha
}}

$tokenEvidence = [Text.UTF8Encoding]::new($false).GetString(
  [Convert]::FromBase64String($TokenEvidenceBase64)
) | ConvertFrom-Json -ErrorAction Stop
if ([string]$tokenEvidence.invocation_nonce -cne $InvocationNonce -or
    $tokenEvidence.administrator -ne $true -or
    [string]$tokenEvidence.integrity -notin @('High','System') -or
    [string]$tokenEvidence.token_elevation_type -cne 'Full' -or
    [int]$tokenEvidence.token_elevation_type_value -ne 2) {{
  throw 'launcher_token_evidence_or_nonce_mismatch_at_bridge'
}}
$claimedHost = $tokenEvidence.execution_powershell
if ([int]$claimedHost.pid -ne $hostIdentity.pid -or
    [int]$claimedHost.ppid -ne $hostIdentity.ppid -or
    [int]$claimedHost.session_id -ne $hostIdentity.session_id -or
    [int64]$claimedHost.creation_filetime -ne $hostIdentity.creation_filetime -or
    [IO.Path]::GetFullPath([string]$claimedHost.path) -ne $hostIdentity.process_path -or
    [string]$claimedHost.path_sha256 -cne $hostIdentity.process_path_sha256) {{
  throw 'launcher_powershell_identity_mismatch_at_bridge'
}}
$toolchainObservation = [Text.UTF8Encoding]::new($false).GetString(
  [Convert]::FromBase64String($ToolchainObservationBase64)
) | ConvertFrom-Json -ErrorAction Stop
$pythonObservation = $toolchainObservation.python_distribution
$gitObservation = $toolchainObservation.git_distribution
if ([string]$pythonObservation.distribution_tree_sha256 -cne $PinnedPythonDistributionSha256 -or
    [int]$pythonObservation.file_count -ne $PinnedPythonDistributionFileCount -or
    [string]$pythonObservation.tree_encoding -cne $PinnedPythonTreeEncoding -or
    [string]$gitObservation.distribution_tree_sha256 -cne $PinnedGitDistributionSha256 -or
    [int]$gitObservation.file_count -ne $PinnedGitDistributionFileCount -or
    [string]$gitObservation.tree_encoding -cne $PinnedGitTreeEncoding) {{
  throw 'toolchain_distribution_observation_mismatch_at_bridge'
}}

# R7S1_PATH_FENCE_BRIDGE_PREWRITE
Assert-BoundRunLocation $PSScriptRoot $PinnedStagingPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $true 'staging'
Assert-BoundRunLocation $OutputDirectory $PinnedOutputPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'output'
Assert-BoundRunLocation $PinnedEmergencySealPath $PinnedEmergencySealPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'emergency_seal'

$bridgeReservation = Join-Path $PSScriptRoot 'r7s1-bridge-invocation-reservation.json'
$outerReservation = Join-Path $PSScriptRoot 'r7s1-outer-invocation-reservation.json'
$outerReservationSha256 = Get-Sha256 $outerReservation
Write-CreateNewJson $bridgeReservation ([ordered]@{{
  schema='s8-v4-x1-phase-b2-r7s1-bridge-reservation/v1'
  created_at=[DateTime]::UtcNow.ToString('o')
  invocation_nonce=$InvocationNonce
  pid=$hostIdentity.pid
  ppid=$hostIdentity.ppid
  session_id=$hostIdentity.session_id
  creation_filetime=$hostIdentity.creation_filetime
  process_path=$hostIdentity.process_path
  process_path_sha256=$hostIdentity.process_path_sha256
  run_id=$PinnedRunId
  mode='restore-only'
  output_directory=[IO.Path]::GetFullPath($OutputDirectory)
  outer_reservation_sha256=$outerReservationSha256
}})
$launcherEvidence = [ordered]@{{
  schema='s8-v4-x1-phase-b2-r7s1-launcher-evidence/v1'
  token_evidence=$tokenEvidence
  sha_chain=$null
  toolchain_observation=$toolchainObservation
  git=[ordered]@{{
    measurement='deferred_to_contained_runner'
    branch=$ExpectedBranch
    revision=$PinnedRevision
    origin_revision=$PinnedRevision
    remote_revision=$PinnedRevision
    tree=$PinnedTree
    tracked=0
    untracked=$ExpectedUntrackedCount
    untracked_path_set_sha256=$ExpectedUntrackedDigestSha256
  }}
  run_id=$PinnedRunId
  mode='restore-only'
  invocation_counts=[ordered]@{{outer=1;bridge=1;runner=1;automatic_retry=0}}
}}
$shaChain = [ordered]@{{
  outer=Get-Sha256 $OuterLauncherPath
  bridge=Get-Sha256 $PSCommandPath
  manifest=Get-Sha256 $ManifestPath
  trusted_checkpoint=$PinnedTrustedCheckpointSha256
  parent_map=$PinnedParentMapSha256
  python_distribution=$PinnedPythonDistributionSha256
  git_distribution=$PinnedGitDistributionSha256
  {";".join(chain_entries)}
}}
foreach ($role in $ExpectedParentRoles) {{ $shaChain[$role] = $parentShaChain[$role] }}
$launcherEvidence.sha_chain = $shaChain
$launcherBase64 = [Convert]::ToBase64String([Text.UTF8Encoding]::new($false).GetBytes(($launcherEvidence | ConvertTo-Json -Depth 20 -Compress)))

# Recompute both executable distribution identities after the reservation and
# launcher serialization, immediately before the only child invocation.  This
# closes the outer-to-bridge mutation window for Python stdlib/DLL/zip files and
# Git helpers/TLS dependencies; the serialized observation must match too.
$pythonDistributionFinal = Get-DistributionTreeIdentity $PinnedPythonDistributionRoot 'python'
if ($pythonDistributionFinal.sha256 -ne $PinnedPythonDistributionSha256 -or
    $pythonDistributionFinal.file_count -ne $PinnedPythonDistributionFileCount -or
    [string]$pythonObservation.distribution_tree_sha256 -cne $pythonDistributionFinal.sha256 -or
    [int]$pythonObservation.file_count -ne $pythonDistributionFinal.file_count) {{
  throw 'python_distribution_tree_mismatch_immediate_before_runner'
}}
$gitDistributionFinal = Get-DistributionTreeIdentity $PinnedGitDistributionRoot 'git'
if ($gitDistributionFinal.sha256 -ne $PinnedGitDistributionSha256 -or
    $gitDistributionFinal.file_count -ne $PinnedGitDistributionFileCount -or
    [string]$gitObservation.distribution_tree_sha256 -cne $gitDistributionFinal.sha256 -or
    [int]$gitObservation.file_count -ne $gitDistributionFinal.file_count) {{
  throw 'git_distribution_tree_mismatch_immediate_before_runner'
}}

# R7S1_PATH_FENCE_BRIDGE_FINAL
Assert-BoundRunLocation $PSScriptRoot $PinnedStagingPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $true 'staging'
Assert-BoundRunLocation $OutputDirectory $PinnedOutputPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'output'
Assert-BoundRunLocation $PinnedEmergencySealPath $PinnedEmergencySealPath $PinnedLocationVolumeRoot $PinnedLocationVolumeSerial $PinnedLocationFilesystem $false 'emergency_seal'
# R7S1_GIT_CONFIG_FENCE_BRIDGE_FINAL
Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath $PinnedGitRepositoryConfigSha256 $PinnedGitRepositoryConfigBytes
# R7S1_GIT_ATTRIBUTES_FENCE_BRIDGE_FINAL
Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath $PinnedGitRepositoryAttributesSha256 $PinnedGitRepositoryAttributesBytes $PinnedGitTopAttributesPath $PinnedGitInfoAttributesPath
# R7S1_CLIENT_CONFIG_FENCE_BRIDGE_FINAL
Assert-ClientConfigurationPins

# The executable Python leaves are imported before the runner can perform its
# own manifest validation, so pin them again at the invocation boundary.  The
# outer and bridge are re-read here as the final launcher-chain observation.
if ((Get-Sha256 $OuterLauncherPath) -ne $outerExpected) {{ throw 'outer_sha256_mismatch_immediate_before_runner' }}
if ((Get-Sha256 $PSCommandPath) -ne $bridgeExpected) {{ throw 'bridge_sha256_mismatch_immediate_before_runner' }}
if ((Get-Sha256 $RunnerPath) -ne $ExpectedRunnerSha256) {{ throw 'runner_sha256_mismatch_immediate' }}
if ((Get-Sha256 $CorePath) -ne $ExpectedCoreSha256) {{ throw 'core_sha256_mismatch_immediate' }}
if ((Get-Sha256 $ProcessPath) -ne $ExpectedProcessSha256) {{ throw 'process_sha256_mismatch_immediate' }}
# R7S1_RUNNER_INVOKE_EXACTLY_ONCE
& $PythonPath -I -S -B $RunnerPath --manifest $ManifestPath --output-directory $OutputDirectory --expected-revision $PinnedRevision --expected-trusted-checkpoint-sha256 $trustedCheckpointExpected --launcher-evidence-base64 $launcherBase64 --repository-root $RepositoryRoot --mode restore-only
exit $LASTEXITCODE
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one append-only Phase B2 r7s1 restore-only bundle."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--staging-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--parent", action="append", default=[], metavar="ROLE=PATH")
    parser.add_argument("--runtime-state-pins", type=Path, required=True)
    parser.add_argument("--external-terminal-fencing", type=Path, required=True)
    parser.add_argument("--successor-nonce", required=True)
    parser.add_argument("--expected-trusted-checkpoint-sha256", required=True)
    parser.add_argument("--toolchain-pins", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--branch", default="codex/distributed-scale-validation-plan")
    parser.add_argument("--expected-untracked", type=int, required=True)
    parser.add_argument("--expected-untracked-digest", required=True)
    parser.add_argument("--python", type=Path, default=Path(r"F:\evm_w7_torch\python.exe"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{15,160}", args.run_id)
        or "r7s1" not in args.run_id.lower()
    ):
        raise BundleBuildError("run_id_invalid_or_not_r7s1")
    try:
        canonical_attempt_id = str(uuid.UUID(args.attempt_id))
    except (ValueError, AttributeError) as exc:
        raise BundleBuildError("attempt_id_canonical_uuid_required") from exc
    if (
        canonical_attempt_id != args.attempt_id
        or args.attempt_id == args.run_id
        or args.attempt_id == args.successor_nonce
    ):
        raise BundleBuildError("attempt_id_must_be_distinct_canonical_uuid")
    staging_directory, output_directory, emergency_seal_directory = (
        validate_canonical_run_locations(
            run_id=args.run_id,
            staging_directory=args.staging_directory,
            output_directory=args.output_directory,
        )
    )
    if args.staging_directory.exists():
        raise BundleBuildError(f"staging_directory_exists:{args.staging_directory}")
    if args.output_directory.exists():
        raise BundleBuildError(f"output_directory_exists:{args.output_directory}")
    if emergency_seal_directory.exists():
        raise BundleBuildError(f"emergency_seal_directory_exists:{emergency_seal_directory}")
    if os.path.normcase(str(staging_directory)) == os.path.normcase(str(output_directory)):
        raise BundleBuildError("staging_output_must_be_distinct")
    if not args.python.is_file():
        raise BundleBuildError(f"python_missing:{args.python}")
    if sha256_file(ETW_AMENDMENT) != ETW_AMENDMENT_SHA256:
        raise BundleBuildError("etw_amendment_sha256_mismatch")
    parent_paths = parse_parent_specs(args.parent)
    for role, parent_path in parent_paths.items():
        for protected in (
            staging_directory,
            output_directory,
            emergency_seal_directory,
        ):
            prefix = os.path.normcase(str(protected)) + os.sep
            if os.path.normcase(str(parent_path)).startswith(prefix):
                raise BundleBuildError(f"parent_inside_protected_output:{role}")
            if _path_is_within(protected, parent_path.parent):
                raise BundleBuildError(f"protected_output_inside_parent_checkpoint_root:{role}")
    parent_checkpoints, parent_payloads = build_parent_checkpoints(parent_paths)
    # Validate and live-measure the complete Python/Git distributions before
    # the first contained Git source-identity read.  Later launcher boundaries
    # repeat the same measurement.
    toolchain = validate_toolchain_pins(args.toolchain_pins.resolve())
    source_identity = verify_source_identity(
        project_root,
        args.branch,
        args.expected_untracked,
        args.expected_untracked_digest,
    )
    parent_map_digest = parent_map_sha256(parent_checkpoints)
    external_terminal_fencing = validate_external_terminal_fencing(
        args.external_terminal_fencing.resolve(),
        expected_successor_binding={
            "run_id": args.run_id,
            "attempt_id": args.attempt_id,
            "commit": source_identity["revision"],
            "tree": source_identity["tree"],
            "nonce": args.successor_nonce,
            "parent_map_sha256": parent_map_digest,
            "staging_path": str(staging_directory),
            "output_path": str(output_directory),
            "emergency_seal_path": str(emergency_seal_directory),
        },
        expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
    )
    external_paths = {
        Path(str(pin["path"])).resolve()
        for pin in (
            *external_terminal_fencing["snapshots"],
            *external_terminal_fencing["exact_link_scans"],
            external_terminal_fencing["terminal_decision"],
            external_terminal_fencing["trusted_checkpoint"],
        )
    }
    if external_paths & set(parent_paths.values()):
        raise BundleBuildError("external_fencing_and_parent_paths_must_be_distinct")
    for external_path in external_paths:
        for protected in (
            staging_directory,
            output_directory,
            emergency_seal_directory,
        ):
            prefix = os.path.normcase(str(protected)) + os.sep
            if os.path.normcase(str(external_path)).startswith(prefix):
                raise BundleBuildError("external_fencing_inside_protected_output")
    expected_state, _runtime_state_document = validate_runtime_state_pins(
        args.runtime_state_pins.resolve(),
        project_root=project_root,
        source_identity=source_identity,
        parent_entries=parent_checkpoints,
        parent_payloads=parent_payloads,
    )
    cross_validate_external_fencing_job_scope(
        expected_state["job_scope_contract"], external_terminal_fencing
    )
    runtime = {name: source_pin(project_root, relative) for name, relative in RUNTIME_PATHS.items()}
    manifest = build_manifest(
        run_id=args.run_id,
        attempt_id=args.attempt_id,
        successor_nonce=args.successor_nonce,
        source_identity=source_identity,
        project_root=project_root,
        staging_directory=staging_directory,
        output_directory=output_directory,
        emergency_seal_directory=emergency_seal_directory,
        python_path=args.python,
        runtime=runtime,
        parent_checkpoints=parent_checkpoints,
        expected_state=expected_state,
        external_terminal_fencing=external_terminal_fencing,
        expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
        toolchain=toolchain,
    )
    staging_directory.mkdir(parents=True, exist_ok=False)
    manifest_path = staging_directory / "phase-b2-r7s1-work-order.json"
    write_exclusive(manifest_path, canonical_json_bytes(manifest))
    bridge_path = staging_directory / "invoke-x1-phase-b2-r7s1-bridge.ps1"
    bridge = render_bridge(
        manifest_sha256=sha256_file(manifest_path),
        manifest=manifest,
        runtime=runtime,
        project_root=project_root,
        source_identity=source_identity,
        python_path=args.python,
    )
    write_exclusive(bridge_path, bridge.encode("utf-8"))
    outer_path = staging_directory / "invoke-verified-x1-phase-b2-r7s1.ps1"
    outer = render_outer(
        bridge_sha256=sha256_file(bridge_path),
        run_id=args.run_id,
        trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
        toolchain=toolchain,
        successor_binding=external_terminal_fencing["successor_binding"],
    )
    write_exclusive(outer_path, outer.encode("utf-8"))
    powershell_pin = toolchain["powershell"]
    assert isinstance(powershell_pin, Mapping)
    validator_outcome = _run_contained(
        [
            str(powershell_pin["path"]),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(runtime["validator"]["path"]),
            "-ManifestPath",
            str(manifest_path),
            "-OuterPath",
            str(outer_path),
            "-BridgePath",
            str(bridge_path),
            "-ExpectedOuterSha256",
            sha256_file(outer_path),
            "-ExpectedTrustedCheckpointSha256",
            args.expected_trusted_checkpoint_sha256.lower(),
            "-OfflineContained",
        ],
        name="r7s1-builder-contained-offline-validator",
        cwd=project_root,
        env=dict(os.environ),
    )
    if validator_outcome.return_code != 0:
        raise BundleBuildError(
            "contained_offline_validator_failed:"
            + (
                validator_outcome.stderr.strip()
                or validator_outcome.stdout.strip()
                or str(validator_outcome.return_code)
            )
        )
    try:
        validator_payload = json.loads(validator_outcome.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise BundleBuildError("contained_offline_validator_output_invalid") from exc
    if not isinstance(validator_payload, dict):
        raise BundleBuildError("contained_offline_validator_output_object_required")
    if validator_payload.get("status") != "PASS":
        raise BundleBuildError("contained_offline_validator_not_pass")
    result = {
        "schema": "s8-v4-x1-phase-b2-r7s1-bundle-build/v1",
        "created_at": utc_now(),
        "mode": RESTORE_MODE,
        "run_id": args.run_id,
        "attempt_id": args.attempt_id,
        "staging_directory": str(staging_directory),
        "location_identity": {
            "staging": path_filesystem_identity(staging_directory),
            "output": path_filesystem_identity(output_directory),
            "emergency_seal": path_filesystem_identity(emergency_seal_directory),
        },
        "source_identity": source_identity,
        "parent_checkpoints": parent_checkpoints,
        "files": {
            "outer": {"path": str(outer_path), "sha256": sha256_file(outer_path)},
            "bridge": {"path": str(bridge_path), "sha256": sha256_file(bridge_path)},
            "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        },
        "staging_validation": {
            "status": "PASS",
            "scope": "offline_job_contained",
            "run_uuid": validator_outcome.run_uuid,
            "duration_seconds": validator_outcome.duration_seconds,
            "active_process_zero": validator_outcome.active_process_zero,
            "streams_drained": validator_outcome.streams_drained,
            "identity_coverage_complete": validator_outcome.identity_coverage_complete,
            "forced_termination_attempts": validator_outcome.forced_termination_attempts,
            "residual_pids": list(validator_outcome.residual_pids),
            "check_count": validator_payload.get("check_count"),
        },
        "actual_invocations": {"outer": 0, "bridge": 0, "runner": 0},
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
