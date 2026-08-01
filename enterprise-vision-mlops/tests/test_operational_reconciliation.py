from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from evm.operations.reconciliation import plan_device_plugin_reconciliation


FIXTURE = Path(__file__).parent / "fixtures" / "operations" / "stale_device_plugin_daemonset.json"
OLD = "/usr/lib/wsl/drivers/nv_dispi.inf_amd64_oldhash"
CURRENT = "/usr/lib/wsl/drivers/nv_dispi.inf_amd64_currenthash"


def _resource() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_stale_driver_path_produces_exact_non_mutating_plan() -> None:
    plan = plan_device_plugin_reconciliation(_resource(), [CURRENT])

    assert plan.decision == "change_required"
    assert plan.current_driver_path == OLD
    assert plan.proposed_driver_path == CURRENT
    assert plan.mutation_performed is False
    assert {change.field for change in plan.changes} == {
        "hostPath",
        "volumeMount",
        "LD_LIBRARY_PATH",
    }
    assert all(change.current != change.proposed for change in plan.changes)


def test_current_driver_path_is_idempotent() -> None:
    resource = _resource()
    serialized = json.dumps(resource).replace(OLD, CURRENT)
    plan = plan_device_plugin_reconciliation(json.loads(serialized), [CURRENT])

    assert plan.decision == "no_change"
    assert plan.changes == []
    assert plan.mutation_performed is False


def test_zero_or_multiple_discovered_paths_fail_closed() -> None:
    missing = plan_device_plugin_reconciliation(_resource(), [])
    multiple = plan_device_plugin_reconciliation(
        _resource(),
        [CURRENT, "/usr/lib/wsl/drivers/nv_dispi.inf_amd64_otherhash"],
    )

    assert missing.decision == "blocked"
    assert "driver_path_discovery_cardinality:0" in missing.blockers
    assert multiple.decision == "blocked"
    assert "driver_path_discovery_cardinality:2" in multiple.blockers


def test_ambiguous_or_malformed_daemonset_fails_closed() -> None:
    resource = _resource()
    resource["spec"]["template"]["spec"]["volumes"].append(
        deepcopy(resource["spec"]["template"]["spec"]["volumes"][0])
    )
    plan = plan_device_plugin_reconciliation(resource, [CURRENT])

    assert plan.decision == "blocked"
    assert "wsl_driver_volume_cardinality:2" in plan.blockers
    assert plan.mutation_performed is False
