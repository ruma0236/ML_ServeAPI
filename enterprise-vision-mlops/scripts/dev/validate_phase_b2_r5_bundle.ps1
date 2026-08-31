#Requires -Version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$ManifestPath,
  [Parameter(Mandatory = $true)][string]$OuterPath,
  [Parameter(Mandatory = $true)][string]$BridgePath,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidateSet('restore-only', 'fresh')][string]$Mode,
  [switch]$PreExecution
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$checks = [Collections.Generic.List[object]]::new()
$oldR4Revision = 'e48c1d82938b9f64b414d58bb71c53dd258fbd78'
$oldR4RevisionPrefix = 'e48c1d8'

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
  if ($null -eq $Value) {
    throw "bundle_validation_failed:json_parent_missing:$Name"
  }
  $property = $Value.PSObject.Properties[$Name]
  if ($null -eq $property) {
    throw "bundle_validation_failed:json_property_missing:$Name"
  }
  return $property.Value
}

function Invoke-GitRead([string[]]$Arguments) {
  $text = @(& git.exe -c "safe.directory=$script:RepositoryRoot" -C $script:RepositoryRoot @Arguments 2>&1)
  if ($LASTEXITCODE -ne 0) {
    throw "bundle_validation_failed:git_read:$($Arguments -join ','):$($text -join [Environment]::NewLine)"
  }
  return ($text -join [Environment]::NewLine).Trim()
}

function Get-GitBlobOid(
  [string]$Revision,
  [string]$AbsolutePath,
  [string]$GitTopLevel
) {
  $fullPath = [IO.Path]::GetFullPath($AbsolutePath)
  $prefix = [IO.Path]::GetFullPath($GitTopLevel).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
  if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "bundle_validation_failed:runtime_path_outside_git_tree:$fullPath"
  }
  $relativePath = $fullPath.Substring($prefix.Length).Replace('\', '/')
  # A repository may expose the application from a subdirectory below the Git
  # top-level. Revision:path is always top-relative, unlike an ls-tree pathspec
  # evaluated from the current -C directory.
  $entry = (Invoke-GitRead @('rev-parse', "${Revision}:$relativePath")).ToLowerInvariant()
  if ($entry -notmatch '^[0-9a-f]{40}$') {
    throw "bundle_validation_failed:git_blob_missing:$relativePath"
  }
  return $entry
}

function Get-AmpersandInvocationCount(
  [Management.Automation.Language.Ast]$Ast,
  [string]$VariableName
) {
  return @($Ast.FindAll({
        param($node)
        if ($node -isnot [Management.Automation.Language.CommandAst] -or
          $node.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Ampersand -or
          $node.CommandElements.Count -lt 1) {
          return $false
        }
        $target = $node.CommandElements[0]
        return $target -is [Management.Automation.Language.VariableExpressionAst] -and
          $target.VariablePath.UserPath -eq $VariableName
      }, $true)).Count
}

function Assert-ExactSha([string]$Name, [object]$Value) {
  $text = [string]$Value
  Assert-Check $Name ($text -cmatch '^[0-9a-f]{64}$') $text
}

function Assert-ExactBlob([string]$Name, [object]$Value) {
  $text = [string]$Value
  Assert-Check $Name ($text -cmatch '^[0-9a-f]{40}$') $text
}

