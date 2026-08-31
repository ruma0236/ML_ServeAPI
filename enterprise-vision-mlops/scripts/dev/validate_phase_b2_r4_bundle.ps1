#Requires -Version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$BundleDirectory,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][string]$RepositoryRoot,
  [Parameter(Mandatory = $true)][string]$Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$checks = [Collections.Generic.List[object]]::new()

function Add-Pass([string]$Name, [object]$Detail) {
  [void]$checks.Add([ordered]@{ name = $Name; status = 'PASS'; detail = $Detail })
}

function Assert-Check([string]$Name, [bool]$Condition, [object]$Detail) {
  if (-not $Condition) {
    throw "bundle_validation_failed:${Name}:$Detail"
  }
  Add-Pass $Name $Detail
}

function Get-Sha256([string]$Path) {
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}

function Read-PowerShellAst([string]$Path) {
  $tokens = $null
  $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile(
    $Path,
    [ref]$tokens,
    [ref]$errors
  )
  Assert-Check "powershell_ast_$([IO.Path]::GetFileName($Path))" ($errors.Count -eq 0) @($errors | ForEach-Object { $_.Message })
  return $ast
}

function Get-LiteralAssignment(
  [Management.Automation.Language.Ast]$Ast,
  [string]$VariableName
) {
  $matches = @($Ast.FindAll({
        param($node)
        if ($node -isnot [Management.Automation.Language.AssignmentStatementAst]) {
          return $false
        }
        if ($node.Left -isnot [Management.Automation.Language.VariableExpressionAst]) {
          return $false
        }
        return $node.Left.VariablePath.UserPath -eq $VariableName
      }, $true))
  if ($matches.Count -ne 1) {
    throw "bundle_validation_failed:literal_assignment_${VariableName}:count=$($matches.Count)"
  }
  $right = $matches[0].Right
  if ($right -is [Management.Automation.Language.CommandExpressionAst]) {
    $right = $right.Expression
  }
  try {
    return [string]$right.SafeGetValue()
  }
  catch {
    throw "bundle_validation_failed:literal_assignment_${VariableName}:not_constant"
  }
}

function Get-PropertyValue([object]$Value, [string]$Name) {
  $property = $Value.PSObject.Properties[$Name]
  if ($null -eq $property) {
    throw "bundle_validation_failed:json_property_missing:$Name"
  }
  return $property.Value
}

function Invoke-GitRead([string[]]$Arguments) {
  $text = @(& git -c "safe.directory=$RepositoryRoot" -C $RepositoryRoot @Arguments 2>&1)
  if ($LASTEXITCODE -ne 0) {
    throw "bundle_validation_failed:git_read:$($Arguments -join ','):$($text -join [Environment]::NewLine)"
  }
  return ($text -join [Environment]::NewLine).Trim()
}

