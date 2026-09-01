#Requires -Version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$ManifestPath,
  [Parameter(Mandatory = $true)][string]$OuterPath,
  [Parameter(Mandatory = $true)][string]$BridgePath,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedOuterSha256,
  [switch]$PreExecution
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$checks = [Collections.Generic.List[object]]::new()
$requiredParentRoles = @(
  'r5_failure_seal', 'r5_failure_index', 'r6_compose_rca',
  'r6_failure_seal_amendment', 'r6_final_index',
  'post_manual_on_readback', 'post_manual_on_index'
)
$expectedParentKinds = [ordered]@{
  r5_failure_seal = 'r5_failure_seal'
  r5_failure_index = 'r5_failure_index'
  r6_compose_rca = 'r6_compose_rca'
  r6_failure_seal_amendment = 'r6_failure_seal_amendment'
  r6_final_index = 'r6_final_index'
  post_manual_on_readback = 'post_manual_on_readback'
  post_manual_on_index = 'post_manual_on_index'
}
$longLivedServices = @(
  'airflow-postgres', 'airflow-scheduler', 'airflow-webserver', 'api',
  'control-panel', 'control-plane-postgres', 'grafana', 'minio', 'mlflow',
  'otel-collector', 'postgres', 'prometheus', 'task-queue-worker'
)
$oneShotServices = @('airflow-init', 'minio-create-buckets')

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

function Get-RunnerInvocationCount([Management.Automation.Language.Ast]$Ast) {
  return @($Ast.FindAll({
        param($node)
        if ($node -isnot [Management.Automation.Language.CommandAst] -or
          $node.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Ampersand -or
          $node.CommandElements.Count -lt 2) {
          return $false
        }
        $target = $node.CommandElements[0]
        $runner = $node.CommandElements[1]
        return $target -is [Management.Automation.Language.VariableExpressionAst] -and
          $target.VariablePath.UserPath -eq 'PythonPath' -and
          $runner -is [Management.Automation.Language.VariableExpressionAst] -and
          $runner.VariablePath.UserPath -eq 'RunnerPath'
      }, $true)).Count
}

function Get-AmpersandInvocations([Management.Automation.Language.Ast]$Ast) {
  return @($Ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.CommandAst] -and
        $node.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Ampersand
      }, $true))
}

function Get-CommandElementSemantic([Management.Automation.Language.CommandElementAst]$Element) {
  if ($Element -is [Management.Automation.Language.VariableExpressionAst]) {
    return '$' + $Element.VariablePath.UserPath
  }
  if ($Element -is [Management.Automation.Language.StringConstantExpressionAst]) {
    return [string]$Element.Value
  }
  if ($Element -is [Management.Automation.Language.CommandParameterAst]) {
    return [string]$Element.Extent.Text
  }
  return ([string]$Element.Extent.Text).Trim()
}

function Get-InvocationSignature([Management.Automation.Language.CommandAst]$Command) {
  return (@($Command.CommandElements | ForEach-Object { Get-CommandElementSemantic $_ }) -join [char]31)
}

function Assert-ExactSha([string]$Name, [object]$Value) {
  $text = [string]$Value
  Assert-Check $Name ($text -cmatch '^[0-9a-f]{64}$') $text
}

function Assert-ExactBlob([string]$Name, [object]$Value) {
  $text = [string]$Value
  Assert-Check $Name ($text -cmatch '^[0-9a-f]{40}$') $text
}

function Assert-ExactObjectKeys([string]$Name, [object]$Value, [string[]]$Expected) {
  $actual = @($Value.PSObject.Properties | ForEach-Object { $_.Name })
  $difference = @(Compare-Object -ReferenceObject $Expected -DifferenceObject $actual)
  Assert-Check $Name ($difference.Count -eq 0 -and $actual.Count -eq $Expected.Count) $actual
}