try {
  $manifestPath = [IO.Path]::GetFullPath($ManifestPath)
  $outerPath = [IO.Path]::GetFullPath($OuterPath)
  $bridgePath = [IO.Path]::GetFullPath($BridgePath)
  $bundle = [IO.Path]::GetDirectoryName($manifestPath)
  Assert-Check 'bundle_directory_exists' (Test-Path -LiteralPath $bundle -PathType Container) $bundle
  Assert-Check 'manifest_path_exact' ($manifestPath -eq (Join-Path $bundle 'phase-b2-r5-work-order.json')) $manifestPath
  Assert-Check 'outer_path_exact' ($outerPath -eq (Join-Path $bundle 'invoke-verified-x1-phase-b2-r5.ps1')) $outerPath
  Assert-Check 'bridge_path_exact' ($bridgePath -eq (Join-Path $bundle 'invoke-x1-phase-b2-r5-bridge.ps1')) $bridgePath
  $expectedNames = [Collections.Generic.List[string]]::new()
  foreach ($name in @(
    'invoke-verified-x1-phase-b2-r5.ps1',
    'invoke-x1-phase-b2-r5-bridge.ps1',
    'phase-b2-r5-work-order.json'
  )) { [void]$expectedNames.Add($name) }
  if ($PreExecution) {
    [void]$expectedNames.Add('r5-outer-invocation-reservation.json')
  }
  $actualItems = @(Get-ChildItem -LiteralPath $bundle -Force)
  $actualNames = @($actualItems | ForEach-Object { $_.Name })
  $difference = @(Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames)
  Assert-Check 'bundle_exact_expected_files' (
    $actualItems.Count -eq $expectedNames.Count -and
    @($actualItems | Where-Object { $_.PSIsContainer }).Count -eq 0 -and
    $difference.Count -eq 0
  ) $actualNames
  foreach ($item in $actualItems) {
    Assert-Check "bundle_not_reparse_$($item.Name)" (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) $item.Attributes.ToString()
  }
  if ($PreExecution) {
    $reservationPath = Join-Path $bundle 'r5-outer-invocation-reservation.json'
    try {
      $reservation = [IO.File]::ReadAllText($reservationPath) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
      throw "bundle_validation_failed:outer_reservation_json:$($_.Exception.Message)"
    }
    Assert-Check 'outer_reservation_schema' ([string]$reservation.schema -eq 's8-v4-x1-phase-b2-r5-outer-reservation/v1') $reservation.schema
    Assert-Check 'outer_reservation_mode' ([string]$reservation.mode -eq $Mode) $reservation.mode
  }

  $selfAst = Read-PowerShellAst $PSCommandPath
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
  $repo = [IO.Path]::GetFullPath([string]$manifest.repository.path)
  $script:RepositoryRoot = $repo
  $pythonPath = [IO.Path]::GetFullPath([string]$manifest.runtime.python)
  Assert-Check 'repository_root_exists' (Test-Path -LiteralPath $repo -PathType Container) $repo
  Assert-Check 'python_exists' (Test-Path -LiteralPath $pythonPath -PathType Leaf) $pythonPath

  $observedOuter = Get-Sha256 $outerPath
  Assert-Check 'outer_sha_pin' ($observedOuter -eq $ExpectedOuterSha256.ToLowerInvariant()) $observedOuter
  $expectedBridge = (Get-LiteralAssignment $outerAst 'ExpectedBridgeSha256').ToLowerInvariant()
  $observedBridge = Get-Sha256 $bridgePath
  Assert-Check 'outer_to_bridge_sha_pin' ($observedBridge -eq $expectedBridge) $observedBridge
  $expectedManifest = (Get-LiteralAssignment $bridgeAst 'ExpectedManifestSha256').ToLowerInvariant()
  $observedManifest = Get-Sha256 $manifestPath
  Assert-Check 'bridge_to_manifest_sha_pin' ($observedManifest -eq $expectedManifest) $observedManifest

  $outerBridgeMarker = '# R5_BRIDGE_INVOKE_EXACTLY_ONCE'
  $bridgeRunnerMarker = '# R5_RUNNER_INVOKE_EXACTLY_ONCE'
  Assert-Check 'outer_exact_one_bridge_marker' (($outerText.Split(@($outerBridgeMarker), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  Assert-Check 'bridge_exact_one_runner_marker' (($bridgeText.Split(@($bridgeRunnerMarker), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  Assert-Check 'outer_ast_exact_one_bridge_invocation' ((Get-AmpersandInvocationCount $outerAst 'bridgePath') -eq 1) 'one'
  Assert-Check 'bridge_ast_exact_one_runner_invocation' ((Get-AmpersandInvocationCount $bridgeAst 'PythonPath') -eq 1) 'one'
  $outerInvokeIndex = $outerText.IndexOf($outerBridgeMarker, [StringComparison]::Ordinal)
  $runnerInvokeIndex = $bridgeText.IndexOf($bridgeRunnerMarker, [StringComparison]::Ordinal)
  Assert-Check 'outer_self_hash_guard_before_bridge' (
    $outerText.Contains('if ($outerObserved -ne $outerExpected) { throw ''outer_sha256_mismatch'' }') -and
    $outerText.IndexOf('outer_sha256_mismatch', [StringComparison]::Ordinal) -lt $outerInvokeIndex
  ) 'outer guard precedes bridge'
  Assert-Check 'outer_bridge_hash_guard_before_bridge' (
    $outerText.Contains('if ($bridgeObserved -ne $ExpectedBridgeSha256) { throw ''bridge_sha256_mismatch'' }') -and
    $outerText.IndexOf('bridge_sha256_mismatch', [StringComparison]::Ordinal) -lt $outerInvokeIndex
  ) 'bridge guard precedes bridge'

  Assert-Check 'manifest_schema' ($manifest.schema_version -eq 'evm.s8_v4.x1_phase_b2_r5_work_order.v1') $manifest.schema_version
  $revision = ([string]$manifest.canonical_revision).ToLowerInvariant()
  $tree = ([string]$manifest.canonical_tree).ToLowerInvariant()
  Assert-Check 'manifest_revision_full' ($revision -cmatch '^[0-9a-f]{40}$') $revision
  Assert-Check 'manifest_tree_full' ($tree -cmatch '^[0-9a-f]{40}$') $tree
  Assert-Check 'old_e48_revision_rejected' (
    $revision -ne $oldR4Revision -and -not $revision.StartsWith($oldR4RevisionPrefix, [StringComparison]::OrdinalIgnoreCase)
  ) $revision
  # The restore checkpoint is intentionally an immutable r4 artifact and its
  # historical directory name may contain e48c1d8. Remove only those two
  # parsed path literals before proving that no executable/runtime pin reuses
  # the old revision.
  $historicalCheckpointPath = Get-LiteralAssignment $bridgeAst 'CheckpointPath'
  $historicalCheckpointIndexPath = Get-LiteralAssignment $bridgeAst 'CheckpointIndexPath'
  $bridgeWithoutHistoricalCheckpointPaths = $bridgeText.Replace($historicalCheckpointPath, '')
  $bridgeWithoutHistoricalCheckpointPaths = $bridgeWithoutHistoricalCheckpointPaths.Replace($historicalCheckpointIndexPath, '')
  Assert-Check 'old_e48_runtime_pin_absent_from_launchers' (
    $outerText.IndexOf($oldR4RevisionPrefix, [StringComparison]::OrdinalIgnoreCase) -lt 0 -and
    $bridgeWithoutHistoricalCheckpointPaths.IndexOf($oldR4RevisionPrefix, [StringComparison]::OrdinalIgnoreCase) -lt 0
  ) 'absent except immutable checkpoint paths'

  $mode = [string](Get-PropertyValue $manifest 'execution_mode')
  Assert-Check 'execution_mode_explicit' ($mode -cin @('restore-only', 'fresh')) $mode
  Assert-Check 'requested_mode_matches_manifest' ($Mode -ceq $mode) @{ requested = $Mode; manifest = $mode }
  if ($PreExecution) {
    Assert-Check 'outer_reservation_pid_positive' ([int64]$reservation.pid -gt 0) $reservation.pid
    Assert-Check 'outer_reservation_created_at_present' (-not [string]::IsNullOrWhiteSpace([string]$reservation.created_at)) $reservation.created_at
    Assert-Check 'outer_reservation_output_matches_manifest' (
      [IO.Path]::GetFullPath([string]$reservation.output_directory) -eq
      [IO.Path]::GetFullPath([string]$manifest.output.path)
    ) $reservation.output_directory
  }
  Assert-Check 'bridge_mode_guard' ($bridgeText.Contains('if ([string]$manifest.execution_mode -ne $Mode) { throw ''manifest_execution_mode_mismatch'' }')) 'exact guard'
  Assert-Check 'outer_mode_validate_set' ([regex]::IsMatch($outerText, "(?i)\[ValidateSet\(\s*'restore-only'\s*,\s*'fresh'\s*\)\]")) 'restore-only|fresh'
  Assert-Check 'bridge_mode_validate_set' ([regex]::IsMatch($bridgeText, "(?i)\[ValidateSet\(\s*'restore-only'\s*,\s*'fresh'\s*\)\]")) 'restore-only|fresh'

  Assert-Check 'manifest_repository_path' ([string]$manifest.repository.path -eq $repo) $manifest.repository.path
  Assert-Check 'manifest_python_path' ([string]$manifest.runtime.python -eq $pythonPath) $manifest.runtime.python
  Assert-Check 'manifest_local_origin_remote_equal' ($manifest.repository.local_origin_remote_equal -eq $true) $manifest.repository.local_origin_remote_equal
  Assert-Check 'bridge_repository_path_pin' (
    [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'RepositoryRoot')) -eq $repo
  ) $repo
  Assert-Check 'bridge_python_path_pin' (
    [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'PythonPath')) -eq $pythonPath
  ) $pythonPath
  Assert-Check 'bridge_branch_pin' (
    (Get-LiteralAssignment $bridgeAst 'ExpectedBranch') -ceq [string]$manifest.repository.branch
  ) $manifest.repository.branch
  Assert-Check 'bridge_untracked_count_pin' (
    [int](Get-LiteralAssignment $bridgeAst 'ExpectedUntrackedCount') -eq [int]$manifest.repository.preserved_untracked_count
  ) $manifest.repository.preserved_untracked_count
  $gitTop = [IO.Path]::GetFullPath((Invoke-GitRead @('rev-parse', '--show-toplevel')))

  $componentSpecs = @(
    @{ name = 'core'; sha_literal = 'ExpectedCoreSha256'; path_literal = 'CorePath' },
    @{ name = 'process'; sha_literal = 'ExpectedProcessSha256'; path_literal = 'ProcessPath' },
    @{ name = 'fresh'; sha_literal = 'ExpectedFreshSha256'; path_literal = 'FreshPath' },
    @{ name = 'runner'; sha_literal = 'ExpectedRunnerSha256'; path_literal = 'RunnerPath' },
    @{ name = 'validator'; sha_literal = 'ExpectedValidatorSha256'; path_literal = 'ValidatorPath' }
  )
  $componentTexts = [ordered]@{}
  $componentHashes = [ordered]@{}
  foreach ($spec in $componentSpecs) {
    $name = [string]$spec.name
    $runtimeEntry = Get-PropertyValue $manifest.runtime $name
    $pathValue = [string](Get-PropertyValue $runtimeEntry 'path')
    $shaValue = ([string](Get-PropertyValue $runtimeEntry 'sha256')).ToLowerInvariant()
    $blobValue = ([string](Get-PropertyValue $runtimeEntry 'blob_oid')).ToLowerInvariant()
    Assert-ExactSha "manifest_${name}_sha_format" $shaValue
    Assert-ExactBlob "manifest_${name}_blob_format" $blobValue
    $componentPath = [IO.Path]::GetFullPath($pathValue)
    Assert-Check "${name}_is_inside_repository" ($componentPath.StartsWith($repo.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $componentPath
    Assert-Check "${name}_exists" (Test-Path -LiteralPath $componentPath -PathType Leaf) $componentPath
    $observedSha = Get-Sha256 $componentPath
    Assert-Check "manifest_${name}_sha" ($observedSha -eq $shaValue) $observedSha
    $observedBlob = Get-GitBlobOid $revision $componentPath $gitTop
    Assert-Check "manifest_${name}_blob" ($observedBlob -eq $blobValue) $observedBlob
    $bridgePinnedPath = [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst ([string]$spec.path_literal)))
    Assert-Check "bridge_${name}_path" ($bridgePinnedPath -eq $componentPath) $bridgePinnedPath
    $bridgePinnedSha = (Get-LiteralAssignment $bridgeAst ([string]$spec.sha_literal)).ToLowerInvariant()
    Assert-Check "bridge_${name}_sha" ($bridgePinnedSha -eq $observedSha) $observedSha
    $guardText = switch ($name) {
      'process' { 'process_sha256_mismatch' }
      'fresh' { 'fresh_sha256_mismatch' }
      'validator' { 'validator_sha256_mismatch' }
      'runner' { 'runner_sha256_mismatch' }
      default { 'core_sha256_mismatch' }
    }
    Assert-Check "bridge_${name}_guard_before_runner" (
      $bridgeText.IndexOf($guardText, [StringComparison]::Ordinal) -ge 0 -and
      $bridgeText.IndexOf($guardText, [StringComparison]::Ordinal) -lt $runnerInvokeIndex
    ) $guardText
    $componentTexts[$name] = [IO.File]::ReadAllText($componentPath)
    $componentHashes[$name] = $observedSha
  }
  Assert-Check 'bridge_revision_pin' ((Get-LiteralAssignment $bridgeAst 'PinnedRevision').ToLowerInvariant() -eq $revision) $revision
  Assert-Check 'bridge_tree_pin' ((Get-LiteralAssignment $bridgeAst 'PinnedTree').ToLowerInvariant() -eq $tree) $tree

  $actualBranch = Invoke-GitRead @('branch', '--show-current')
  $actualRevision = (Invoke-GitRead @('rev-parse', 'HEAD')).ToLowerInvariant()
  $actualTree = (Invoke-GitRead @('rev-parse', 'HEAD^{tree}')).ToLowerInvariant()
  $revisionTree = (Invoke-GitRead @('rev-parse', "$revision^{tree}")).ToLowerInvariant()
  $originRevision = (Invoke-GitRead @('rev-parse', "origin/$($manifest.repository.branch)")).ToLowerInvariant()
  $remoteText = Invoke-GitRead @('ls-remote', 'origin', "refs/heads/$($manifest.repository.branch)")
  $remoteRevision = @($remoteText -split '\s+')[0].ToLowerInvariant()
  $trackedStatus = Invoke-GitRead @('status', '--porcelain=v1', '--untracked-files=no')
  $allStatus = Invoke-GitRead @('status', '--porcelain=v1', '--untracked-files=all')
  $untrackedCount = @($allStatus -split "`r?`n" | Where-Object { $_ -like '?? *' }).Count
  Assert-Check 'git_branch_pin' ($actualBranch -eq [string]$manifest.repository.branch) $actualBranch
  Assert-Check 'git_revision_pin' ($actualRevision -eq $revision) $actualRevision
  Assert-Check 'git_tree_pin' ($actualTree -eq $tree -and $revisionTree -eq $tree) $actualTree
  Assert-Check 'git_origin_revision_pin' ($originRevision -eq $revision) $originRevision
  Assert-Check 'git_remote_revision_pin' ($remoteRevision -eq $revision) $remoteRevision
  Assert-Check 'git_tracked_clean' ([string]::IsNullOrWhiteSpace($trackedStatus)) $trackedStatus
  Assert-Check 'git_untracked_preserved' ($untrackedCount -eq [int]$manifest.repository.preserved_untracked_count) $untrackedCount

  $timeoutNames = @(
    'kubectl_timeout_seconds', 'wrapper_timeout_seconds',
    'restore_deadline_seconds', 'residual_repoll_seconds', 'stream_drain_seconds'
  )
  $previousPythonPath = $env:PYTHONPATH
  $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
  try {
    $env:PYTHONPATH = Join-Path $repo 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $runtimeOutput = @(& $pythonPath -c 'import json;from dataclasses import asdict;from evm.scale_validation.phase_b2_r5_process import TimeoutContract;print(json.dumps(asdict(TimeoutContract()),sort_keys=True))' 2>&1)
    if ($LASTEXITCODE -ne 0) {
      throw "bundle_validation_failed:runtime_timeout_contract_read:$($runtimeOutput -join [Environment]::NewLine)"
    }
    $runtimeTimeout = (($runtimeOutput -join [Environment]::NewLine).Trim() | ConvertFrom-Json -ErrorAction Stop)
  }
  finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
  }
  foreach ($name in $timeoutNames) {
    $manifestValue = [double](Get-PropertyValue $manifest.timeout_contract $name)
    $runtimeValue = [double](Get-PropertyValue $runtimeTimeout $name)
    Assert-Check "timeout_${name}_finite_positive" (
      -not [double]::IsNaN($manifestValue) -and
      -not [double]::IsInfinity($manifestValue) -and
      $manifestValue -gt 0
    ) $manifestValue
    Assert-Check "manifest_runtime_timeout_match_$name" ($manifestValue -eq $runtimeValue) @{ manifest = $manifestValue; runtime = $runtimeValue }
  }
  Assert-Check 'nested_timeout_order' (
    [double]$manifest.timeout_contract.kubectl_timeout_seconds -lt [double]$manifest.timeout_contract.wrapper_timeout_seconds -and
    [double]$manifest.timeout_contract.wrapper_timeout_seconds -lt [double]$manifest.timeout_contract.restore_deadline_seconds
  ) $manifest.timeout_contract
  Assert-Check 'residual_repoll_exact_120' (
    [double]$manifest.timeout_contract.residual_repoll_seconds -eq 120.0 -and
    [double]$runtimeTimeout.residual_repoll_seconds -eq 120.0
  ) 'manifest=runtime=120'
  Assert-Check 'stream_drain_exact_5' ([double]$manifest.timeout_contract.stream_drain_seconds -eq 5.0) $manifest.timeout_contract.stream_drain_seconds
  Assert-Check 'residual_budget_within_restore_deadline' (
    ([double]$manifest.timeout_contract.wrapper_timeout_seconds +
      [double]$manifest.timeout_contract.residual_repoll_seconds +
      [double]$manifest.timeout_contract.stream_drain_seconds) -lt
    [double]$manifest.timeout_contract.restore_deadline_seconds
  ) 'wrapper+residual+drain < restore deadline'
  Assert-Check 'process_containment_residual_exact' ([double]$manifest.process_containment.residual_repoll_seconds -eq 120.0) $manifest.process_containment.residual_repoll_seconds
  Assert-Check 'process_containment_no_forced_termination' (
    [string]$manifest.process_containment.provider -ceq 'windows_job_object' -and
    $manifest.process_containment.create_suspended -eq $true -and
    $manifest.process_containment.assign_before_resume -eq $true -and
    $manifest.process_containment.breakaway_allowed -eq $false -and
    $manifest.process_containment.kill_on_job_close -eq $false -and
    $manifest.process_containment.terminate_job_object_allowed -eq $false -and
    [int64]$manifest.process_containment.force_termination_attempts -eq 0
  ) $manifest.process_containment

  $b0Uid = [string]$manifest.expected_state.b0.uid
  Assert-Check 'b0_uid_well_formed' ($b0Uid -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') $b0Uid
  Assert-Check 'b0_uid_exact' ($b0Uid -ceq 'cfdab424-dcc5-4d5f-a46f-ae7530441ef4') $b0Uid
  Assert-Check 'b0_image_exact' (
    [string]$manifest.expected_state.b0.image -ceq 'enterprise-vision-mlops-efficientnet-serving@sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a'
  ) $manifest.expected_state.b0.image

  $phase = $manifest.phase_b2_contract
  Assert-Check 'fresh_mode_docker_off' ([string]$phase.mode -ceq 'docker-off') $phase.mode
  Assert-Check 'fresh_duration_180' ([int]$phase.duration_seconds -eq 180) $phase.duration_seconds
  Assert-Check 'fresh_cadence_100' ([int]$phase.cadence_ms -eq 100) $phase.cadence_ms
  Assert-Check 'fresh_samples_1800_each' (
    [int]$phase.windows_samples -eq 1800 -and [int]$phase.wsl_samples -eq 1800
  ) @{ windows = $phase.windows_samples; wsl = $phase.wsl_samples }
  Assert-Check 'fresh_maximum_invocations_one' ([int]$phase.maximum_invocations -eq 1) $phase.maximum_invocations
  Assert-Check 'fresh_raw_samples_required' ($phase.raw_samples_required -eq $true) $phase.raw_samples_required
  Assert-Check 'restore_report_synthesis_forbidden' ($phase.restore_report_synthesis_forbidden -eq $true) $phase.restore_report_synthesis_forbidden
  foreach ($name in @(
    'windows_discontinuity', 'wsl_discontinuity', 'backward_step',
    'unclassified_gap', 'bracket_violation', 'residual_pid'
  )) {
    Assert-Check "fresh_expected_zero_$name" ([int64](Get-PropertyValue $phase $name) -eq 0) (Get-PropertyValue $phase $name)
  }

  $callNames = @('docker_off_probe', 'compose_stop', 'desktop_stop', 'wsl_shutdown', 'desktop_start', 'compose_start')
  $modeCalls = Get-PropertyValue $manifest.call_contract $mode
  $expectedCalls = if ($mode -eq 'restore-only') {
    [ordered]@{ docker_off_probe = 0; compose_stop = 0; desktop_stop = 0; wsl_shutdown = 0; desktop_start = 0; compose_start = 0 }
  }
  else {
    [ordered]@{ docker_off_probe = 1; compose_stop = 1; desktop_stop = 1; wsl_shutdown = 0; desktop_start = 1; compose_start = 1 }
  }
  foreach ($name in $callNames) {
    Assert-Check "${mode}_call_exact_$name" ([int](Get-PropertyValue $modeCalls $name) -eq [int]$expectedCalls[$name]) (Get-PropertyValue $modeCalls $name)
  }
  foreach ($name in @('outer', 'bridge', 'runner')) {
    Assert-Check "invocation_exact_one_$name" ([int](Get-PropertyValue $manifest.call_contract.launcher $name) -eq 1) (Get-PropertyValue $manifest.call_contract.launcher $name)
  }
  Assert-Check 'automatic_retry_zero' ([int]$manifest.call_contract.launcher.automatic_retry -eq 0) $manifest.call_contract.launcher.automatic_retry
  foreach ($name in @('full_stack_3180', 'q0', 'calibration_54', 'matrix_78', 'integrated_v4', 'etw')) {
    Assert-Check "downstream_call_zero_$name" (
      [int](Get-PropertyValue $manifest.call_contract.downstream $name) -eq 0
    ) (Get-PropertyValue $manifest.call_contract.downstream $name)
  }

  $expectedCheckpointKind = if ($mode -eq 'restore-only') { 'r4_failure_seal' } else { 'r5_restore_only_index' }
  $expectedCheckpointLeaves = if ($mode -eq 'restore-only') {
    @('failure-seal.json', 'failure-evidence-index.json')
  }
  else {
    @('restore-only-report.json', 'restore-only-index.json')
  }
  Assert-Check 'checkpoint_kind_exact' ([string]$manifest.checkpoint.kind -ceq $expectedCheckpointKind) $manifest.checkpoint.kind
  Assert-Check 'checkpoint_immutable_nonexecuting' (
    $manifest.checkpoint.immutable -eq $true -and $manifest.checkpoint.must_not_execute -eq $true
  ) $manifest.checkpoint
  $checkpointPaths = [Collections.Generic.List[string]]::new()
  $checkpointEntries = @(
    @{
      name = 'primary'
      value = $manifest.checkpoint
      leaf = $expectedCheckpointLeaves[0]
      path_literal = 'CheckpointPath'
      sha_literal = 'ExpectedCheckpointSha256'
      guard = 'checkpoint_sha256_mismatch'
    },
    @{
      name = 'companion_index'
      value = (Get-PropertyValue $manifest.checkpoint 'companion_index')
      leaf = $expectedCheckpointLeaves[1]
      path_literal = 'CheckpointIndexPath'
      sha_literal = 'ExpectedCheckpointIndexSha256'
      guard = 'checkpoint_index_sha256_mismatch'
    }
  )
  foreach ($checkpointSpec in $checkpointEntries) {
    $name = [string]$checkpointSpec.name
    $checkpointEntry = $checkpointSpec.value
    $checkpointPath = [IO.Path]::GetFullPath([string](Get-PropertyValue $checkpointEntry 'path'))
    $checkpointSha = ([string](Get-PropertyValue $checkpointEntry 'sha256')).ToLowerInvariant()
    $expectedLeaf = [string]$checkpointSpec.leaf
    Assert-ExactSha "checkpoint_${name}_sha_format" $checkpointSha
    Assert-Check "checkpoint_${name}_leaf_exact" ([IO.Path]::GetFileName($checkpointPath) -ceq $expectedLeaf) $checkpointPath
    Assert-Check "checkpoint_${name}_exists" (Test-Path -LiteralPath $checkpointPath -PathType Leaf) $checkpointPath
    Assert-Check "checkpoint_${name}_outside_bundle" (-not $checkpointPath.StartsWith($bundle.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $checkpointPath
    Assert-Check "checkpoint_${name}_sha" ((Get-Sha256 $checkpointPath) -eq $checkpointSha) $checkpointSha
    $bridgeCheckpointPath = [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst ([string]$checkpointSpec.path_literal)))
    $bridgeCheckpointSha = (Get-LiteralAssignment $bridgeAst ([string]$checkpointSpec.sha_literal)).ToLowerInvariant()
    Assert-Check "bridge_checkpoint_${name}_path" ($bridgeCheckpointPath -eq $checkpointPath) $bridgeCheckpointPath
    Assert-Check "bridge_checkpoint_${name}_sha" ($bridgeCheckpointSha -eq $checkpointSha) $bridgeCheckpointSha
    Assert-Check "bridge_checkpoint_${name}_guard_before_runner" (
      $bridgeText.IndexOf([string]$checkpointSpec.guard, [StringComparison]::Ordinal) -ge 0 -and
      $bridgeText.IndexOf([string]$checkpointSpec.guard, [StringComparison]::Ordinal) -lt $runnerInvokeIndex
    ) $checkpointSpec.guard
    [void]$checkpointPaths.Add($checkpointPath)
  }
  Assert-Check 'checkpoint_primary_index_distinct' ($checkpointPaths.Count -eq 2 -and $checkpointPaths[0] -ne $checkpointPaths[1]) $checkpointPaths
  Assert-Check 'bridge_checkpoint_index_in_sha_chain' (
    [regex]::IsMatch($bridgeText, '(?i)checkpoint_index\s*=\s*Get-Sha256\s+\$CheckpointIndexPath')
  ) 'checkpoint_index=Get-Sha256 $CheckpointIndexPath'

  Assert-Check 'evidence_create_exclusive' ([string]$manifest.evidence.write_mode -ceq 'create-exclusive') $manifest.evidence.write_mode
  Assert-Check 'output_create_exclusive' ([string]$manifest.output.write_mode -ceq 'create-exclusive') $manifest.output.write_mode
  Assert-Check 'output_must_not_exist' ($manifest.output.must_not_exist_before_runner -eq $true) $manifest.output.must_not_exist_before_runner
  $outputPath = [IO.Path]::GetFullPath([string]$manifest.output.path)
  Assert-Check 'output_path_currently_absent' (-not (Test-Path -LiteralPath $outputPath)) $outputPath
  Assert-Check 'failure_forbids_completion_marker' ($manifest.evidence.failure_creates_completion_marker -eq $false) $manifest.evidence.failure_creates_completion_marker
  # The validator contains the forbidden-pattern literals by design, so only
  # executable harness sources participate in the destructive-command scan.
  $runtimeSource = @(
    $componentTexts.core,
    $componentTexts.process,
    $componentTexts.fresh,
    $componentTexts.runner
  ) -join "`n"
  Assert-Check 'runtime_uses_exclusive_create' ($runtimeSource.Contains('os.O_EXCL')) 'os.O_EXCL'
  Assert-Check 'runtime_omits_truncating_create' (-not $runtimeSource.Contains('os.O_TRUNC')) 'os.O_TRUNC absent'
  $createNewPattern = '(?i)\[(?:System\.)?IO\.FileMode\]::CreateNew'
  Assert-Check 'outer_reservation_uses_create_new' (
    [regex]::Matches($outerText, $createNewPattern).Count -eq 1
  ) 'exactly one FileMode.CreateNew'
  Assert-Check 'bridge_reservation_uses_create_new' (
    [regex]::Matches($bridgeText, $createNewPattern).Count -eq 1
  ) 'exactly one FileMode.CreateNew'
  Assert-Check 'outer_reservation_precedes_bridge' (
    $outerText.IndexOf('r5-outer-invocation-reservation.json', [StringComparison]::Ordinal) -ge 0 -and
    $outerText.IndexOf('r5-outer-invocation-reservation.json', [StringComparison]::Ordinal) -lt $outerInvokeIndex
  ) 'outer reservation before bridge invocation'
  Assert-Check 'bridge_validator_exactly_once' (
    (Get-AmpersandInvocationCount $bridgeAst 'ValidatorPath') -eq 1
  ) 'one'
  $validatorInvokeIndex = $bridgeText.IndexOf('& $ValidatorPath', [StringComparison]::Ordinal)
  $bridgeReservationIndex = $bridgeText.IndexOf('Write-CreateNewJson $bridgeReservation', [StringComparison]::Ordinal)
  Assert-Check 'bridge_preexecution_validator_before_reservation' (
    $validatorInvokeIndex -ge 0 -and
    $bridgeText.IndexOf('-PreExecution', $validatorInvokeIndex, [StringComparison]::Ordinal) -ge 0 -and
    $bridgeReservationIndex -gt $validatorInvokeIndex -and
    $bridgeReservationIndex -lt $runnerInvokeIndex
  ) 'validator -> bridge reservation -> runner'

  $combinedSource = $outerText + "`n" + $bridgeText + "`n" + $manifestText + "`n" + $runtimeSource
  $forbiddenPatterns = [ordered]@{
    terminate_job_object = '\bTerminateJobObject\b'
    kill_on_job_close = '\b(?:JOB_OBJECT_LIMIT_)?KILL_ON_JOB_CLOSE\b'
    terminate_process = '\bTerminateProcess\b'
    taskkill = '(?im)\btaskkill(?:\.exe)?\b'
    stop_process_force = '(?im)\bstop-process\b[^\r\n]*\b-force\b'
    python_kill = '(?im)(?:\.kill|\.terminate|os\.kill)\s*\('
    docker_reset_prune_down_up = '(?im)\bdocker(?:\.exe)?\s+(?:(?:compose\s+)?(?:down|up)\b|(?:system\s+)?prune\b|reset\b)'
    wsl_shutdown = '(?im)\bwsl(?:\.exe)?\b[^\r\n]*--shutdown\b'
    wsl_unregister = '(?im)\bwsl(?:\.exe)?\b[^\r\n]*--unregister\b'
    kubectl_delete_reset = '(?im)\bkubectl(?:\.exe)?\s+(?:delete|drain|reset)\b'
    git_reset_clean_checkout = '(?im)\bgit(?:\.exe)?\s+(?:reset|clean|checkout)\b'
    chkdsk = '(?im)\bchkdsk\b'
    python_remove = '(?im)(?:\bos\.(?:remove|unlink|replace)|\bshutil\.rmtree|\.unlink|\.write_text)\s*\('
    truncating_flag = '(?i)\bos\.O_TRUNC\b'
  }
  foreach ($entry in $forbiddenPatterns.GetEnumerator()) {
    Assert-Check "forbidden_absent_$($entry.Key)" (-not [regex]::IsMatch($combinedSource, [string]$entry.Value)) 'absent'
  }
  $forbiddenAstCommands = @(
    'Remove-Item', 'Clear-Content', 'Set-Content', 'Out-File', 'Add-Content',
    'Stop-Process', 'Move-Item', 'Copy-Item', 'Start-Process',
    'Invoke-Expression', 'Invoke-Command'
  )
  foreach ($entry in @(@{ name = 'outer'; ast = $outerAst }, @{ name = 'bridge'; ast = $bridgeAst })) {
    $commandNames = @($entry.ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ })
    foreach ($forbidden in $forbiddenAstCommands) {
      Assert-Check "powershell_ast_forbidden_absent_$($entry.name)_$forbidden" ($commandNames -notcontains $forbidden) $commandNames
    }
  }

  [ordered]@{
    schema_version = 'evm.s8_v4.x1_phase_b2_r5_bundle_validation.v1'
    status = 'PASS'
    validated_at = [DateTime]::UtcNow.ToString('o')
    execution_mode = $mode
    check_count = $checks.Count
    canonical_revision = $revision
    canonical_tree = $tree
    observed_sha256 = [ordered]@{
      outer = $observedOuter
      bridge = $observedBridge
      manifest = $observedManifest
      core = $componentHashes.core
      process = $componentHashes.process
      fresh = $componentHashes.fresh
      runner = $componentHashes.runner
      validator = $componentHashes.validator
    }
    checks = $checks
  } | ConvertTo-Json -Depth 14 -Compress
  exit 0
}
catch {
  [ordered]@{
    schema_version = 'evm.s8_v4.x1_phase_b2_r5_bundle_validation.v1'
    status = 'FAIL'
    validated_at = [DateTime]::UtcNow.ToString('o')
    error = $_.Exception.Message
    passed_check_count = $checks.Count
    checks = $checks
  } | ConvertTo-Json -Depth 14 -Compress
  exit 2
}
