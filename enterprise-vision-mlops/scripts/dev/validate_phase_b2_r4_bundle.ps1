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
    throw "bundle_validation_failed:literal_assignment_$VariableName:count=$($matches.Count)"
  }
  try {
    return [string]$matches[0].Right.SafeGetValue()
  }
  catch {
    throw "bundle_validation_failed:literal_assignment_$VariableName:not_constant"
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

  Assert-Check 'manifest_schema' ($manifest.schema_version -eq 'evm.s8_v4.x1_phase_b2_r4_work_order.v1') $manifest.schema_version
  Assert-Check 'manifest_revision_full' ([string]$manifest.canonical_revision -match '^[0-9a-f]{40}$') $manifest.canonical_revision
  Assert-Check 'manifest_tree_full' ([string]$manifest.canonical_tree -match '^[0-9a-f]{40}$') $manifest.canonical_tree
  Assert-Check 'manifest_repository_path' ([string]$manifest.repository.path -eq $repo) $manifest.repository.path
  Assert-Check 'manifest_python_path' ([string]$manifest.runtime.python -eq [IO.Path]::GetFullPath($Python)) $manifest.runtime.python

  $runnerPath = [IO.Path]::GetFullPath([string]$manifest.runtime.runner_path)
  $corePath = [IO.Path]::GetFullPath([string]$manifest.runtime.core_path)
  Assert-Check 'runner_is_inside_repository' ($runnerPath.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $runnerPath
  Assert-Check 'core_is_inside_repository' ($corePath.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $corePath
  $runnerSha = Get-Sha256 $runnerPath
  $coreSha = Get-Sha256 $corePath
  Assert-Check 'manifest_runner_sha' ($runnerSha -eq ([string]$manifest.runtime.runner_sha256).ToLowerInvariant()) $runnerSha
  Assert-Check 'manifest_core_sha' ($coreSha -eq ([string]$manifest.runtime.core_sha256).ToLowerInvariant()) $coreSha
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
    $runtimeOutput = @(& $Python -c 'import json;from evm.scale_validation.phase_b2_r4 import TimeoutContract;print(json.dumps(TimeoutContract().to_dict(),sort_keys=True,separators=(",",":")))' 2>&1)
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

  $phase = $manifest.phase_b2_contract
  Assert-Check 'phase_mode_docker_off' ($phase.mode -eq 'docker-off') $phase.mode
  Assert-Check 'phase_duration_180' ([int]$phase.duration_seconds -eq 180) $phase.duration_seconds
  Assert-Check 'phase_cadence_100' ([int]$phase.cadence_ms -eq 100) $phase.cadence_ms
  Assert-Check 'phase_samples_1800_each' (
    [int]$phase.windows_samples -eq 1800 -and [int]$phase.wsl_samples -eq 1800
  ) @{ windows = $phase.windows_samples; wsl = $phase.wsl_samples }

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
  $coreText = [IO.File]::ReadAllText($corePath)
  $runnerText = [IO.File]::ReadAllText($runnerPath)
  Assert-Check 'runtime_uses_os_exclusive_create' ($coreText.Contains('os.O_EXCL')) 'os.O_EXCL'
  Assert-Check 'runtime_deadline_budget_guard_present' ($coreText.Contains('probe_launch_budget_seconds')) 'probe_launch_budget_seconds'
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
  $forbiddenAstCommands = @('Remove-Item', 'Clear-Content', 'Set-Content', 'Out-File', 'Add-Content', 'Stop-Process')
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