try {
  $manifestPath = [IO.Path]::GetFullPath($ManifestPath)
  $outerPath = [IO.Path]::GetFullPath($OuterPath)
  $bridgePath = [IO.Path]::GetFullPath($BridgePath)
  $bundle = [IO.Path]::GetDirectoryName($manifestPath)
  Assert-Check 'bundle_directory_exists' (Test-Path -LiteralPath $bundle -PathType Container) $bundle
  Assert-Check 'manifest_path_exact' ($manifestPath -eq (Join-Path $bundle 'phase-b2-r7-work-order.json')) $manifestPath
  Assert-Check 'outer_path_exact' ($outerPath -eq (Join-Path $bundle 'invoke-verified-x1-phase-b2-r7.ps1')) $outerPath
  Assert-Check 'bridge_path_exact' ($bridgePath -eq (Join-Path $bundle 'invoke-x1-phase-b2-r7-bridge.ps1')) $bridgePath
  $expectedNames = [Collections.Generic.List[string]]::new()
  foreach ($name in @(
    'invoke-verified-x1-phase-b2-r7.ps1',
    'invoke-x1-phase-b2-r7-bridge.ps1',
    'phase-b2-r7-work-order.json'
  )) { [void]$expectedNames.Add($name) }
  if ($PreExecution) {
    [void]$expectedNames.Add('r7-outer-invocation-reservation.json')
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
    $reservationPath = Join-Path $bundle 'r7-outer-invocation-reservation.json'
    try {
      $reservation = [IO.File]::ReadAllText($reservationPath) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
      throw "bundle_validation_failed:outer_reservation_json:$($_.Exception.Message)"
    }
    Assert-Check 'outer_reservation_schema' ([string]$reservation.schema -eq 's8-v4-x1-phase-b2-r7-outer-reservation/v1') $reservation.schema
    Assert-Check 'outer_reservation_mode' ([string]$reservation.mode -ceq 'restore-only') $reservation.mode
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
  $repo = [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'RepositoryRoot'))
  $projectRoot = [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'ProjectRoot'))
  $script:RepositoryRoot = $repo
  $pythonPath = [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'PythonPath'))
  Assert-Check 'repository_root_exists' (Test-Path -LiteralPath $repo -PathType Container) $repo
  Assert-Check 'project_root_exists' (Test-Path -LiteralPath $projectRoot -PathType Container) $projectRoot
  Assert-Check 'project_root_inside_repository' (
    $projectRoot.StartsWith($repo.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
  ) $projectRoot
  Assert-Check 'python_exists' (Test-Path -LiteralPath $pythonPath -PathType Leaf) $pythonPath

  $observedOuter = Get-Sha256 $outerPath
  Assert-Check 'outer_sha_pin' ($observedOuter -eq $ExpectedOuterSha256.ToLowerInvariant()) $observedOuter
  $expectedBridge = (Get-LiteralAssignment $outerAst 'ExpectedBridgeSha256').ToLowerInvariant()
  $observedBridge = Get-Sha256 $bridgePath
  Assert-Check 'outer_to_bridge_sha_pin' ($observedBridge -eq $expectedBridge) $observedBridge
  $expectedManifest = (Get-LiteralAssignment $bridgeAst 'ExpectedManifestSha256').ToLowerInvariant()
  $observedManifest = Get-Sha256 $manifestPath
  Assert-Check 'bridge_to_manifest_sha_pin' ($observedManifest -eq $expectedManifest) $observedManifest

  $outerBridgeMarker = '# R7_BRIDGE_INVOKE_EXACTLY_ONCE'
  $bridgeRunnerMarker = '# R7_RUNNER_INVOKE_EXACTLY_ONCE'
  Assert-Check 'outer_exact_one_bridge_marker' (($outerText.Split(@($outerBridgeMarker), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  Assert-Check 'bridge_exact_one_runner_marker' (($bridgeText.Split(@($bridgeRunnerMarker), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  $outerAmpersand = @(Get-AmpersandInvocations $outerAst)
  $bridgeAmpersand = @(Get-AmpersandInvocations $bridgeAst)
  $outerExpectedSignature = @(
    '$bridgePath', '-ExpectedOuterSha256', '$outerExpected',
    '-ObservedOuterSha256', '$outerObserved',
    '-ExpectedBridgeSha256FromOuter', '$ExpectedBridgeSha256',
    '-ObservedBridgeSha256', '$bridgeObserved',
    '-OuterLauncherPath', '$outerPath', '-OutputDirectory', '$OutputDirectory'
  ) -join [char]31
  $runnerExpectedSignature = @(
    '$PythonPath', '$RunnerPath', '--manifest', '$ManifestPath',
    '--output-directory', '$OutputDirectory', '--expected-revision', '$PinnedRevision',
    '--launcher-evidence-base64', '$launcherBase64', '--repository-root', '$RepositoryRoot',
    '--mode', 'restore-only'
  ) -join [char]31
  $untrackedExpectedSignature = @('$PythonPath', '-c', '$untrackedProbe', '$RepositoryRoot') -join [char]31
  $validatorExpectedSignature = @(
    '$ValidatorPath', '-ManifestPath', '$ManifestPath', '-OuterPath', '$OuterLauncherPath',
    '-BridgePath', '$PSCommandPath', '-ExpectedOuterSha256', '$outerExpected', '-PreExecution'
  ) -join [char]31
  Assert-Check 'outer_ast_exact_invocation_set' (
    $outerAmpersand.Count -eq 1 -and
    (Get-InvocationSignature $outerAmpersand[0]) -ceq $outerExpectedSignature
  ) @($outerAmpersand | ForEach-Object { Get-InvocationSignature $_ })
  Assert-Check 'bridge_ast_exact_ampersand_invocation_count' ($bridgeAmpersand.Count -eq 5) $bridgeAmpersand.Count
  $bridgeTargets = @($bridgeAmpersand | ForEach-Object { Get-CommandElementSemantic $_.CommandElements[0] })
  $expectedBridgeTargets = @('$PythonPath', '$PythonPath', '$ValidatorPath', 'git.exe', 'whoami.exe')
  Assert-Check 'bridge_ast_exact_ampersand_target_multiset' (
    @(Compare-Object ($expectedBridgeTargets | Sort-Object) ($bridgeTargets | Sort-Object)).Count -eq 0 -and
    $bridgeTargets.Count -eq $expectedBridgeTargets.Count
  ) $bridgeTargets
  $bridgeSignatures = @($bridgeAmpersand | ForEach-Object { Get-InvocationSignature $_ })
  Assert-Check 'bridge_ast_exact_one_runner_invocation' (
    @($bridgeSignatures | Where-Object { $_ -ceq $runnerExpectedSignature }).Count -eq 1
  ) $bridgeSignatures
  Assert-Check 'bridge_ast_exact_one_untracked_probe_invocation' (
    @($bridgeSignatures | Where-Object { $_ -ceq $untrackedExpectedSignature }).Count -eq 1
  ) $bridgeSignatures
  Assert-Check 'bridge_ast_exact_one_validator_invocation' (
    @($bridgeSignatures | Where-Object { $_ -ceq $validatorExpectedSignature }).Count -eq 1
  ) $bridgeSignatures
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

  $outerReservationWriteIndex = $outerText.IndexOf('$stream = [IO.File]::Open($reservation', [StringComparison]::Ordinal)
  $outerImmediateSelfIndex = $outerText.IndexOf('outer_sha256_mismatch_immediate', [StringComparison]::Ordinal)
  $outerImmediateBridgeIndex = $outerText.IndexOf('bridge_sha256_mismatch_immediate', [StringComparison]::Ordinal)
  Assert-Check 'outer_immediate_rehash_after_reservation_before_bridge' (
    $outerReservationWriteIndex -ge 0 -and
    $outerImmediateSelfIndex -gt $outerReservationWriteIndex -and
    $outerImmediateBridgeIndex -gt $outerImmediateSelfIndex -and
    $outerImmediateBridgeIndex -lt $outerInvokeIndex -and
    $outerText.Contains("if (`$outerObserved -ne `$outerExpected) { throw 'outer_sha256_mismatch_immediate' }") -and
    $outerText.Contains("if (`$bridgeObserved -ne `$ExpectedBridgeSha256) { throw 'bridge_sha256_mismatch_immediate' }")
  ) 'reservation -> immediate outer/bridge rehash -> bridge invocation'

  Assert-Check 'manifest_schema' ($manifest.schema_version -eq 'evm.s8_v4.x1_phase_b2_r7_restore_work_order.v1') $manifest.schema_version
  $revision = ([string]$manifest.canonical_revision).ToLowerInvariant()
  $tree = ([string]$manifest.canonical_tree).ToLowerInvariant()
  Assert-Check 'manifest_revision_full' ($revision -cmatch '^[0-9a-f]{40}$') $revision
  Assert-Check 'manifest_tree_full' ($tree -cmatch '^[0-9a-f]{40}$') $tree
  $mode = [string](Get-PropertyValue $manifest 'execution_mode')
  Assert-Check 'execution_mode_restore_only_exact' ($mode -ceq 'restore-only') $mode
  Assert-Check 'bundle_path_exact' (
    [IO.Path]::GetFullPath([string]$manifest.bundle.path) -eq [IO.Path]::GetFullPath($bundle)
  ) $manifest.bundle.path
  Assert-ExactObjectKeys 'bundle_keys_exact' $manifest.bundle @('path')
  Assert-Check 'bundle_run_id_present' (-not [string]::IsNullOrWhiteSpace([string]$manifest.bundle_id)) $manifest.bundle_id
  Assert-Check 'outer_run_id_pin' (
    (Get-LiteralAssignment $outerAst 'PinnedRunId') -ceq [string]$manifest.bundle_id
  ) (Get-LiteralAssignment $outerAst 'PinnedRunId')
  Assert-Check 'bridge_run_id_pin' (
    (Get-LiteralAssignment $bridgeAst 'PinnedRunId') -ceq [string]$manifest.bundle_id
  ) (Get-LiteralAssignment $bridgeAst 'PinnedRunId')
  if ($PreExecution) {
    Assert-Check 'outer_reservation_pid_positive' ([int64]$reservation.pid -gt 0) $reservation.pid
    Assert-Check 'outer_reservation_created_at_present' (-not [string]::IsNullOrWhiteSpace([string]$reservation.created_at)) $reservation.created_at
    Assert-Check 'outer_reservation_output_matches_manifest' (
      [IO.Path]::GetFullPath([string]$reservation.output_directory) -eq
      [IO.Path]::GetFullPath([string]$manifest.output.path)
    ) $reservation.output_directory
    Assert-Check 'outer_reservation_run_id_matches_manifest' ([string]$reservation.run_id -ceq [string]$manifest.bundle_id) $reservation.run_id
  }
  Assert-Check 'bridge_mode_guard' ($bridgeText.Contains("if ([string]`$manifest.execution_mode -ne 'restore-only') { throw 'manifest_execution_mode_mismatch' }")) 'restore-only exact guard'
  Assert-Check 'fresh_mode_absent_from_launchers' (
    $outerText.IndexOf("'fresh'", [StringComparison]::OrdinalIgnoreCase) -lt 0 -and
    $bridgeText.IndexOf("'fresh'", [StringComparison]::OrdinalIgnoreCase) -lt 0
  ) 'absent'
  foreach ($oldLeaf in @(
    'invoke-verified-x1-phase-b2-r3.ps1', 'invoke-verified-x1-phase-b2-r4.ps1', 'invoke-verified-x1-phase-b2-r5.ps1',
    'invoke-x1-phase-b2-r3-bridge.ps1', 'invoke-x1-phase-b2-r4-bridge.ps1', 'invoke-x1-phase-b2-r5-bridge.ps1',
    'run_x1_phase_b2_r3.py', 'run_x1_phase_b2_r4.py', 'run_x1_phase_b2_r5.py',
    'phase_b2_r3.py', 'phase_b2_r4.py', 'phase_b2_r5.py'
  )) {
    Assert-Check "old_executable_leaf_absent_$oldLeaf" (
      $outerText.IndexOf($oldLeaf, [StringComparison]::OrdinalIgnoreCase) -lt 0 -and
      $bridgeText.IndexOf($oldLeaf, [StringComparison]::OrdinalIgnoreCase) -lt 0
    ) 'absent'
  }

  Assert-ExactObjectKeys 'repository_keys_exact' $manifest.repository @(
    'preserved_untracked_count','untracked_path_set_sha256',
    'untracked_path_set_encoding','tracked_changes'
  )
  Assert-Check 'repository_untracked_encoding_exact' (
    [string]$manifest.repository.untracked_path_set_encoding -ceq
    'ordinal-sorted UTF-8 paths, each NUL-terminated'
  ) $manifest.repository.untracked_path_set_encoding
  Assert-Check 'repository_tracked_changes_zero' ([int]$manifest.repository.tracked_changes -eq 0) $manifest.repository.tracked_changes
  Assert-Check 'bridge_repository_path_pin' (
    [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'RepositoryRoot')) -eq $repo
  ) $repo
  Assert-Check 'bridge_project_path_pin' (
    [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'ProjectRoot')) -eq $projectRoot
  ) $projectRoot
  Assert-Check 'bridge_python_path_pin' (
    [IO.Path]::GetFullPath((Get-LiteralAssignment $bridgeAst 'PythonPath')) -eq $pythonPath
  ) $pythonPath
  $expectedBranch = Get-LiteralAssignment $bridgeAst 'ExpectedBranch'
  Assert-Check 'bridge_branch_pin_nonempty' (-not [string]::IsNullOrWhiteSpace($expectedBranch)) $expectedBranch
  Assert-Check 'bridge_untracked_count_pin' (
    [int](Get-LiteralAssignment $bridgeAst 'ExpectedUntrackedCount') -eq [int]$manifest.repository.preserved_untracked_count
  ) $manifest.repository.preserved_untracked_count
  Assert-Check 'bridge_untracked_digest_pin' (
    (Get-LiteralAssignment $bridgeAst 'ExpectedUntrackedDigestSha256').ToLowerInvariant() -eq
    ([string]$manifest.repository.untracked_path_set_sha256).ToLowerInvariant()
  ) $manifest.repository.untracked_path_set_sha256
  $gitTop = [IO.Path]::GetFullPath((Invoke-GitRead @('rev-parse', '--show-toplevel')))

  $runtimeNames = @($manifest.runtime.PSObject.Properties | ForEach-Object { $_.Name })
  $runtimeExpectedNames = @('builder', 'core', 'process', 'runner', 'validator', 'docker_compose')
  Assert-Check 'runtime_component_set_exact' (
    @(Compare-Object $runtimeExpectedNames $runtimeNames).Count -eq 0 -and $runtimeNames.Count -eq $runtimeExpectedNames.Count
  ) $runtimeNames
  $componentSpecs = @(
    @{ name = 'builder'; sha_literal = 'ExpectedBuilderSha256'; path_literal = 'BuilderPath' },
    @{ name = 'core'; sha_literal = 'ExpectedCoreSha256'; path_literal = 'CorePath' },
    @{ name = 'process'; sha_literal = 'ExpectedProcessSha256'; path_literal = 'ProcessPath' },
    @{ name = 'runner'; sha_literal = 'ExpectedRunnerSha256'; path_literal = 'RunnerPath' },
    @{ name = 'validator'; sha_literal = 'ExpectedValidatorSha256'; path_literal = 'ValidatorPath' },
    @{ name = 'docker_compose'; sha_literal = 'ExpectedDockerComposeSha256'; path_literal = 'DockerComposePath' }
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
      default { "${name}_sha256_mismatch" }
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
  $originRevision = (Invoke-GitRead @('rev-parse', "origin/$expectedBranch")).ToLowerInvariant()
  $remoteText = Invoke-GitRead @('ls-remote', 'origin', "refs/heads/$expectedBranch")
  $remoteRevision = @($remoteText -split '\s+')[0].ToLowerInvariant()
  $trackedStatus = Invoke-GitRead @('status', '--porcelain=v1', '--untracked-files=no')
  $untrackedProbeCode = @'
import hashlib,json,subprocess,sys
raw=subprocess.run(['git','-C',sys.argv[1],'-c','core.quotepath=false','ls-files','--others','--exclude-standard','-z'],check=True,capture_output=True).stdout
parts=raw.split(b'\0'); parts=parts[:-1] if parts and parts[-1]==b'' else parts
paths=sorted(item.decode('utf-8','strict') for item in parts)
digest=hashlib.sha256()
for item in paths: digest.update(item.encode('utf-8')); digest.update(b'\0')
print(json.dumps({'count':len(paths),'sha256':digest.hexdigest()},sort_keys=True))
'@
  $untrackedOutput = @(& $pythonPath -c $untrackedProbeCode $repo 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "bundle_validation_failed:untracked_probe:$($untrackedOutput -join [Environment]::NewLine)" }
  $untrackedIdentity = (($untrackedOutput -join [Environment]::NewLine).Trim() | ConvertFrom-Json -ErrorAction Stop)
  $untrackedCount = [int]$untrackedIdentity.count
  $untrackedDigest = ([string]$untrackedIdentity.sha256).ToLowerInvariant()
  Assert-Check 'git_branch_pin' ($actualBranch -eq $expectedBranch) $actualBranch
  Assert-Check 'git_revision_pin' ($actualRevision -eq $revision) $actualRevision
  Assert-Check 'git_tree_pin' ($actualTree -eq $tree -and $revisionTree -eq $tree) $actualTree
  Assert-Check 'git_origin_revision_pin' ($originRevision -eq $revision) $originRevision
  Assert-Check 'git_remote_revision_pin' ($remoteRevision -eq $revision) $remoteRevision
  Assert-Check 'git_tracked_clean' ([string]::IsNullOrWhiteSpace($trackedStatus)) $trackedStatus
  Assert-Check 'git_untracked_preserved' ($untrackedCount -eq [int]$manifest.repository.preserved_untracked_count) $untrackedCount
  Assert-Check 'git_untracked_digest_preserved' (
    $untrackedDigest -eq ([string]$manifest.repository.untracked_path_set_sha256).ToLowerInvariant()
  ) $untrackedDigest
  Assert-Check 'manifest_repository_identity_explicit' (
    [int]$manifest.repository.tracked_changes -eq 0 -and
    [string]$manifest.repository.untracked_path_set_encoding -ceq
      'ordinal-sorted UTF-8 paths, each NUL-terminated'
  ) $manifest.repository

  Assert-Check 'compose_stability_exact' (
    [int]$manifest.expected_state.compose.stability.duration_seconds -eq 300 -and
    [int]$manifest.expected_state.compose.stability.interval_seconds -eq 5 -and
    [int]$manifest.expected_state.compose.stability.samples -eq 61 -and
    [int]$manifest.expected_state.compose.stability.restart_delta -eq 0
  ) $manifest.expected_state.compose.stability
  Assert-Check 'kubernetes_health_confirmation_samples_exact' (
    [int]$manifest.expected_state.kubernetes.health_confirmation_samples -eq 2
  ) $manifest.expected_state.kubernetes.health_confirmation_samples
  Assert-Check 'parent_checkpoint_count_exact' (
    @($manifest.parent_checkpoints).Count -eq 7
  ) @($manifest.parent_checkpoints).Count
  Assert-Check 'collector_call_contract_count_mismatch' (
    [int]$manifest.call_contract.collectors.windows_fresh_collector -eq 0 -and
    [int]$manifest.call_contract.collectors.wsl_fresh_collector -eq 0
  ) $manifest.call_contract.collectors

  $coreValidationProbe = @'
import inspect,json,pathlib,sys
manifest=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
from evm.scale_validation.phase_b2_r7 import validate_r7_manifest
kwargs={
  'expected_revision':sys.argv[3],
  'repository_root':pathlib.Path(sys.argv[2]),
  'expected_untracked_path_set_sha256':sys.argv[4],
}
if 'verify_attestations' in inspect.signature(validate_r7_manifest).parameters:
  kwargs['verify_attestations']=True
validate_r7_manifest(manifest,**kwargs)
print('PASS')
'@
  $previousPythonPath = $env:PYTHONPATH
  $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
  try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $coreValidationOutput = @(
      & $pythonPath -c $coreValidationProbe $manifestPath $repo $revision $untrackedDigest 2>&1
    )
    Assert-Check 'core_validate_r7_manifest_integration' (
      $LASTEXITCODE -eq 0 -and ($coreValidationOutput -join '').Trim() -ceq 'PASS'
    ) ($coreValidationOutput -join [Environment]::NewLine)
  }
  finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
  }

  $timeoutNames = @(
    'kubectl_timeout_seconds', 'wrapper_timeout_seconds',
    'restore_deadline_seconds', 'residual_repoll_seconds', 'stream_drain_seconds'
  )
  $previousPythonPath = $env:PYTHONPATH
  $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
  try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $runtimeOutput = @(& $pythonPath -c 'import json;from dataclasses import asdict;from evm.scale_validation.phase_b2_r7_process import TimeoutContract;print(json.dumps(asdict(TimeoutContract()),sort_keys=True))' 2>&1)
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

  $compose = Get-PropertyValue $manifest.expected_state 'compose'
  Assert-ExactObjectKeys 'compose_keys_exact' $compose @(
    'project_name', 'config_path', 'config_sha256', 'long_lived_services',
    'one_shot_services', 'service_pins', 'stability'
  )
  Assert-Check 'compose_project_exact' ([string]$compose.project_name -ceq 'enterprise-vision-mlops') $compose.project_name
  $composePath = [IO.Path]::GetFullPath([string]$compose.config_path)
  Assert-Check 'compose_path_matches_runtime' (
    $composePath -eq [IO.Path]::GetFullPath([string]$manifest.runtime.docker_compose.path)
  ) $composePath
  Assert-Check 'compose_sha_matches_runtime' (
    ([string]$compose.config_sha256).ToLowerInvariant() -eq ([string]$manifest.runtime.docker_compose.sha256).ToLowerInvariant()
  ) $compose.config_sha256
  Assert-Check 'compose_long_lived_exact' (
    [string]::Join('|', @($compose.long_lived_services)) -ceq [string]::Join('|', $longLivedServices)
  ) $compose.long_lived_services
  Assert-Check 'compose_one_shot_exact' (
    [string]::Join('|', @($compose.one_shot_services)) -ceq [string]::Join('|', $oneShotServices)
  ) $compose.one_shot_services
  $expectedContainerNames = [ordered]@{
    'airflow-postgres'='evm-airflow-postgres'; 'airflow-scheduler'='evm-airflow-scheduler';
    'airflow-webserver'='evm-airflow-webserver'; api='evm-api'; 'control-panel'='evm-control-panel';
    'control-plane-postgres'='evm-control-plane-postgres'; grafana='evm-grafana'; minio='evm-minio';
    mlflow='evm-mlflow'; 'otel-collector'='evm-otel-collector'; postgres='evm-postgres';
    prometheus='evm-prometheus'; 'task-queue-worker'='evm-task-queue-worker';
    'airflow-init'='evm-airflow-init'; 'minio-create-buckets'='evm-minio-init'
  }
  $healthServices = @(
    'airflow-postgres','airflow-scheduler','airflow-webserver','api','control-panel',
    'control-plane-postgres','mlflow','postgres','task-queue-worker'
  )
  $pinNames = @($compose.service_pins.PSObject.Properties | ForEach-Object { $_.Name })
  $allServices = @($longLivedServices)
  Assert-Check 'compose_service_pin_set_exact' (
    @(Compare-Object $allServices $pinNames).Count -eq 0 -and $pinNames.Count -eq 13
  ) $pinNames
  foreach ($service in $allServices) {
    $pin = $compose.service_pins.PSObject.Properties[$service].Value
    Assert-ExactObjectKeys "compose_pin_keys_$service" $pin @('container_name','container_id','image_id','healthcheck_expected')
    Assert-Check "compose_pin_container_name_$service" ([string]$pin.container_name -ceq [string]$expectedContainerNames[$service]) $pin.container_name
    Assert-Check "compose_pin_container_id_$service" ([string]$pin.container_id -cmatch '^[0-9a-f]{64}$') $pin.container_id
    Assert-Check "compose_pin_image_id_$service" ([string]$pin.image_id -cmatch '^sha256:[0-9a-f]{64}$') $pin.image_id
    Assert-Check "compose_pin_health_$service" (
      [bool]$pin.healthcheck_expected -eq ($healthServices -contains $service)
    ) $pin.healthcheck_expected
  }
  Assert-ExactObjectKeys 'compose_stability_keys_exact' $compose.stability @('duration_seconds','interval_seconds','samples','restart_delta')
  Assert-Check 'compose_stability_exact' (
    [int]$compose.stability.duration_seconds -eq 300 -and
    [int]$compose.stability.interval_seconds -eq 5 -and
    [int]$compose.stability.samples -eq 61 -and
    [int]$compose.stability.restart_delta -eq 0
  ) $compose.stability

  $api = Get-PropertyValue $manifest.expected_state 'api'
  Assert-ExactObjectKeys 'api_keys_exact' $api @(
    'base_url','api_container_name','worker_container_name','image_id','image_attestation',
    'source_revision','source_tree'
  )
  Assert-Check 'api_identity_exact' (
    [string]$api.base_url -ceq 'http://127.0.0.1:8000' -and
    [string]$api.api_container_name -ceq 'evm-api' -and
    [string]$api.worker_container_name -ceq 'evm-task-queue-worker' -and
    [string]$api.image_id -cmatch '^sha256:[0-9a-f]{64}$' -and
    [string]$api.source_revision -ceq $revision -and [string]$api.source_tree -ceq $tree
  ) $api
  Assert-ExactObjectKeys 'api_attestation_keys_exact' $api.image_attestation @('path','sha256')
  $attestationPath = [IO.Path]::GetFullPath([string]$api.image_attestation.path)
  Assert-ExactSha 'api_attestation_sha_format' $api.image_attestation.sha256
  Assert-Check 'api_attestation_exists' (Test-Path -LiteralPath $attestationPath -PathType Leaf) $attestationPath
  Assert-Check 'api_attestation_sha' ((Get-Sha256 $attestationPath) -eq [string]$api.image_attestation.sha256) $api.image_attestation.sha256

  $database = Get-PropertyValue $manifest.expected_state 'database'
  Assert-ExactObjectKeys 'database_keys_exact' $database @(
    'control_plane_schema_versions','airflow_migration_head','mlflow_migration_head','instances'
  )
  $schemaProbe = @'
import ast,json,pathlib,sys
tree=ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA_VERSIONS' for t in n.targets)]
assert len(nodes)==1
value=ast.literal_eval(nodes[0].value)
assert isinstance(value,tuple) and value and all(isinstance(item,str) for item in value)
print(json.dumps(list(value),separators=(',',':')))
'@
  $schemaSource = Join-Path $projectRoot 'src\evm\control_panel\transactional_store.py'
  $schemaOutput = @(& $pythonPath -c $schemaProbe $schemaSource 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "bundle_validation_failed:schema_versions_probe:$($schemaOutput -join [Environment]::NewLine)" }
  $sourceVersions = @(
    (($schemaOutput -join [Environment]::NewLine).Trim() | ConvertFrom-Json -ErrorAction Stop) |
      ForEach-Object { [string]$_ }
  )
  $manifestVersions = @($database.control_plane_schema_versions | ForEach-Object { [string]$_ })
  Assert-Check 'database_schema_versions_match_source' (
    ($manifestVersions -join '|') -ceq ($sourceVersions -join '|')
  ) $database.control_plane_schema_versions
  Assert-Check 'database_migration_heads_exact' (
    [string]$database.airflow_migration_head -ceq '5f2621c13b39' -and
    [string]$database.mlflow_migration_head -ceq '0584bdc529eb'
  ) $database
  $expectedInstances = [ordered]@{
    control_plane=@{container_name='evm-control-plane-postgres';user='evm_control_plane';database='evm_control_plane'}
    mlflow=@{container_name='evm-postgres';user='mlflow';database='mlflow'}
    airflow=@{container_name='evm-airflow-postgres';user='airflow';database='airflow'}
  }
  Assert-ExactObjectKeys 'database_instances_roles_exact' $database.instances @('control_plane','mlflow','airflow')
  foreach ($role in @('control_plane','mlflow','airflow')) {
    $instance = $database.instances.PSObject.Properties[$role].Value
    Assert-ExactObjectKeys "database_instance_keys_$role" $instance @('container_name','user','database')
    foreach ($field in @('container_name','user','database')) {
      Assert-Check "database_instance_${role}_$field" ([string]$instance.$field -ceq [string]$expectedInstances[$role][$field]) $instance.$field
    }
  }

  $kubernetes = Get-PropertyValue $manifest.expected_state 'kubernetes'
  Assert-ExactObjectKeys 'kubernetes_keys_exact' $kubernetes @('allowed_historical_failed_pods','health_confirmation_samples','residual_selectors')
  Assert-Check 'kubernetes_health_confirmation_samples_exact' ([int]$kubernetes.health_confirmation_samples -eq 2) $kubernetes.health_confirmation_samples
  Assert-Check 'kubernetes_residual_selectors_exact' (
    [string]::Join('|', @($kubernetes.residual_selectors)) -ceq 'evm.openai.local/scenario=s8-v4-x1'
  ) $kubernetes.residual_selectors
  Assert-Check 'kubernetes_failed_pod_count_exact' (@($kubernetes.allowed_historical_failed_pods).Count -eq 11) @($kubernetes.allowed_historical_failed_pods).Count
  $failedIdentities = [Collections.Generic.List[string]]::new()
  foreach ($pod in @($kubernetes.allowed_historical_failed_pods)) {
    Assert-ExactObjectKeys 'kubernetes_failed_pod_keys_exact' $pod @('uid','name','namespace','reason','owner_uid')
    Assert-Check 'kubernetes_failed_pod_uid_format' (
      [string]$pod.uid -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' -and
      [string]$pod.owner_uid -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ) $pod
    Assert-Check 'kubernetes_failed_pod_identity_exact' (
      [string]$pod.namespace -ceq 'evm-production' -and
      [string]$pod.name -clike 'evm-b0-production-*' -and
      [string]$pod.reason -ceq 'UnexpectedAdmissionError'
    ) $pod
    [void]$failedIdentities.Add("$($pod.namespace)|$($pod.name)|$($pod.uid)")
  }
  Assert-Check 'kubernetes_failed_pod_allowlist_unique' (
    @($failedIdentities | Select-Object -Unique).Count -eq $failedIdentities.Count
  ) $failedIdentities

  $jobScope = Get-PropertyValue $manifest 'job_scope_contract'
  Assert-ExactObjectKeys 'job_scope_keys_exact' $jobScope @(
    'canonical_active_jobs','historical_observations','historical_classifications'
  )
  Assert-Check 'job_scope_contract_exact' (
    [string]::Join('|', @($jobScope.canonical_active_jobs.sources)) -ceq 'kubernetes_job_status_active|manifest_active_job_file_markers' -and
    [int]$jobScope.canonical_active_jobs.required_count -eq 0 -and
    [string]::Join('|', @($jobScope.historical_observations.sources)) -ceq 'control_plane_task_entity_statuses|mlflow_running_rows|kubernetes_terminal_failed_objects' -and
    $jobScope.historical_observations.separate_from_canonical_active_jobs -eq $true -and
    $jobScope.historical_observations.unknown_or_unproven_blocks_restore -eq $true -and
    $jobScope.historical_observations.deletion_required -eq $false -and
    @($jobScope.historical_classifications).Count -eq 3
  ) $jobScope

  Assert-Check 'probe_max_attempts_one' ([int]$manifest.probe_max_attempts -eq 1) $manifest.probe_max_attempts

  $callNames = @('docker_off_probe','compose_stop','desktop_stop','wsl_shutdown','desktop_start','compose_start')
  $modeCalls = Get-PropertyValue $manifest.call_contract 'restore-only'
  foreach ($name in $callNames) {
    Assert-Check "restore_only_call_zero_$name" ([int](Get-PropertyValue $modeCalls $name) -eq 0) (Get-PropertyValue $modeCalls $name)
  }
  foreach ($name in @('outer', 'bridge', 'runner')) {
    Assert-Check "invocation_exact_one_$name" ([int](Get-PropertyValue $manifest.call_contract.launcher $name) -eq 1) (Get-PropertyValue $manifest.call_contract.launcher $name)
  }
  Assert-Check 'automatic_retry_zero' ([int]$manifest.call_contract.launcher.automatic_retry -eq 0) $manifest.call_contract.launcher.automatic_retry
  foreach ($name in @('windows_fresh_collector','wsl_fresh_collector')) {
    Assert-Check "collector_call_zero_$name" ([int](Get-PropertyValue $manifest.call_contract.collectors $name) -eq 0) (Get-PropertyValue $manifest.call_contract.collectors $name)
  }
  foreach ($name in @('full_stack_3180', 'q0', 'calibration_54', 'matrix_78', 'integrated_v4', 'etw')) {
    Assert-Check "downstream_call_zero_$name" (
      [int](Get-PropertyValue $manifest.call_contract.downstream $name) -eq 0
    ) (Get-PropertyValue $manifest.call_contract.downstream $name)
  }

  $parents = @($manifest.parent_checkpoints)
  Assert-Check 'parent_checkpoint_count_exact' ($parents.Count -eq 7) $parents.Count
  Assert-Check 'parent_role_order_exact' (
    [string]::Join('|', @($parents | ForEach-Object { [string]$_.role })) -ceq [string]::Join('|', $requiredParentRoles)
  ) @($parents | ForEach-Object { $_.role })
  $parentPaths = [Collections.Generic.List[string]]::new()
  $parentByRole = [ordered]@{}
  foreach ($parent in $parents) {
    Assert-ExactObjectKeys "parent_keys_$($parent.role)" $parent @('role','path','sha256','kind','immutable','must_not_execute')
    $role = [string]$parent.role
    Assert-Check "parent_role_known_$role" ($requiredParentRoles -contains $role) $role
    Assert-Check "parent_kind_exact_$role" ([string]$parent.kind -ceq [string]$expectedParentKinds[$role]) $parent.kind
    Assert-Check "parent_immutable_$role" ($parent.immutable -eq $true -and $parent.must_not_execute -eq $true) $parent
    $parentPath = [IO.Path]::GetFullPath([string]$parent.path)
    $parentSha = ([string]$parent.sha256).ToLowerInvariant()
    Assert-ExactSha "parent_sha_format_$role" $parentSha
    Assert-Check "parent_exists_$role" (Test-Path -LiteralPath $parentPath -PathType Leaf) $parentPath
    Assert-Check "parent_outside_bundle_$role" (
      -not $parentPath.StartsWith($bundle.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
    ) $parentPath
    Assert-Check "parent_sha_exact_$role" ((Get-Sha256 $parentPath) -eq $parentSha) $parentSha
    [void]$parentPaths.Add($parentPath)
    $parentByRole[$role] = $parent
  }
  Assert-Check 'parent_paths_distinct' (@($parentPaths | Select-Object -Unique).Count -eq 7) $parentPaths
  Assert-Check 'bridge_parent_verification_before_runner' (
    $bridgeText.IndexOf('parent_checkpoint_sha256_mismatch', [StringComparison]::Ordinal) -ge 0 -and
    $bridgeText.IndexOf('parent_checkpoint_sha256_mismatch', [StringComparison]::Ordinal) -lt $runnerInvokeIndex -and
    $bridgeText.IndexOf('$shaChain[$role] = $parentShaChain[$role]', [StringComparison]::Ordinal) -ge 0
  ) 'parents verified and in sha chain'
  $linkProbe = @'
import json,sys
manifest=json.load(open(sys.argv[1],encoding='utf-8-sig'))
readback=json.load(open(sys.argv[2],encoding='utf-8-sig'))
index=json.load(open(sys.argv[3],encoding='utf-8-sig'))
expected={'compose':manifest['expected_state']['compose'],'api':manifest['expected_state']['api'],'database':manifest['expected_state']['database'],'kubernetes':manifest['expected_state']['kubernetes'],'job_scope_contract':manifest['job_scope_contract']}
assert readback.get('runtime_state')==expected
def contains(value,target):
    if isinstance(value,str): return value==target
    if isinstance(value,dict): return any(contains(item,target) for item in value.values())
    if isinstance(value,list): return any(contains(item,target) for item in value)
    return False
assert contains(index,sys.argv[2]) and contains(index,sys.argv[4])
'@
  $readbackParent = $parentByRole['post_manual_on_readback']
  $indexParent = $parentByRole['post_manual_on_index']
  $linkOutput = @(& $pythonPath -c $linkProbe $manifestPath $readbackParent.path $indexParent.path $readbackParent.sha256 2>&1)
  Assert-Check 'post_manual_runtime_state_and_index_link' ($LASTEXITCODE -eq 0) ($linkOutput -join [Environment]::NewLine)

  Assert-ExactObjectKeys 'evidence_keys_exact' $manifest.evidence @(
    'write_mode','failure_creates_completion_marker','failure_index_is_not_success_index',
    'restore_only_creates_completion_marker','success_requires_all_invariants'
  )
  Assert-Check 'evidence_create_exclusive' ([string]$manifest.evidence.write_mode -ceq 'create-exclusive') $manifest.evidence.write_mode
  Assert-Check 'output_create_exclusive' ([string]$manifest.output.write_mode -ceq 'create-exclusive') $manifest.output.write_mode
  Assert-Check 'output_must_not_exist' ($manifest.output.must_not_exist_before_runner -eq $true) $manifest.output.must_not_exist_before_runner
  $outputPath = [IO.Path]::GetFullPath([string]$manifest.output.path)
  Assert-Check 'output_path_currently_absent' (-not (Test-Path -LiteralPath $outputPath)) $outputPath
  Assert-Check 'failure_forbids_completion_marker' ($manifest.evidence.failure_creates_completion_marker -eq $false) $manifest.evidence.failure_creates_completion_marker
  Assert-Check 'restore_only_forbids_completion_marker' ($manifest.evidence.restore_only_creates_completion_marker -eq $false) $manifest.evidence.restore_only_creates_completion_marker
  # The validator contains the forbidden-pattern literals by design, so only
  # executable harness sources participate in the destructive-command scan.
  $runtimeSource = @(
    $componentTexts.core,
    $componentTexts.process,
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
    $outerText.IndexOf('r7-outer-invocation-reservation.json', [StringComparison]::Ordinal) -ge 0 -and
    $outerText.IndexOf('r7-outer-invocation-reservation.json', [StringComparison]::Ordinal) -lt $outerInvokeIndex
  ) 'outer reservation before bridge invocation'
  Assert-Check 'bridge_validator_exactly_once' (
    (Get-AmpersandInvocationCount $bridgeAst 'ValidatorPath') -eq 1
  ) 'one'
  $validatorInvokeIndex = $bridgeText.IndexOf('& $ValidatorPath', [StringComparison]::Ordinal)
  $bridgeReservationIndex = $bridgeText.IndexOf('Write-CreateNewJson $bridgeReservation', [StringComparison]::Ordinal)
  $validatorImmediateIndex = $bridgeText.IndexOf('validator_sha256_mismatch_immediate', [StringComparison]::Ordinal)
  Assert-Check 'bridge_preexecution_validator_before_reservation' (
    $validatorInvokeIndex -ge 0 -and
    $bridgeText.IndexOf('-PreExecution', $validatorInvokeIndex, [StringComparison]::Ordinal) -ge 0 -and
    $bridgeReservationIndex -gt $validatorInvokeIndex -and
    $bridgeReservationIndex -lt $runnerInvokeIndex
  ) 'validator -> bridge reservation -> runner'
  Assert-Check 'bridge_validator_immediate_rehash_before_validator' (
    $validatorImmediateIndex -ge 0 -and
    $validatorImmediateIndex -lt $validatorInvokeIndex -and
    $bridgeText.Contains("if ((Get-Sha256 `$ValidatorPath) -ne `$ExpectedValidatorSha256) { throw 'validator_sha256_mismatch_immediate' }")
  ) 'validator rehash immediately precedes validator invocation'
  Assert-Check 'bridge_reservation_contains_run_id' (
    $bridgeText.IndexOf('run_id=$PinnedRunId', [StringComparison]::Ordinal) -ge 0 -and
    $bridgeText.IndexOf('run_id=$PinnedRunId', [StringComparison]::Ordinal) -lt $runnerInvokeIndex
  ) 'run_id pinned before runner'
  $launcherEvidenceIndex = $bridgeText.IndexOf('$launcherEvidence = [ordered]@{', [StringComparison]::Ordinal)
  $launcherSearchStart = [Math]::Max(0, $launcherEvidenceIndex)
  $launcherRunIdIndex = $bridgeText.IndexOf('run_id=$PinnedRunId', $launcherSearchStart, [StringComparison]::Ordinal)
  $launcherEvidenceEndIndex = $bridgeText.IndexOf('$shaChain = [ordered]@{', $launcherSearchStart, [StringComparison]::Ordinal)
  Assert-Check 'launcher_evidence_run_id_exact_manifest_pin' (
    $launcherEvidenceIndex -ge 0 -and
    $launcherRunIdIndex -gt $launcherEvidenceIndex -and
    $launcherRunIdIndex -lt $launcherEvidenceEndIndex -and
    (($bridgeText.Split(@('run_id=$PinnedRunId'), [StringSplitOptions]::None).Count - 1) -eq 2)
  ) 'launcher evidence and bridge reservation each use the exact pinned manifest run_id'

  $launcherBase64Index = $bridgeText.IndexOf('$launcherBase64 = ', [StringComparison]::Ordinal)
  $invocationBoundaryGuards = [ordered]@{
    outer_sha256_mismatch_immediate_before_runner = "if ((Get-Sha256 `$OuterLauncherPath) -ne `$outerExpected) { throw 'outer_sha256_mismatch_immediate_before_runner' }"
    bridge_sha256_mismatch_immediate_before_runner = "if ((Get-Sha256 `$PSCommandPath) -ne `$bridgeExpected) { throw 'bridge_sha256_mismatch_immediate_before_runner' }"
    runner_sha256_mismatch_immediate = "if ((Get-Sha256 `$RunnerPath) -ne `$ExpectedRunnerSha256) { throw 'runner_sha256_mismatch_immediate' }"
    core_sha256_mismatch_immediate = "if ((Get-Sha256 `$CorePath) -ne `$ExpectedCoreSha256) { throw 'core_sha256_mismatch_immediate' }"
    process_sha256_mismatch_immediate = "if ((Get-Sha256 `$ProcessPath) -ne `$ExpectedProcessSha256) { throw 'process_sha256_mismatch_immediate' }"
  }
  foreach ($guardEntry in $invocationBoundaryGuards.GetEnumerator()) {
    $guard = [string]$guardEntry.Key
    $guardText = [string]$guardEntry.Value
    $guardIndex = $bridgeText.IndexOf($guardText, [StringComparison]::Ordinal)
    Assert-Check "bridge_${guard}_at_invocation_boundary" (
      $launcherBase64Index -ge 0 -and $guardIndex -gt $launcherBase64Index -and $guardIndex -lt $runnerInvokeIndex
    ) $guardIndex
  }

  $combinedSource = $outerText + "`n" + $bridgeText + "`n" + $manifestText + "`n" + $runtimeSource
  $forbiddenPatterns = [ordered]@{
    terminate_job_object = '\bTerminateJobObject\b'
    kill_on_job_close = '\bJOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE\b'
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
    $directProcessHosts = @($commandNames | Where-Object {
        [IO.Path]::GetFileName([string]$_) -match '^(?i:pythonw?|py|powershell|pwsh|cmd|cscript|wscript)(?:\.exe)?$'
      })
    Assert-Check "powershell_ast_direct_process_host_absent_$($entry.name)" ($directProcessHosts.Count -eq 0) $directProcessHosts
  }

  [ordered]@{
    schema_version = 'evm.s8_v4.x1_phase_b2_r7_bundle_validation.v1'
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
      builder = $componentHashes.builder
      core = $componentHashes.core
      process = $componentHashes.process
      runner = $componentHashes.runner
      validator = $componentHashes.validator
      docker_compose = $componentHashes.docker_compose
    }
    checks = $checks
  } | ConvertTo-Json -Depth 14 -Compress
  exit 0
}
catch {
  [ordered]@{
    schema_version = 'evm.s8_v4.x1_phase_b2_r7_bundle_validation.v1'
    status = 'FAIL'
    validated_at = [DateTime]::UtcNow.ToString('o')
    error = $_.Exception.Message
    passed_check_count = $checks.Count
    checks = $checks
  } | ConvertTo-Json -Depth 14 -Compress
  exit 2
}