try {
  $bundle = [IO.Path]::GetFullPath($BundleDirectory)
  $repo = [IO.Path]::GetFullPath($RepositoryRoot)
  Assert-Check 'bundle_directory_exists' (Test-Path -LiteralPath $bundle -PathType Container) $bundle
  Assert-Check 'repository_root_exists' (Test-Path -LiteralPath $repo -PathType Container) $repo
  Assert-Check 'python_exists' (Test-Path -LiteralPath $Python -PathType Leaf) $Python

  $manifestPath = Join-Path $bundle 'phase-b2-r4-work-order.json'
  $bridgePath = Join-Path $bundle 'invoke-x1-phase-b2-r4-bridge.ps1'
  $outerPath = Join-Path $bundle 'invoke-verified-x1-phase-b2-r4.ps1'
  $expectedNames = @(
    'invoke-verified-x1-phase-b2-r4.ps1',
    'invoke-x1-phase-b2-r4-bridge.ps1',
    'phase-b2-r4-work-order.json'
  )
  $actualItems = @(Get-ChildItem -LiteralPath $bundle -Force)
  $actualNames = @($actualItems | ForEach-Object { $_.Name })
  $difference = @(Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames)
  Assert-Check 'bundle_exact_three_files' (
    $actualItems.Count -eq 3 -and
    @($actualItems | Where-Object { $_.PSIsContainer }).Count -eq 0 -and
    $difference.Count -eq 0
  ) $actualNames
  foreach ($item in $actualItems) {
    Assert-Check "bundle_not_reparse_$($item.Name)" (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) $item.Attributes.ToString()
  }

  $outerAst = Read-PowerShellAst $outerPath
  $bridgeAst = Read-PowerShellAst $bridgePath
  $outerText = [IO.File]::ReadAllText($outerPath)
  $bridgeText = [IO.File]::ReadAllText($bridgePath)
  $manifestText = [IO.File]::ReadAllText($manifestPath)
  try {
    $manifest = $manifestText | ConvertFrom-Json -ErrorAction Stop
  }
  catch {
    throw "bundle_validation_failed:manifest_json:$($_.Exception.Message)"
  }
  Add-Pass 'manifest_json' 'parsed'

  $observedOuter = Get-Sha256 $outerPath
  Assert-Check 'outer_sha_pin' ($observedOuter -eq $ExpectedOuterSha256.ToLowerInvariant()) $observedOuter
  $expectedBridge = (Get-LiteralAssignment $outerAst 'ExpectedBridgeSha256').ToLowerInvariant()
  $observedBridge = Get-Sha256 $bridgePath
  Assert-Check 'outer_to_bridge_sha_pin' ($observedBridge -eq $expectedBridge) $observedBridge
  $expectedManifest = (Get-LiteralAssignment $bridgeAst 'ExpectedManifestSha256').ToLowerInvariant()
  $observedManifest = Get-Sha256 $manifestPath
  Assert-Check 'bridge_to_manifest_sha_pin' ($observedManifest -eq $expectedManifest) $observedManifest

  $outerVerifyIndex = $outerText.IndexOf('outer_sha256_mismatch', [StringComparison]::Ordinal)
  $outerInvokeIndex = $outerText.IndexOf('# R4_BRIDGE_INVOKE', [StringComparison]::Ordinal)
  Assert-Check 'outer_self_sha_verified_immediately_before_execution' (
    $outerVerifyIndex -ge 0 -and $outerInvokeIndex -gt $outerVerifyIndex
  ) @{ verify_index = $outerVerifyIndex; invoke_index = $outerInvokeIndex }
  $bridgeVerifyIndex = $bridgeText.IndexOf('manifest_sha256_mismatch', [StringComparison]::Ordinal)
  $runnerInvokeIndex = $bridgeText.IndexOf('# R4_RUNNER_INVOKE', [StringComparison]::Ordinal)
  Assert-Check 'bridge_manifest_verified_before_runner' (
    $bridgeVerifyIndex -ge 0 -and $runnerInvokeIndex -gt $bridgeVerifyIndex
  ) @{ verify_index = $bridgeVerifyIndex; invoke_index = $runnerInvokeIndex }
  Assert-Check 'outer_exact_one_bridge_call_site' (($outerText.Split(@('# R4_BRIDGE_INVOKE'), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  Assert-Check 'bridge_exact_one_runner_call_site' (($bridgeText.Split(@('# R4_RUNNER_INVOKE'), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  $outerBridgeCalls = @($outerAst.FindAll({
        param($node)
        if ($node -isnot [Management.Automation.Language.CommandAst] -or
          $node.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Ampersand -or
          $node.CommandElements.Count -lt 1) { return $false }
        $target = $node.CommandElements[0]
        return $target -is [Management.Automation.Language.VariableExpressionAst] -and
          $target.VariablePath.UserPath -eq 'bridgePath'
      }, $true))
  $bridgeRunnerCalls = @($bridgeAst.FindAll({
        param($node)
        if ($node -isnot [Management.Automation.Language.CommandAst] -or
          $node.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Ampersand -or
          $node.CommandElements.Count -lt 1) { return $false }
        $target = $node.CommandElements[0]
        return $target -is [Management.Automation.Language.VariableExpressionAst] -and
          $target.VariablePath.UserPath -eq 'PythonPath'
      }, $true))
  Assert-Check 'outer_ast_exact_one_bridge_invocation' ($outerBridgeCalls.Count -eq 1) $outerBridgeCalls.Count
  Assert-Check 'bridge_ast_exact_one_runner_invocation' ($bridgeRunnerCalls.Count -eq 1) $bridgeRunnerCalls.Count
  Assert-Check 'outer_hash_guard_exact' ($outerText.Contains('if ($outerObserved -ne $outerExpected) { throw ''outer_sha256_mismatch'' }')) 'exact guard'
  Assert-Check 'outer_bridge_guard_exact' ($outerText.Contains('if ($bridgeObserved -ne $ExpectedBridgeSha256) { throw ''bridge_sha256_mismatch'' }')) 'exact guard'
  Assert-Check 'bridge_manifest_guard_exact' ($bridgeText.Contains('if ((Get-Sha256 $ManifestPath) -ne $ExpectedManifestSha256) { throw ''manifest_sha256_mismatch'' }')) 'exact guard'
  Assert-Check 'bridge_runner_guard_exact' ($bridgeText.Contains('if ((Get-Sha256 $RunnerPath) -ne $ExpectedRunnerSha256) { throw ''runner_sha256_mismatch'' }')) 'exact guard'
  Assert-Check 'bridge_core_guard_exact' ($bridgeText.Contains('if ((Get-Sha256 $CorePath) -ne $ExpectedCoreSha256) { throw ''core_sha256_mismatch'' }')) 'exact guard'
  Assert-Check 'bridge_admin_gate_before_runner' (
    $bridgeText.IndexOf('administrator_token_required', [StringComparison]::Ordinal) -ge 0 -and
    $bridgeText.IndexOf('administrator_token_required', [StringComparison]::Ordinal) -lt $runnerInvokeIndex
  ) 'token gate precedes runner'

  Assert-Check 'manifest_schema' ($manifest.schema_version -eq 'evm.s8_v4.x1_phase_b2_r4_work_order.v1') $manifest.schema_version
  Assert-Check 'manifest_revision_full' ([string]$manifest.canonical_revision -match '^[0-9a-f]{40}$') $manifest.canonical_revision
  Assert-Check 'manifest_tree_full' ([string]$manifest.canonical_tree -match '^[0-9a-f]{40}$') $manifest.canonical_tree
  Assert-Check 'manifest_repository_path' ([string]$manifest.repository.path -eq $repo) $manifest.repository.path
  Assert-Check 'manifest_python_path' ([string]$manifest.runtime.python -eq [IO.Path]::GetFullPath($Python)) $manifest.runtime.python

  $runnerPath = [IO.Path]::GetFullPath([string]$manifest.runtime.runner_path)
  $corePath = [IO.Path]::GetFullPath([string]$manifest.runtime.core_path)
  $validatorPath = [IO.Path]::GetFullPath([string]$manifest.runtime.validator_path)
  Assert-Check 'runner_is_inside_repository' ($runnerPath.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $runnerPath
  Assert-Check 'core_is_inside_repository' ($corePath.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $corePath
  Assert-Check 'validator_is_inside_repository' ($validatorPath.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $validatorPath
  $runnerSha = Get-Sha256 $runnerPath
  $coreSha = Get-Sha256 $corePath
  $validatorSha = Get-Sha256 $validatorPath
  $coreText = [IO.File]::ReadAllText($corePath)
  $runnerText = [IO.File]::ReadAllText($runnerPath)
  Assert-Check 'manifest_runner_sha' ($runnerSha -eq ([string]$manifest.runtime.runner_sha256).ToLowerInvariant()) $runnerSha
  Assert-Check 'manifest_core_sha' ($coreSha -eq ([string]$manifest.runtime.core_sha256).ToLowerInvariant()) $coreSha
  Assert-Check 'manifest_validator_sha' ($validatorSha -eq ([string]$manifest.runtime.validator_sha256).ToLowerInvariant()) $validatorSha
  Assert-Check 'bridge_runner_sha' ($runnerSha -eq (Get-LiteralAssignment $bridgeAst 'ExpectedRunnerSha256').ToLowerInvariant()) $runnerSha
  Assert-Check 'bridge_core_sha' ($coreSha -eq (Get-LiteralAssignment $bridgeAst 'ExpectedCoreSha256').ToLowerInvariant()) $coreSha
  Assert-Check 'bridge_revision_pin' ([string]$manifest.canonical_revision -eq (Get-LiteralAssignment $bridgeAst 'PinnedRevision')) $manifest.canonical_revision
  Assert-Check 'bridge_tree_pin' ([string]$manifest.canonical_tree -eq (Get-LiteralAssignment $bridgeAst 'PinnedTree')) $manifest.canonical_tree

  $timeoutNames = @(
    'kubectl_timeout_seconds',
    'wrapper_timeout_seconds',
    'restore_deadline_seconds',
    'residual_repoll_seconds',
    'stream_drain_seconds'
  )
  $expectedTimeouts = [ordered]@{
    kubectl_timeout_seconds = 8.0
    wrapper_timeout_seconds = 15.0
    restore_deadline_seconds = 600.0
    residual_repoll_seconds = 120.0
    stream_drain_seconds = 5.0
  }
  foreach ($name in $timeoutNames) {
    $actual = [double](Get-PropertyValue $manifest.timeout_contract $name)
    Assert-Check "manifest_timeout_$name" ($actual -eq [double]$expectedTimeouts[$name]) $actual
  }
  Assert-Check 'nested_timeout_order' (
    [double]$manifest.timeout_contract.kubectl_timeout_seconds -lt [double]$manifest.timeout_contract.wrapper_timeout_seconds -and
    [double]$manifest.timeout_contract.wrapper_timeout_seconds -lt [double]$manifest.timeout_contract.restore_deadline_seconds
  ) $manifest.timeout_contract
  Assert-Check 'residual_120_protected_by_deadline_budget' (
    ([double]$manifest.timeout_contract.wrapper_timeout_seconds +
      [double]$manifest.timeout_contract.residual_repoll_seconds +
      [double]$manifest.timeout_contract.stream_drain_seconds) -lt
    [double]$manifest.timeout_contract.restore_deadline_seconds
  ) 'wrapper+residual+drain < restore deadline'

  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Join-Path $repo 'src'
    $runtimeOutput = @(& $Python -c 'import json;from evm.scale_validation.phase_b2_r4 import TimeoutContract;print(json.dumps(TimeoutContract().to_dict(),sort_keys=True))' 2>&1)
    if ($LASTEXITCODE -ne 0) {
      throw "bundle_validation_failed:runtime_contract_read:$($runtimeOutput -join [Environment]::NewLine)"
    }
    $runtimeContract = (($runtimeOutput -join [Environment]::NewLine).Trim() | ConvertFrom-Json -ErrorAction Stop)
  }
  finally {
    $env:PYTHONPATH = $previousPythonPath
  }
  foreach ($name in $timeoutNames) {
    $manifestValue = [double](Get-PropertyValue $manifest.timeout_contract $name)
    $runtimeValue = [double](Get-PropertyValue $runtimeContract $name)
    Assert-Check "manifest_runtime_timeout_match_$name" ($manifestValue -eq $runtimeValue) @{ manifest = $manifestValue; runtime = $runtimeValue }
  }

  $actualBranch = Invoke-GitRead @('branch', '--show-current')
  $actualRevision = Invoke-GitRead @('rev-parse', 'HEAD')
  $actualTree = Invoke-GitRead @('rev-parse', 'HEAD^{tree}')
  $originRevision = Invoke-GitRead @('rev-parse', "origin/$($manifest.repository.branch)")
  $trackedStatus = Invoke-GitRead @('status', '--porcelain=v1', '--untracked-files=no')
  $allStatus = Invoke-GitRead @('status', '--porcelain=v1', '--untracked-files=all')
  $untrackedCount = @($allStatus -split "`r?`n" | Where-Object { $_ -like '?? *' }).Count
  Assert-Check 'git_branch_pin' ($actualBranch -eq [string]$manifest.repository.branch) $actualBranch
  Assert-Check 'git_revision_pin' ($actualRevision -eq [string]$manifest.canonical_revision) $actualRevision
  Assert-Check 'git_tree_pin' ($actualTree -eq [string]$manifest.canonical_tree) $actualTree
  Assert-Check 'git_origin_revision_pin' ($originRevision -eq [string]$manifest.canonical_revision) $originRevision
  Assert-Check 'git_tracked_clean' ([string]::IsNullOrWhiteSpace($trackedStatus)) $trackedStatus
  Assert-Check 'git_untracked_preserved' ($untrackedCount -eq [int]$manifest.repository.preserved_untracked_count) $untrackedCount

  $expectedComposeServices = @(
    'airflow-postgres', 'airflow-scheduler', 'airflow-webserver', 'api',
    'control-panel', 'control-plane-postgres', 'grafana', 'minio', 'mlflow',
    'otel-collector', 'postgres', 'prometheus', 'task-queue-worker'
  )
  $actualComposeServices = @($manifest.expected_state.compose_services | ForEach-Object { [string]$_ })
  $composeDifference = @(Compare-Object -ReferenceObject $expectedComposeServices -DifferenceObject $actualComposeServices)
  Assert-Check 'expected_compose_services_exact' (
    $actualComposeServices.Count -eq $expectedComposeServices.Count -and $composeDifference.Count -eq 0
  ) $actualComposeServices
  Assert-Check 'expected_b0_uid_exact' (
    [string]$manifest.expected_state.b0.uid -eq 'cfdab424-dcc5-4d5f-ae7530441ef4'
  ) $manifest.expected_state.b0.uid
  Assert-Check 'expected_b0_image_exact' (
    [string]$manifest.expected_state.b0.image -eq 'enterprise-vision-mlops-efficientnet-serving@sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a'
  ) $manifest.expected_state.b0.image
  Assert-Check 'expected_b0_endpoints_exact' (
    [string]$manifest.expected_state.b0.ready_url -eq 'http://127.0.0.1:30800/ready' -and
    [string]$manifest.expected_state.b0.predict_url -eq 'http://127.0.0.1:30800/predict'
  ) $manifest.expected_state.b0
  Assert-Check 'expected_b0_sample_exact' (
    [string]$manifest.expected_state.b0.sample_image_uri -eq '/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG'
  ) $manifest.expected_state.b0.sample_image_uri
  $expectedPrometheusJobs = @('evm-api', 'evm-b0-production', 'evm-otel-collector', 'evm-task-queue-worker', 'prometheus')
  $actualPrometheusJobs = @($manifest.expected_state.prometheus_jobs | ForEach-Object { [string]$_ })
  $prometheusDifference = @(Compare-Object -ReferenceObject $expectedPrometheusJobs -DifferenceObject $actualPrometheusJobs)
  Assert-Check 'expected_prometheus_jobs_exact' (
    $actualPrometheusJobs.Count -eq $expectedPrometheusJobs.Count -and $prometheusDifference.Count -eq 0
  ) $actualPrometheusJobs
  Assert-Check 'expected_service_urls_exact' (
    [string]$manifest.expected_state.prometheus_targets_url -eq 'http://127.0.0.1:9090/api/v1/targets' -and
    [string]$manifest.expected_state.api_base_url -eq 'http://127.0.0.1:8000'
  ) $manifest.expected_state
  $selectors = @($manifest.expected_state.x1_kubernetes_selectors | ForEach-Object { [string]$_ })
  Assert-Check 'expected_x1_selector_exact' (
    $selectors.Count -eq 1 -and $selectors[0] -eq 'evm.openai.local/scenario=s8-v4-x1'
  ) $selectors
  $ports = @($manifest.expected_state.x1_ports | ForEach-Object { [int]$_ })
  Assert-Check 'expected_x1_ports_exact' (
    $ports.Count -eq 3 -and $ports[0] -eq 31120 -and $ports[1] -eq 31121 -and $ports[2] -eq 31122
  ) $ports
  Assert-Check 'expected_x1_docker_filter_exact' (
    [string]$manifest.expected_state.x1_docker_name_filter -eq 'name=evm-x1'
  ) $manifest.expected_state.x1_docker_name_filter
  Assert-Check 'expected_gpu_lease_path_exact' (
    [string]$manifest.expected_state.gpu_lease_path -eq 'F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json'
  ) $manifest.expected_state.gpu_lease_path
  $expectedResiduePaths = @(
    'F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/prometheus-targets/s8-v4-x1-triton.json',
    'F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/prometheus-targets/s8-v4-x1-api.json'
  )
  $actualResiduePaths = @($manifest.expected_state.x1_residue_paths | ForEach-Object { [string]$_ })
  Assert-Check 'expected_x1_residue_paths_exact' (
    $actualResiduePaths.Count -eq 2 -and
    @(Compare-Object -ReferenceObject $expectedResiduePaths -DifferenceObject $actualResiduePaths).Count -eq 0
  ) $actualResiduePaths
  Assert-Check 'expected_nonvacuous_queue_job_claim_checks' (
    @($manifest.expected_state.active_job_roots).Count -eq 0 -and
    @($manifest.expected_state.active_claim_roots).Count -eq 0 -and
    $runnerText.Contains('self._kubectl_command("get", "jobs", "-A", "-o", "json")') -and
    $runnerText.Contains('FROM evm_control_plane.lifecycle_claims')
  ) 'Kubernetes jobs plus database claims'
  Assert-Check 'r3_checkpoint_pin_exact' (
    [string]$manifest.checkpoint.path -eq 'F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private\s8-v4\x1-clock-phase-b2\x1-clock-phase-b2-20260831T114427Z-0a68addf-r3\outcome.json' -and
    [string]$manifest.checkpoint.sha256 -eq '8b424add49fe59b07da0e0c1685b3d136a7f71b3126c55595ff441e5eb1de5a8' -and
    $manifest.checkpoint.docker_off_probe_must_not_repeat -eq $true -and
    $manifest.checkpoint.lifecycle_calls_must_not_repeat -eq $true
  ) $manifest.checkpoint

  $phase = $manifest.phase_b2_contract
  Assert-Check 'phase_mode_docker_off' ($phase.mode -eq 'docker-off') $phase.mode
  Assert-Check 'phase_duration_180' ([int]$phase.duration_seconds -eq 180) $phase.duration_seconds
  Assert-Check 'phase_cadence_100' ([int]$phase.cadence_ms -eq 100) $phase.cadence_ms
  Assert-Check 'phase_samples_1800_each' (
    [int]$phase.windows_samples -eq 1800 -and [int]$phase.wsl_samples -eq 1800
  ) @{ windows = $phase.windows_samples; wsl = $phase.wsl_samples }
  foreach ($name in @('windows_discontinuity', 'wsl_discontinuity', 'backward_step', 'unclassified_gap', 'bracket_violation', 'residual_pid')) {
    Assert-Check "phase_exact_zero_$name" ([int](Get-PropertyValue $phase $name) -eq 0) (Get-PropertyValue $phase $name)
  }
  Assert-Check 'phase_maximum_invocations_one' ([int]$phase.maximum_invocations -eq 1) $phase.maximum_invocations

  $callNames = @('probe', 'compose_stop', 'docker_desktop_stop', 'wsl_shutdown', 'docker_desktop_start', 'compose_start')
  foreach ($name in $callNames) {
    Assert-Check "restore_only_call_zero_$name" ([int](Get-PropertyValue $manifest.call_contract.restore_only $name) -eq 0) (Get-PropertyValue $manifest.call_contract.restore_only $name)
    Assert-Check "phase_b2_call_once_$name" ([int](Get-PropertyValue $manifest.call_contract.phase_b2 $name) -eq 1) (Get-PropertyValue $manifest.call_contract.phase_b2 $name)
  }
  foreach ($name in @('full_stack_3180', 'q0', 'calibration_54', 'matrix_78', 'integrated_v4', 'etw')) {
    Assert-Check "downstream_call_zero_$name" ([int](Get-PropertyValue $manifest.call_contract.downstream $name) -eq 0) (Get-PropertyValue $manifest.call_contract.downstream $name)
  }
  Assert-Check 'evidence_create_exclusive_contract' ($manifest.evidence.write_mode -eq 'create-exclusive') $manifest.evidence.write_mode
  Assert-Check 'failure_forbids_success_marker' ($manifest.evidence.failure_creates_completion_marker -eq $false) $manifest.evidence.failure_creates_completion_marker
  Assert-Check 'success_requires_all_invariants' ($manifest.evidence.success_requires_all_invariants -eq $true) $manifest.evidence.success_requires_all_invariants
  Assert-Check 'runtime_uses_os_exclusive_create' ($coreText.Contains('os.O_EXCL')) 'os.O_EXCL'
  Assert-Check 'runtime_deadline_budget_guard_present' ($coreText.Contains('probe_launch_budget_seconds')) 'probe_launch_budget_seconds'
  Assert-Check 'runtime_timeout_manual_latch_present' (
    $runnerText.Contains('outcome.manual_intervention_required or outcome.timed_out') -and
    $runnerText.Contains('"timeout_manual_latch": outcome.timed_out')
  ) 'all timeouts block subsequent probes'
  Assert-Check 'runtime_compound_probe_fail_closed_present' (
    $runnerText.Contains('def _failed_process_chain(') -and
    $runnerText.Contains('"process_chain_stopped": True')
  ) 'compound child chain stops on first failure'
  Assert-Check 'runtime_phase_b2_marker_guard_present' (
    $coreText.Contains('report.mode != "phase-b2"') -and $coreText.Contains('report.decision != "phase_b2_pass"')
  ) 'phase-b2 decision guard'

  $forbiddenPatterns = @(
    '(?im)\btaskkill(?:\.exe)?\b[^\r\n]*(?:/f|/F)',
    '(?im)\bstop-process\b[^\r\n]*\b-force\b',
    '(?im)\bdocker(?:\.exe)?\s+(?:compose\s+)?(?:down|up|system\s+prune)\b',
    '(?im)\bwsl(?:\.exe)?\b[^\r\n]*--unregister\b',
    '(?im)\bkubectl(?:\.exe)?\s+(?:delete|drain|reset)\b',
    '(?im)\bgit\s+(?:reset|clean|checkout)\b',
    '(?im)\bchkdsk\b',
    '(?im)\.kill\s*\(',
    '(?im)\.terminate\s*\('
  )
  $combinedSource = $outerText + "`n" + $bridgeText + "`n" + $manifestText + "`n" + $coreText + "`n" + $runnerText
  foreach ($pattern in $forbiddenPatterns) {
    Assert-Check "forbidden_absent_$pattern" (-not [regex]::IsMatch($combinedSource, $pattern)) 'absent'
  }
  Assert-Check 'old_staging_bundle_absent' (-not [regex]::IsMatch(
      $combinedSource,
      '(?i)scale_validation[\\/]+staging[\\/]+[^\r\n''"]+-r[23]'
    )) 'r2/r3 staging path absent'
  $forbiddenAstCommands = @(
    'Remove-Item', 'Clear-Content', 'Set-Content', 'Out-File', 'Add-Content',
    'Stop-Process', 'Start-Process', 'Invoke-Expression', 'Invoke-Command'
  )
  foreach ($entry in @(@{ name = 'outer'; ast = $outerAst }, @{ name = 'bridge'; ast = $bridgeAst })) {
    $commandNames = @($entry.ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ })
    foreach ($forbidden in $forbiddenAstCommands) {
      Assert-Check "powershell_ast_forbidden_absent_$($entry.name)_$forbidden" ($commandNames -notcontains $forbidden) $commandNames
    }
  }

  [ordered]@{
    schema_version = 'evm.s8_v4.x1_phase_b2_r4_bundle_validation.v1'
    status = 'PASS'
    validated_at = [DateTime]::UtcNow.ToString('o')
    check_count = $checks.Count
    observed_sha256 = [ordered]@{
      outer = $observedOuter
      bridge = $observedBridge
      manifest = $observedManifest
      runner = $runnerSha
      core = $coreSha
    }
    checks = $checks
  } | ConvertTo-Json -Depth 12 -Compress
  exit 0
}
catch {
  [ordered]@{
    schema_version = 'evm.s8_v4.x1_phase_b2_r4_bundle_validation.v1'
    status = 'FAIL'
    validated_at = [DateTime]::UtcNow.ToString('o')
    error = $_.Exception.Message
    passed_check_count = $checks.Count
    checks = $checks
  } | ConvertTo-Json -Depth 12 -Compress
  exit 2
}
