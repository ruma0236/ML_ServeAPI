#Requires -Version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$ManifestPath,
  [Parameter(Mandatory = $true)][string]$OuterPath,
  [Parameter(Mandatory = $true)][string]$BridgePath,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedTrustedCheckpointSha256,
  [switch]$OfflineContained,
  [switch]$PreExecution
)

function Get-BootstrapSha256([string]$Path) {
  $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
  $hasher = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-','').ToLowerInvariant()
  }
  finally {
    $hasher.Dispose()
    $stream.Dispose()
  }
}

function Assert-CanonicalValidatorEntry(
  [object]$PowerShellPin,
  [string]$ExpectedValidatorPath,
  [string]$ExpectedManifestPath,
  [string]$ExpectedOuterPath,
  [string]$ExpectedBridgePath,
  [string]$ExpectedOuterSha,
  [string]$ExpectedCheckpointSha,
  [bool]$ExpectOfflineContained,
  [bool]$ExpectPreExecution
) {
  if ($ExpectOfflineContained -and $ExpectPreExecution) {
    throw 'bundle_validation_failed:canonical_validator_entry_mode_conflict'
  }
  $argv = [Environment]::GetCommandLineArgs()
  foreach ($argument in $argv) {
    if ($argument -imatch '^-(?:c|command|e|encodedcommand)$') {
      throw 'bundle_validation_failed:canonical_validator_entry_command_or_encoded_command_rejected'
    }
  }
  $expected = [Collections.Generic.List[string]]::new()
  foreach ($argument in @(
      [string]$PowerShellPin.path,'-NoProfile','-NonInteractive','-File',
      $ExpectedValidatorPath,'-ManifestPath',$ExpectedManifestPath,
      '-OuterPath',$ExpectedOuterPath,'-BridgePath',$ExpectedBridgePath,
      '-ExpectedOuterSha256',$ExpectedOuterSha,
      '-ExpectedTrustedCheckpointSha256',$ExpectedCheckpointSha
    )) {
    [void]$expected.Add($argument)
  }
  if ($ExpectOfflineContained) { [void]$expected.Add('-OfflineContained') }
  if ($ExpectPreExecution) { [void]$expected.Add('-PreExecution') }
  if ($argv.Count -ne $expected.Count) {
    throw "bundle_validation_failed:canonical_validator_entry_argv_count_mismatch:$($argv.Count)"
  }
  foreach ($index in 0..($expected.Count - 1)) {
    if ($index -in @(0,4,6,8,10)) {
      if (-not [IO.Path]::GetFullPath($argv[$index]).Equals(
          [IO.Path]::GetFullPath($expected[$index]),
          [StringComparison]::OrdinalIgnoreCase)) {
        throw "bundle_validation_failed:canonical_validator_entry_path_mismatch:$index"
      }
    }
    elseif ($argv[$index] -cne $expected[$index]) {
      throw "bundle_validation_failed:canonical_validator_entry_argument_mismatch:$index"
    }
  }
  $process = [Diagnostics.Process]::GetCurrentProcess()
  $processPath = [IO.Path]::GetFullPath([string]$process.MainModule.FileName)
  if (-not $processPath.Equals(
      [IO.Path]::GetFullPath([string]$PowerShellPin.path),
      [StringComparison]::OrdinalIgnoreCase) -or
      (Get-BootstrapSha256 $processPath) -cne ([string]$PowerShellPin.sha256).ToLowerInvariant() -or
      [IO.FileInfo]::new($processPath).Length -ne [int64]$PowerShellPin.bytes) {
    throw 'bundle_validation_failed:canonical_validator_entry_host_identity_mismatch'
  }
}

$bootstrapManifest = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (
  [IO.File]::ReadAllText([IO.Path]::GetFullPath($ManifestPath))
)
$bootstrapPowerShellPin = $bootstrapManifest.toolchain.powershell
Assert-CanonicalValidatorEntry `
  $bootstrapPowerShellPin $PSCommandPath $ManifestPath $OuterPath $BridgePath `
  ($ExpectedOuterSha256.ToLowerInvariant()) `
  ($ExpectedTrustedCheckpointSha256.ToLowerInvariant()) `
  ([bool]$OfflineContained) ([bool]$PreExecution)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$checks = [Collections.Generic.List[object]]::new()
$script:GitPath = $null
$script:GitRepositoryConfigPin = $null
$script:GitRepositoryAttributesLivePin = $null
$script:GitRemoteCwd = $null
$script:CanonicalGitRemoteUrl = 'https://github.com/ruma0236/ML_ServeAPI.git'
$requiredParentRoles = @(
  'r5_failure_seal', 'r5_failure_index', 'r6_compose_rca',
  'r6_failure_seal_amendment', 'r6_final_index',
  'post_manual_on_readback', 'post_manual_on_index',
  'r7_failure_seal', 'r7_failure_index', 'r7_post_seal_residual_amendment'
)
$expectedParentKinds = [ordered]@{
  r5_failure_seal = 'r5_failure_seal'
  r5_failure_index = 'r5_failure_index'
  r6_compose_rca = 'r6_compose_rca'
  r6_failure_seal_amendment = 'r6_failure_seal_amendment'
  r6_final_index = 'r6_final_index'
  post_manual_on_readback = 'post_manual_on_readback'
  post_manual_on_index = 'post_manual_on_index'
  r7_failure_seal = 'r7_failure_seal'
  r7_failure_index = 'r7_failure_index'
  r7_post_seal_residual_amendment = 'r7_post_seal_residual_amendment'
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
  $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
  $hasher = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-','').ToLowerInvariant()
  }
  finally {
    $hasher.Dispose()
    $stream.Dispose()
  }
}

function Get-WorktreeBlobOid([string]$Path) {
  $payload = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path))
  $header = [Text.Encoding]::ASCII.GetBytes("blob $($payload.Length)`0")
  $combined = [byte[]]::new($header.Length + $payload.Length)
  [Array]::Copy($header, 0, $combined, 0, $header.Length)
  [Array]::Copy($payload, 0, $combined, $header.Length, $payload.Length)
  $hasher = [Security.Cryptography.SHA1]::Create()
  try {
    return ([BitConverter]::ToString($hasher.ComputeHash($combined))).Replace('-','').ToLowerInvariant()
  }
  finally {
    $hasher.Dispose()
  }
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

function Test-CurrentProcessInJob {
  $assemblyName = [Reflection.AssemblyName]::new("R7S1ValidatorNative_$PID")
  $assembly = [Reflection.Emit.AssemblyBuilder]::DefineDynamicAssembly(
    $assemblyName,[Reflection.Emit.AssemblyBuilderAccess]::Run
  )
  $module = $assembly.DefineDynamicModule($assemblyName.Name)
  $type = $module.DefineType(
    'R7S1ValidatorNative',[Reflection.TypeAttributes]'Public,Abstract,Sealed'
  )
  $parameterTypes = [Type[]]@([IntPtr],[IntPtr],[bool].MakeByRefType())
  $method = $type.DefinePInvokeMethod(
    'IsProcessInJob','kernel32.dll',
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
  $arguments = [object[]]@(
    [Diagnostics.Process]::GetCurrentProcess().Handle,[IntPtr]::Zero,$false
  )
  $ok = [bool]$native.GetMethod('IsProcessInJob').Invoke($null,$arguments)
  if (-not $ok) { throw 'bundle_validation_failed:is_process_in_job_api_failed' }
  return [bool]$arguments[2]
}

function Assert-GitRepositoryConfigLive {
  if ($null -eq $script:GitRepositoryConfigPin) {
    throw 'bundle_validation_failed:git_repository_config_not_verified'
  }
  $configPath = [IO.Path]::GetFullPath([string]$script:GitRepositoryConfigPin.path)
  $candidate = $configPath
  while (-not [string]::IsNullOrWhiteSpace($candidate)) {
    if ([IO.File]::Exists($candidate) -or [IO.Directory]::Exists($candidate)) {
      if (([IO.File]::GetAttributes($candidate) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "bundle_validation_failed:git_repository_config_reparse_ancestor:$candidate"
      }
    }
    $trimmed = $candidate.TrimEnd([char]92,[char]47)
    $parent = [IO.Path]::GetDirectoryName($trimmed)
    if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) { break }
    $candidate = $parent
  }
  if (-not [IO.File]::Exists($configPath) -or
      [IO.FileInfo]::new($configPath).Length -ne [int64]$script:GitRepositoryConfigPin.bytes -or
      (Get-Sha256 $configPath) -cne ([string]$script:GitRepositoryConfigPin.sha256).ToLowerInvariant()) {
    throw 'bundle_validation_failed:git_repository_config_live_identity_mismatch'
  }
  $configWorktree = [IO.Path]::Combine([IO.Path]::GetDirectoryName($configPath),'config.worktree')
  if ([IO.File]::Exists($configWorktree) -or [IO.Directory]::Exists($configWorktree)) {
    throw 'bundle_validation_failed:git_repository_config_worktree_must_be_absent'
  }
}

function Assert-GitRepositoryAttributesLive {
  if ($null -eq $script:GitRepositoryAttributesLivePin) {
    throw 'bundle_validation_failed:git_repository_attributes_not_verified'
  }
  foreach ($rawPath in @(
      [string]$script:GitRepositoryAttributesLivePin.path,
      [string]$script:GitRepositoryAttributesLivePin.git_top_path,
      [string]$script:GitRepositoryAttributesLivePin.git_info_path
    )) {
    $candidate = [IO.Path]::GetFullPath($rawPath)
    while (-not [string]::IsNullOrWhiteSpace($candidate)) {
      if ([IO.File]::Exists($candidate) -or [IO.Directory]::Exists($candidate)) {
        if (([IO.File]::GetAttributes($candidate) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
          throw "bundle_validation_failed:git_repository_attributes_reparse_ancestor:$candidate"
        }
      }
      $trimmed = $candidate.TrimEnd([char]92,[char]47)
      $parent = [IO.Path]::GetDirectoryName($trimmed)
      if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) { break }
      $candidate = $parent
    }
  }
  $attributesPath = [IO.Path]::GetFullPath([string]$script:GitRepositoryAttributesLivePin.path)
  if (-not [IO.File]::Exists($attributesPath) -or
      [IO.FileInfo]::new($attributesPath).Length -ne [int64]$script:GitRepositoryAttributesLivePin.bytes -or
      (Get-Sha256 $attributesPath) -cne
        ([string]$script:GitRepositoryAttributesLivePin.sha256).ToLowerInvariant()) {
    throw 'bundle_validation_failed:git_repository_attributes_live_identity_mismatch'
  }
  foreach ($absentPath in @(
      [string]$script:GitRepositoryAttributesLivePin.git_top_path,
      [string]$script:GitRepositoryAttributesLivePin.git_info_path
    )) {
    if ([IO.File]::Exists($absentPath) -or [IO.Directory]::Exists($absentPath)) {
      throw "bundle_validation_failed:external_git_attributes_must_be_absent:$absentPath"
    }
  }
}

function Set-GitEnvironmentFence {
  $scrubExact = @(
    'all_proxy','curl_ca_bundle','editor','http_proxy','https_proxy','no_proxy','pager',
    'request_method','ssh_agent_pid','ssh_askpass','ssh_askpass_require','ssh_auth_sock',
    'ssl_cert_dir','ssl_cert_file','visual','xdg_config_home'
  )
  foreach ($entry in [Environment]::GetEnvironmentVariables('Process').Keys) {
    $name = [string]$entry
    if ($name.StartsWith('GIT_',[StringComparison]::OrdinalIgnoreCase) -or
        $scrubExact -contains $name.ToLowerInvariant()) {
      [Environment]::SetEnvironmentVariable($name,$null,'Process')
    }
  }
  foreach ($pair in ([ordered]@{
      GCM_INTERACTIVE='never'; GIT_ATTR_NOSYSTEM='1'; GIT_CONFIG_GLOBAL='NUL'; GIT_CONFIG_NOSYSTEM='1';
      GIT_OPTIONAL_LOCKS='0'; GIT_PAGER=''; GIT_TERMINAL_PROMPT='0'
    }).GetEnumerator()) {
    [Environment]::SetEnvironmentVariable([string]$pair.Key,[string]$pair.Value,'Process')
  }
}

function Get-GitRepositoryConfigKeyNames([string]$Path) {
  $text = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Path),[Text.UTF8Encoding]::new($false,$true))
  if ($text.Contains([char]0)) { throw 'bundle_validation_failed:git_repository_config_nul_forbidden' }
  $section = $null
  $names = [Collections.Generic.List[string]]::new()
  foreach ($rawLine in ($text -split "`r?`n")) {
    $line = $rawLine.Trim()
    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#') -or $line.StartsWith(';')) {
      continue
    }
    $sectionMatch = [regex]::Match(
      $line,'^\[([A-Za-z][A-Za-z0-9.-]*)(?:[ \t]+"([^"\\\r\n]+)")?\][ \t]*$'
    )
    if ($sectionMatch.Success) {
      $section = $sectionMatch.Groups[1].Value.ToLowerInvariant()
      if ($sectionMatch.Groups[2].Success) {
        $section += '.' + $sectionMatch.Groups[2].Value.ToLowerInvariant()
      }
      continue
    }
    $keyMatch = [regex]::Match($line,'^([A-Za-z][A-Za-z0-9.-]*)[ \t]*=[ \t]*(.*?)[ \t]*$')
    if ($null -eq $section -or -not $keyMatch.Success -or
        [string]::IsNullOrWhiteSpace($keyMatch.Groups[2].Value) -or
        $keyMatch.Groups[2].Value.StartsWith('!') -or
        $keyMatch.Groups[2].Value.StartsWith('"') -or
        $keyMatch.Groups[2].Value.EndsWith('\')) {
      throw 'bundle_validation_failed:git_repository_config_syntax_not_canonical'
    }
    $name = $section + '.' + $keyMatch.Groups[1].Value.ToLowerInvariant()
    if ($names.Contains($name)) {
      throw "bundle_validation_failed:git_repository_config_duplicate_key:$name"
    }
    [void]$names.Add($name)
  }
  $ordered = [string[]]$names.ToArray()
  [Array]::Sort($ordered,[StringComparer]::Ordinal)
  return $ordered
}

function Invoke-GitRead([string[]]$Arguments) {
  if ([string]::IsNullOrWhiteSpace([string]$script:GitPath)) {
    throw 'bundle_validation_failed:git_toolchain_not_verified'
  }
  Assert-GitRepositoryConfigLive
  Assert-GitRepositoryAttributesLive
  $text = @(& $script:GitPath -c 'core.fsmonitor=false' -C $script:RepositoryRoot @Arguments 2>&1)
  if ($LASTEXITCODE -ne 0) {
    throw "bundle_validation_failed:git_read:$($Arguments -join ','):$($text -join [Environment]::NewLine)"
  }
  return ($text -join [Environment]::NewLine).Trim()
}

function Invoke-GitRemoteRead([string]$Branch) {
  Assert-GitRepositoryConfigLive
  Assert-GitRepositoryAttributesLive
  Microsoft.PowerShell.Management\Push-Location -LiteralPath $script:GitRemoteCwd
  try {
    $text = @(& $script:GitPath -c 'core.fsmonitor=false' -c 'credential.helper=' `
      ls-remote --exit-code $script:CanonicalGitRemoteUrl "refs/heads/$Branch" 2>&1)
    if ($LASTEXITCODE -ne 0) {
      throw 'bundle_validation_failed:git_remote_read_failed'
    }
    return ($text -join [Environment]::NewLine).Trim()
  }
  finally {
    Microsoft.PowerShell.Management\Pop-Location
  }
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

function Get-GitNormalizedWorktreeBlobOid(
  [string]$AbsolutePath,
  [string]$GitTopLevel
) {
  $fullPath = [IO.Path]::GetFullPath($AbsolutePath)
  $prefix = [IO.Path]::GetFullPath($GitTopLevel).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
  if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "bundle_validation_failed:runtime_path_outside_git_tree:$fullPath"
  }
  $relativePath = $fullPath.Substring($prefix.Length).Replace('\', '/')
  $entry = (Invoke-GitRead @(
      '-c','core.autocrlf=true','hash-object',"--path=$relativePath",$fullPath
    )).ToLowerInvariant()
  if ($entry -notmatch '^[0-9a-f]{40}$') {
    throw "bundle_validation_failed:git_normalized_worktree_blob_invalid:$relativePath"
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
  if (-not $PreExecution) {
    Assert-Check 'offline_validator_containment_acknowledged' $OfflineContained.IsPresent $OfflineContained.IsPresent
    Assert-Check 'offline_validator_process_in_job' (Test-CurrentProcessInJob) $PID
  }
  $manifestPath = [IO.Path]::GetFullPath($ManifestPath)
  $outerPath = [IO.Path]::GetFullPath($OuterPath)
  $bridgePath = [IO.Path]::GetFullPath($BridgePath)
  $bundle = [IO.Path]::GetDirectoryName($manifestPath)
  Assert-Check 'bundle_directory_exists' (Test-Path -LiteralPath $bundle -PathType Container) $bundle
  Assert-Check 'manifest_path_exact' ($manifestPath -eq (Join-Path $bundle 'phase-b2-r7s1-work-order.json')) $manifestPath
  Assert-Check 'outer_path_exact' ($outerPath -eq (Join-Path $bundle 'invoke-verified-x1-phase-b2-r7s1.ps1')) $outerPath
  Assert-Check 'bridge_path_exact' ($bridgePath -eq (Join-Path $bundle 'invoke-x1-phase-b2-r7s1-bridge.ps1')) $bridgePath
  $expectedNames = [Collections.Generic.List[string]]::new()
  foreach ($name in @(
    'invoke-verified-x1-phase-b2-r7s1.ps1',
    'invoke-x1-phase-b2-r7s1-bridge.ps1',
    'phase-b2-r7s1-work-order.json'
  )) { [void]$expectedNames.Add($name) }
  if ($PreExecution) {
    [void]$expectedNames.Add('r7s1-outer-invocation-reservation.json')
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
    $reservationPath = Join-Path $bundle 'r7s1-outer-invocation-reservation.json'
    try {
      $reservation = [IO.File]::ReadAllText($reservationPath) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
      throw "bundle_validation_failed:outer_reservation_json:$($_.Exception.Message)"
    }
    Assert-Check 'outer_reservation_schema' ([string]$reservation.schema -eq 's8-v4-x1-phase-b2-r7s1-outer-reservation/v1') $reservation.schema
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

  $outerBridgeMarker = '# R7S1_BRIDGE_INVOKE_EXACTLY_ONCE'
  $bridgeRunnerMarker = '# R7S1_RUNNER_INVOKE_EXACTLY_ONCE'
  Assert-Check 'outer_exact_one_bridge_marker' (($outerText.Split(@($outerBridgeMarker), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  Assert-Check 'bridge_exact_one_runner_marker' (($bridgeText.Split(@($bridgeRunnerMarker), [StringSplitOptions]::None).Count - 1) -eq 1) 'one'
  $outerAmpersand = @(Get-AmpersandInvocations $outerAst)
  $bridgeAmpersand = @(Get-AmpersandInvocations $bridgeAst)
  $outerExpectedSignature = @(
    '$bridgePath', '-ExpectedOuterSha256', '$outerExpected',
    '-ObservedOuterSha256', '$outerObserved',
    '-ExpectedBridgeSha256FromOuter', '$ExpectedBridgeSha256',
    '-ObservedBridgeSha256', '$bridgeObserved',
    '-ExpectedTrustedCheckpointSha256FromOuter', '$trustedCheckpointExpected',
    '-InvocationNonce', '$invocationNonce',
    '-TokenEvidenceBase64', '$tokenEvidenceBase64',
    '-ToolchainObservationBase64', '$toolchainObservationBase64',
    '-OuterLauncherPath', '$outerPath', '-OutputDirectory', '$OutputDirectory'
  ) -join [char]31
  $runnerExpectedSignature = @(
    '$PythonPath', '-I', '-S', '-B', '$RunnerPath', '--manifest', '$ManifestPath',
    '--output-directory', '$OutputDirectory', '--expected-revision', '$PinnedRevision',
    '--expected-trusted-checkpoint-sha256', '$trustedCheckpointExpected',
    '--launcher-evidence-base64', '$launcherBase64', '--repository-root', '$RepositoryRoot',
    '--mode', 'restore-only'
  ) -join [char]31
  Assert-Check 'outer_ast_exact_invocation_set' (
    $outerAmpersand.Count -eq 1 -and
    (Get-InvocationSignature $outerAmpersand[0]) -ceq $outerExpectedSignature
  ) @($outerAmpersand | ForEach-Object { Get-InvocationSignature $_ })
  Assert-Check 'bridge_ast_exact_ampersand_invocation_count' ($bridgeAmpersand.Count -eq 1) $bridgeAmpersand.Count
  $bridgeTargets = @($bridgeAmpersand | ForEach-Object { Get-CommandElementSemantic $_.CommandElements[0] })
  $expectedBridgeTargets = @('$PythonPath')
  Assert-Check 'bridge_ast_exact_ampersand_target_multiset' (
    @(Compare-Object ($expectedBridgeTargets | Sort-Object) ($bridgeTargets | Sort-Object)).Count -eq 0 -and
    $bridgeTargets.Count -eq $expectedBridgeTargets.Count
  ) $bridgeTargets
  $bridgeSignatures = @($bridgeAmpersand | ForEach-Object { Get-InvocationSignature $_ })
  Assert-Check 'bridge_ast_exact_one_runner_invocation' (
    @($bridgeSignatures | Where-Object { $_ -ceq $runnerExpectedSignature }).Count -eq 1
  ) $bridgeSignatures
  Assert-Check 'bridge_preexecution_uncontained_child_zero' (
    $bridgeAmpersand.Count -eq 1 -and $bridgeTargets[0] -ceq '$PythonPath'
  ) $bridgeTargets
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
  $outerReservationPathIndex = $outerText.IndexOf('$reservation = Join-Path $PSScriptRoot',[StringComparison]::Ordinal)
  $outerPrewritePathFenceIndex = $outerText.IndexOf('# R7S1_PATH_FENCE_OUTER_PREWRITE',[StringComparison]::Ordinal)
  $outerEntryGuardIndex = $outerText.IndexOf('# R7S1_CANONICAL_POWERSHELL_ENTRY_OUTER',[StringComparison]::Ordinal)
  $bridgeEntryGuardIndex = $bridgeText.IndexOf('# R7S1_CANONICAL_POWERSHELL_ENTRY_BRIDGE',[StringComparison]::Ordinal)
  $bridgeReservationPathIndexEarly = $bridgeText.IndexOf('$bridgeReservation = Join-Path $PSScriptRoot',[StringComparison]::Ordinal)
  Assert-Check 'outer_canonical_powershell_entry_guard_exact' (
    $outerText.Contains(
      'Assert-CanonicalPowerShellEntry $PinnedPowerShellPath $PinnedPowerShellSha256 $PinnedPowerShellBytes $outerPath $outerExpected $trustedCheckpointExpected $OutputDirectory'
    ) -and
    $outerText.Contains("'-NoProfile','-NonInteractive','-File'") -and
    ($outerText.Split(@('[Environment]::GetCommandLineArgs()'),[StringSplitOptions]::None).Count - 1) -eq 1 -and
    $outerText.Contains('canonical_powershell_entry_command_or_encoded_command_rejected')
  ) $outerEntryGuardIndex
  Assert-Check 'bridge_canonical_powershell_entry_guard_exact' (
    $bridgeText.Contains(
      'Assert-CanonicalPowerShellEntry $PinnedPowerShellPath $PinnedPowerShellSha256 $PinnedPowerShellBytes $OuterLauncherPath $outerExpected $trustedCheckpointExpected $OutputDirectory'
    ) -and
    $bridgeText.Contains("'-NoProfile','-NonInteractive','-File'") -and
    ($bridgeText.Split(@('[Environment]::GetCommandLineArgs()'),[StringSplitOptions]::None).Count - 1) -eq 1 -and
    $bridgeText.Contains('canonical_powershell_entry_command_or_encoded_command_rejected')
  ) $bridgeEntryGuardIndex
  Assert-Check 'outer_canonical_powershell_entry_before_any_reservation_write' (
    $outerEntryGuardIndex -ge 0 -and
    $outerEntryGuardIndex -lt $outerReservationPathIndex -and
    $outerEntryGuardIndex -lt $outerReservationWriteIndex
  ) $outerEntryGuardIndex
  Assert-Check 'bridge_canonical_powershell_entry_before_any_reservation_write' (
    $bridgeEntryGuardIndex -ge 0 -and
    $bridgeEntryGuardIndex -lt $bridgeReservationPathIndexEarly -and
    $bridgeEntryGuardIndex -lt $runnerInvokeIndex
  ) $bridgeEntryGuardIndex
  $preGitConfigPin = Get-PropertyValue $manifest.toolchain 'git_repository_config'
  $preGitAttributesPin = Get-PropertyValue $manifest.toolchain 'git_repository_attributes'
  foreach ($entry in @(
      @{ name='outer'; ast=$outerAst; text=$outerText; invoke=$outerInvokeIndex; reservation=$outerReservationPathIndex },
      @{ name='bridge'; ast=$bridgeAst; text=$bridgeText; invoke=$runnerInvokeIndex; reservation=$bridgeReservationPathIndexEarly }
    )) {
    $pinnedConfigPath = [IO.Path]::GetFullPath(
      (Get-LiteralAssignment $entry.ast 'PinnedGitRepositoryConfigPath')
    )
    $pinnedConfigSha = (Get-LiteralAssignment $entry.ast 'PinnedGitRepositoryConfigSha256').ToLowerInvariant()
    $pinnedConfigBytes = [int64](Get-LiteralAssignment $entry.ast 'PinnedGitRepositoryConfigBytes')
    Assert-Check "$($entry.name)_git_repository_config_pin_exact" (
      $pinnedConfigPath -eq [IO.Path]::GetFullPath([string]$preGitConfigPin.path) -and
      $pinnedConfigSha -ceq ([string]$preGitConfigPin.sha256).ToLowerInvariant() -and
      $pinnedConfigBytes -eq [int64]$preGitConfigPin.bytes
    ) $pinnedConfigPath
    $prewriteMarker = "# R7S1_GIT_CONFIG_FENCE_$($entry.name.ToUpperInvariant())_PREWRITE"
    $finalMarker = "# R7S1_GIT_CONFIG_FENCE_$($entry.name.ToUpperInvariant())_FINAL"
    $prewriteIndex = $entry.text.IndexOf($prewriteMarker,[StringComparison]::Ordinal)
    $finalIndex = $entry.text.IndexOf($finalMarker,[StringComparison]::Ordinal)
    $entryGuardBoundary = if ($entry.name -eq 'outer') {
      $outerEntryGuardIndex
    }
    else {
      $bridgeEntryGuardIndex
    }
    Assert-Check "$($entry.name)_git_repository_config_fence_order" (
      $prewriteIndex -gt $entryGuardBoundary -and
      $prewriteIndex -lt [int]$entry.reservation -and
      $finalIndex -gt [int]$entry.reservation -and
      $finalIndex -lt [int]$entry.invoke -and
      ($entry.text.Split(@('Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath'),
        [StringSplitOptions]::None).Count - 1) -eq 2
    ) @($prewriteIndex,$finalIndex)
    $pinnedAttributesPath = [IO.Path]::GetFullPath(
      (Get-LiteralAssignment $entry.ast 'PinnedGitRepositoryAttributesPath')
    )
    $pinnedAttributesSha = (
      Get-LiteralAssignment $entry.ast 'PinnedGitRepositoryAttributesSha256'
    ).ToLowerInvariant()
    $pinnedAttributesBytes = [int64](
      Get-LiteralAssignment $entry.ast 'PinnedGitRepositoryAttributesBytes'
    )
    $expectedGitTopAttributesPath = [IO.Path]::Combine(
      [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($pinnedAttributesPath)),
      '.gitattributes'
    )
    $expectedGitInfoAttributesPath = [IO.Path]::Combine(
      [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($pinnedAttributesPath)),
      '.git','info','attributes'
    )
    Assert-Check "$($entry.name)_git_repository_attributes_pin_exact" (
      $pinnedAttributesPath -eq [IO.Path]::GetFullPath([string]$preGitAttributesPin.path) -and
      $pinnedAttributesSha -ceq ([string]$preGitAttributesPin.sha256).ToLowerInvariant() -and
      $pinnedAttributesBytes -eq [int64]$preGitAttributesPin.bytes -and
      [IO.Path]::GetFullPath(
        (Get-LiteralAssignment $entry.ast 'PinnedGitTopAttributesPath')
      ) -eq $expectedGitTopAttributesPath -and
      [IO.Path]::GetFullPath(
        (Get-LiteralAssignment $entry.ast 'PinnedGitInfoAttributesPath')
      ) -eq $expectedGitInfoAttributesPath
    ) $pinnedAttributesPath
    $attributesPrewriteMarker = (
      "# R7S1_GIT_ATTRIBUTES_FENCE_$($entry.name.ToUpperInvariant())_PREWRITE"
    )
    $attributesFinalMarker = (
      "# R7S1_GIT_ATTRIBUTES_FENCE_$($entry.name.ToUpperInvariant())_FINAL"
    )
    $attributesPrewriteIndex = $entry.text.IndexOf(
      $attributesPrewriteMarker,[StringComparison]::Ordinal
    )
    $attributesFinalIndex = $entry.text.IndexOf(
      $attributesFinalMarker,[StringComparison]::Ordinal
    )
    Assert-Check "$($entry.name)_git_repository_attributes_fence_order" (
      $attributesPrewriteIndex -gt $entryGuardBoundary -and
      $attributesPrewriteIndex -lt [int]$entry.reservation -and
      $attributesFinalIndex -gt [int]$entry.reservation -and
      $attributesFinalIndex -lt [int]$entry.invoke -and
      ($entry.text.Split(@(
          'Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath'
        ),[StringSplitOptions]::None).Count - 1) -eq 2
    ) @($attributesPrewriteIndex,$attributesFinalIndex)
  }
  $preDockerConfigPin = Get-PropertyValue $manifest.toolchain 'docker_client_config'
  $preDockerMetadataPin = Get-PropertyValue $preDockerConfigPin 'context_metadata'
  $preKubernetesConfigPin = Get-PropertyValue $manifest.toolchain 'kubernetes_client_config'
  foreach ($entry in @(
      @{ name='outer'; ast=$outerAst; text=$outerText; invoke=$outerInvokeIndex; reservation=$outerReservationPathIndex },
      @{ name='bridge'; ast=$bridgeAst; text=$bridgeText; invoke=$runnerInvokeIndex; reservation=$bridgeReservationPathIndexEarly }
    )) {
    Assert-Check "$($entry.name)_client_configuration_literals_exact" (
      [IO.Path]::GetFullPath((Get-LiteralAssignment $entry.ast 'PinnedDockerClientConfigPath')) -eq
        [IO.Path]::GetFullPath([string]$preDockerConfigPin.path) -and
      (Get-LiteralAssignment $entry.ast 'PinnedDockerClientConfigSha256') -ceq
        ([string]$preDockerConfigPin.sha256).ToLowerInvariant() -and
      [int64](Get-LiteralAssignment $entry.ast 'PinnedDockerClientConfigBytes') -eq
        [int64]$preDockerConfigPin.bytes -and
      [IO.Path]::GetFullPath((Get-LiteralAssignment $entry.ast 'PinnedDockerContextMetadataPath')) -eq
        [IO.Path]::GetFullPath([string]$preDockerMetadataPin.path) -and
      (Get-LiteralAssignment $entry.ast 'PinnedDockerContextMetadataSha256') -ceq
        ([string]$preDockerMetadataPin.sha256).ToLowerInvariant() -and
      [int64](Get-LiteralAssignment $entry.ast 'PinnedDockerContextMetadataBytes') -eq
        [int64]$preDockerMetadataPin.bytes -and
      [IO.Path]::GetFullPath((Get-LiteralAssignment $entry.ast 'PinnedKubernetesClientConfigPath')) -eq
        [IO.Path]::GetFullPath([string]$preKubernetesConfigPin.path) -and
      (Get-LiteralAssignment $entry.ast 'PinnedKubernetesClientConfigSha256') -ceq
        ([string]$preKubernetesConfigPin.sha256).ToLowerInvariant() -and
      [int64](Get-LiteralAssignment $entry.ast 'PinnedKubernetesClientConfigBytes') -eq
        [int64]$preKubernetesConfigPin.bytes
    ) $entry.name
    $prewriteMarker = "# R7S1_CLIENT_CONFIG_FENCE_$($entry.name.ToUpperInvariant())_PREWRITE"
    $finalMarker = "# R7S1_CLIENT_CONFIG_FENCE_$($entry.name.ToUpperInvariant())_FINAL"
    $prewriteIndex = $entry.text.IndexOf($prewriteMarker,[StringComparison]::Ordinal)
    $finalIndex = $entry.text.IndexOf($finalMarker,[StringComparison]::Ordinal)
    Assert-Check "$($entry.name)_client_configuration_fence_order" (
      $prewriteIndex -ge 0 -and $prewriteIndex -lt [int]$entry.reservation -and
      $finalIndex -gt [int]$entry.reservation -and $finalIndex -lt [int]$entry.invoke -and
      ($entry.text.Split(@('Assert-ClientConfigurationPins'),[StringSplitOptions]::None).Count - 1) -eq 3
    ) @($prewriteIndex,$finalIndex)
  }
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
  $outerPathFenceIndex = $outerText.IndexOf('# R7S1_PATH_FENCE_OUTER_FINAL',[StringComparison]::Ordinal)
  Assert-Check 'outer_bound_path_fence_after_reservation_before_bridge' (
    $outerPathFenceIndex -gt $outerReservationWriteIndex -and
    $outerPathFenceIndex -lt $outerImmediateSelfIndex -and
    $outerText.Contains("Assert-BoundRunLocation `$PSScriptRoot `$PinnedStagingPath") -and
    $outerText.Contains("Assert-BoundRunLocation `$OutputDirectory `$PinnedOutputPath") -and
    $outerText.Contains("Assert-BoundRunLocation `$PinnedEmergencySealPath `$PinnedEmergencySealPath")
  ) $outerPathFenceIndex
  Assert-Check 'outer_bound_path_fence_before_any_reservation_write' (
    $outerPrewritePathFenceIndex -ge 0 -and
    $outerPrewritePathFenceIndex -lt $outerReservationPathIndex -and
    $outerPrewritePathFenceIndex -lt $outerReservationWriteIndex -and
    ($outerText.Split(@('Assert-BoundRunLocation $'),[StringSplitOptions]::None).Count - 1) -eq 6
  ) $outerPrewritePathFenceIndex

  Assert-Check 'manifest_schema' ($manifest.schema_version -eq 'evm.s8_v4.x1_phase_b2_r7s1_restore_work_order.v1') $manifest.schema_version
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
    'invoke-verified-x1-phase-b2-r6.ps1', 'invoke-verified-x1-phase-b2-r7.ps1',
    'invoke-x1-phase-b2-r3-bridge.ps1', 'invoke-x1-phase-b2-r4-bridge.ps1', 'invoke-x1-phase-b2-r5-bridge.ps1',
    'invoke-x1-phase-b2-r6-bridge.ps1', 'invoke-x1-phase-b2-r7-bridge.ps1',
    'run_x1_phase_b2_r3.py', 'run_x1_phase_b2_r4.py', 'run_x1_phase_b2_r5.py',
    'run_x1_phase_b2_r6.py', 'run_x1_phase_b2_r7.py',
    'phase_b2_r3.py', 'phase_b2_r4.py', 'phase_b2_r5.py', 'phase_b2_r6.py'
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

  # Optional/manual static recheck only; the launcher does not invoke this
  # switch and no live-gate credit may be derived from it. Return before every
  # Git/Python child-backed offline check below.
  if ($PreExecution) {
    [ordered]@{
      schema_version = 'evm.s8_v4.x1_phase_b2_r7s1_bundle_validation.v1'
      status = 'PASS'
      validation_scope = 'pre_execution_zero_child'
      validated_at = [DateTime]::UtcNow.ToString('o')
      execution_mode = $mode
      check_count = $checks.Count
      canonical_revision = $revision
      canonical_tree = $tree
      observed_sha256 = [ordered]@{
        outer = $observedOuter
        bridge = $observedBridge
        manifest = $observedManifest
      }
      checks = $checks
    } | ConvertTo-Json -Depth 14 -Compress
    exit 0
  }
  $gitToolPin = Get-PropertyValue $manifest.toolchain 'git'
  $gitRepositoryConfigPin = Get-PropertyValue $manifest.toolchain 'git_repository_config'
  $pythonToolPin = Get-PropertyValue $manifest.toolchain 'python'
  $dockerComposeToolPin = Get-PropertyValue $manifest.toolchain 'docker_compose'
  foreach ($toolPin in @(
      @{ name = 'git'; value = $gitToolPin },
      @{ name = 'python'; value = $pythonToolPin },
      @{ name = 'docker_compose'; value = $dockerComposeToolPin }
    )) {
    Assert-ExactObjectKeys "offline_$($toolPin.name)_tool_pin_keys_exact" $toolPin.value @(
      'path','sha256','bytes','version','signature'
    )
    Assert-ExactSha "offline_$($toolPin.name)_tool_sha_format" $toolPin.value.sha256
  }
  $script:GitPath = [IO.Path]::GetFullPath([string]$gitToolPin.path)
  Assert-Check 'offline_git_path_exact_canonical' (
    $script:GitPath -ceq 'C:\Program Files\Git\mingw64\bin\git.exe'
  ) $script:GitPath
  Assert-Check 'offline_git_tool_pin_live_match' (
    (Test-Path -LiteralPath $script:GitPath -PathType Leaf) -and
    (Get-Sha256 $script:GitPath) -ceq ([string]$gitToolPin.sha256).ToLowerInvariant() -and
    [IO.FileInfo]::new($script:GitPath).Length -eq [int64]$gitToolPin.bytes
  ) $script:GitPath
  Assert-ExactObjectKeys 'git_repository_config_pin_keys_exact' $gitRepositoryConfigPin @(
    'path','sha256','bytes','policy','readback'
  )
  $expectedGitConfigPath = 'C:\Users\mlops\EnterpriseMLOps_Project\.git\config'
  $gitConfigPath = [IO.Path]::GetFullPath([string]$gitRepositoryConfigPin.path)
  Assert-Check 'git_repository_config_path_exact' (
    $gitConfigPath -eq [IO.Path]::GetFullPath($expectedGitConfigPath) -and
    $gitConfigPath -eq [IO.Path]::Combine($repo,'.git','config')
  ) $gitConfigPath
  Assert-Check 'git_repository_config_production_pin_exact' (
    ([string]$gitRepositoryConfigPin.sha256).ToLowerInvariant() -ceq
      'aefce0bafe9863032f40ed1f62d91c339a321ea61303b77941ec7e36c30028fa' -and
    [int64]$gitRepositoryConfigPin.bytes -eq 787
  ) $gitRepositoryConfigPin.sha256
  $gitConfigPolicy = Get-PropertyValue $gitRepositoryConfigPin 'policy'
  Assert-ExactObjectKeys 'git_repository_config_policy_keys_exact' $gitConfigPolicy @(
    'schema','allowed_key_names','forbidden_key_classes','origin_identity','config_worktree_absent'
  )
  $expectedGitConfigKeyNames = @(
    'branch.codex/distributed-scale-validation-plan.merge',
    'branch.codex/distributed-scale-validation-plan.remote',
    'branch.codex/local-infra-mvp.merge','branch.codex/local-infra-mvp.remote',
    'branch.codex/mac-mini-worker.merge','branch.codex/mac-mini-worker.remote',
    'branch.codex/x1-resume-results-20260825-215716.merge',
    'branch.codex/x1-resume-results-20260825-215716.remote',
    'core.bare','core.filemode','core.ignorecase','core.logallrefupdates',
    'core.repositoryformatversion','core.symlinks','extensions.worktreeconfig',
    'remote.origin.fetch','remote.origin.url','user.email','user.name'
  )
  $expectedForbiddenGitConfigClasses = @(
    'include','includeif','filter','core.fsmonitor','core.attributesfile','credential','url-rewrite',
    'ssh-command','external-helper'
  )
  $gitOriginIdentity = Get-PropertyValue $gitConfigPolicy 'origin_identity'
  Assert-ExactObjectKeys 'git_repository_config_origin_identity_keys_exact' $gitOriginIdentity @(
    'scheme','host','path_sha256'
  )
  Assert-Check 'git_repository_config_policy_exact' (
    [string]$gitConfigPolicy.schema -ceq 's8-v4-x1-phase-b2-r7s1-git-config-policy/v1' -and
    @((Compare-Object $expectedGitConfigKeyNames @($gitConfigPolicy.allowed_key_names) -CaseSensitive)).Count -eq 0 -and
    @($gitConfigPolicy.allowed_key_names).Count -eq $expectedGitConfigKeyNames.Count -and
    @((Compare-Object $expectedForbiddenGitConfigClasses @($gitConfigPolicy.forbidden_key_classes) -CaseSensitive)).Count -eq 0 -and
    @($gitConfigPolicy.forbidden_key_classes).Count -eq $expectedForbiddenGitConfigClasses.Count -and
    [string]$gitOriginIdentity.scheme -ceq 'https' -and
    [string]$gitOriginIdentity.host -ceq 'github.com' -and
    [string]$gitOriginIdentity.path_sha256 -ceq
      'bc3c8d5edcc5862799d21d259324fc8f9f2b8fc6c724821ccb131e8296beba6b' -and
    $gitConfigPolicy.config_worktree_absent -eq $true
  ) $gitConfigPolicy
  $script:GitRepositoryConfigPin = $gitRepositoryConfigPin
  Assert-GitRepositoryConfigLive
  $measuredGitConfigKeyNames = @(Get-GitRepositoryConfigKeyNames $gitConfigPath)
  Assert-Check 'git_repository_config_measured_key_set_exact' (
    @((Compare-Object $expectedGitConfigKeyNames $measuredGitConfigKeyNames -CaseSensitive)).Count -eq 0 -and
    $measuredGitConfigKeyNames.Count -eq $expectedGitConfigKeyNames.Count
  ) $measuredGitConfigKeyNames
  $gitConfigReadbackPin = Get-PropertyValue $gitRepositoryConfigPin 'readback'
  Assert-ExactObjectKeys 'git_repository_config_readback_pin_keys_exact' $gitConfigReadbackPin @(
    'path','sha256','schema'
  )
  $gitConfigReadbackPath = [IO.Path]::GetFullPath([string]$gitConfigReadbackPin.path)
  Assert-Check 'git_repository_config_readback_pin_exact' (
    [string]$gitConfigReadbackPin.schema -ceq
      's8-v4-x1-phase-b2-r7s1-git-repository-config-readback/v1' -and
    [IO.File]::Exists($gitConfigReadbackPath) -and
    (Get-Sha256 $gitConfigReadbackPath) -ceq ([string]$gitConfigReadbackPin.sha256).ToLowerInvariant()
  ) $gitConfigReadbackPin
  $gitConfigReadback = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (
    [IO.File]::ReadAllText($gitConfigReadbackPath)
  )
  Assert-ExactObjectKeys 'git_repository_config_readback_keys_exact' $gitConfigReadback @(
    'schema','status','captured_at','path','sha256','bytes','key_names','origin_identity',
    'config_worktree_absent','policy_sha256'
  )
  Assert-Check 'git_repository_config_readback_projection_exact' (
    [string]$gitConfigReadback.schema -ceq [string]$gitConfigReadbackPin.schema -and
    [string]$gitConfigReadback.status -ceq 'verified' -and
    [IO.Path]::GetFullPath([string]$gitConfigReadback.path) -eq $gitConfigPath -and
    [string]$gitConfigReadback.sha256 -ceq ([string]$gitRepositoryConfigPin.sha256).ToLowerInvariant() -and
    [int64]$gitConfigReadback.bytes -eq [int64]$gitRepositoryConfigPin.bytes -and
    @((Compare-Object $expectedGitConfigKeyNames @($gitConfigReadback.key_names) -CaseSensitive)).Count -eq 0 -and
    @($gitConfigReadback.key_names).Count -eq $expectedGitConfigKeyNames.Count -and
    (($gitConfigReadback.origin_identity | ConvertTo-Json -Compress) -ceq
      ($gitOriginIdentity | ConvertTo-Json -Compress)) -and
    $gitConfigReadback.config_worktree_absent -eq $true -and
    [string]$gitConfigReadback.policy_sha256 -ceq
      'bab550019c7f342923cfd5d07faa43bb6688805f4c3a5260469192a270615bc9'
  ) $gitConfigReadback
  $gitRepositoryAttributesPin = Get-PropertyValue $manifest.toolchain 'git_repository_attributes'
  Assert-ExactObjectKeys 'git_repository_attributes_pin_keys_exact' $gitRepositoryAttributesPin @(
    'path','sha256','bytes','policy','readback'
  )
  $expectedGitAttributesPath = `
    'C:\Users\mlops\EnterpriseMLOps_Project\enterprise-vision-mlops\.gitattributes'
  $expectedGitTopAttributesPath = 'C:\Users\mlops\EnterpriseMLOps_Project\.gitattributes'
  $expectedGitInfoAttributesPath = `
    'C:\Users\mlops\EnterpriseMLOps_Project\.git\info\attributes'
  $gitAttributesPath = [IO.Path]::GetFullPath([string]$gitRepositoryAttributesPin.path)
  Assert-Check 'git_repository_attributes_path_exact' (
    $gitAttributesPath -eq [IO.Path]::GetFullPath($expectedGitAttributesPath) -and
    $gitAttributesPath -eq [IO.Path]::Combine($projectRoot,'.gitattributes') -and
    [IO.Path]::GetFullPath($expectedGitTopAttributesPath) -eq
      [IO.Path]::Combine($repo,'.gitattributes') -and
    [IO.Path]::GetFullPath($expectedGitInfoAttributesPath) -eq
      [IO.Path]::Combine($repo,'.git','info','attributes')
  ) $gitAttributesPath
  Assert-Check 'git_repository_attributes_production_pin_exact' (
    ([string]$gitRepositoryAttributesPin.sha256).ToLowerInvariant() -ceq
      'd7303b6f3a537f1a8382adcf72c0ef49e4aa15261263d8f2c70a475f24f57fa5' -and
    [int64]$gitRepositoryAttributesPin.bytes -eq 577 -and
    [IO.File]::Exists($gitAttributesPath) -and
    [IO.FileInfo]::new($gitAttributesPath).Length -eq [int64]$gitRepositoryAttributesPin.bytes -and
    (Get-Sha256 $gitAttributesPath) -ceq
      ([string]$gitRepositoryAttributesPin.sha256).ToLowerInvariant() -and
    -not [IO.File]::Exists($expectedGitTopAttributesPath) -and
    -not [IO.Directory]::Exists($expectedGitTopAttributesPath) -and
    -not [IO.File]::Exists($expectedGitInfoAttributesPath) -and
    -not [IO.Directory]::Exists($expectedGitInfoAttributesPath)
  ) $gitRepositoryAttributesPin.sha256
  $gitAttributesPolicy = Get-PropertyValue $gitRepositoryAttributesPin 'policy'
  Assert-ExactObjectKeys 'git_repository_attributes_policy_keys_exact' $gitAttributesPolicy @(
    'schema','rule_count','pattern_sha256','attribute_tokens','forbidden_attributes',
    'git_top_level_attributes_absent','git_info_attributes_absent',
    'system_attributes_disabled','child_environment','hash_object_policy'
  )
  $expectedGitAttributePatternSha256 = @(
    'e4bb14173d817b251f7aeb59c87cba83429c31a29a7d16fdd2e6a3c9b1e12db0',
    '76ed074a9305c04054cdebb9e9aad2d818052b07091de1f20cad0bbac34ffb52',
    '396b92906a5a6d2c6a0749130e9d16ffd80bdb3c053c08533f0c9776e7abe4df',
    '76880eb6ef85265f8ff1b841f1a2c28be98fa6d229cff570c9a71af9d0b614f2',
    'ec5d2ab89ac415fede59987ca0f73ebc537b316a89c11cc81a021e43257f3ad7',
    'f76b5543e080ef60847945994d07674571695b4f10fffa8fc0c721c28767846d',
    '4554e3ad9b1f453e2fbfb81ac244d499c9232f71671d8f14cf5ad13298545d63',
    'a0e09a0bd20e893dfd512fdf009dfad0937a4d2687d83ee789a912320cd2d623',
    '64999824d016021f7f629ff79b4d5930fb2a3956dda7b990e38a1e41aaaedc00',
    '751ce4b25fd592d4e0f86a8fb008f16c5705e59a73663d2a39405fc3a3030d39',
    '2b60b4c1a1cd70e2f4ade33310be82c61d8a4503ae8d55074fc752bbc9486e11',
    '2ca964ae17fe6f2b7f47f16540a299c0f7b3380f796e7ac8493bfcee7893378a',
    '21eb880d14a0ccc39dd9fc3798fbfb2d8e82101187e434e6020a575662c3c7d0',
    '36313b00defa02c2145da13d795d2d4201ed45a044b952084d5638806c8d429b',
    'd539d33f0ac4e88605ec0ced396b039f8743174ddbed63c54b9792542dc729f3'
  )
  $gitAttributesChildEnvironment = Get-PropertyValue $gitAttributesPolicy 'child_environment'
  $gitAttributesHashObjectPolicy = Get-PropertyValue $gitAttributesPolicy 'hash_object_policy'
  Assert-ExactObjectKeys 'git_repository_attributes_child_environment_keys_exact' `
    $gitAttributesChildEnvironment @('GIT_ATTR_NOSYSTEM')
  Assert-ExactObjectKeys 'git_repository_attributes_hash_object_policy_keys_exact' `
    $gitAttributesHashObjectPolicy @(
      'core_autocrlf','path_argument_required','absolute_worktree_path_required'
    )
  Assert-Check 'git_repository_attributes_policy_exact' (
    [string]$gitAttributesPolicy.schema -ceq
      's8-v4-x1-phase-b2-r7s1-git-attributes-policy/v1' -and
    [int]$gitAttributesPolicy.rule_count -eq 15 -and
    (@($gitAttributesPolicy.pattern_sha256) -join [char]31) -ceq
      ($expectedGitAttributePatternSha256 -join [char]31) -and
    (@($gitAttributesPolicy.attribute_tokens) -join [char]31) -ceq
      ('text' + [char]31 + 'eol=lf') -and
    (@($gitAttributesPolicy.forbidden_attributes) -join [char]31) -ceq
      ('filter' + [char]31 + 'diff' + [char]31 + 'merge' + [char]31 + 'working-tree-encoding') -and
    $gitAttributesPolicy.git_top_level_attributes_absent -eq $true -and
    $gitAttributesPolicy.git_info_attributes_absent -eq $true -and
    $gitAttributesPolicy.system_attributes_disabled -eq $true -and
    [string]$gitAttributesChildEnvironment.GIT_ATTR_NOSYSTEM -ceq '1' -and
    [string]$gitAttributesHashObjectPolicy.core_autocrlf -ceq 'true' -and
    $gitAttributesHashObjectPolicy.path_argument_required -eq $true -and
    $gitAttributesHashObjectPolicy.absolute_worktree_path_required -eq $true
  ) $gitAttributesPolicy
  $gitAttributesText = [IO.File]::ReadAllText(
    $gitAttributesPath,[Text.UTF8Encoding]::new($false,$true)
  )
  $measuredGitAttributePatternSha256 = [Collections.Generic.List[string]]::new()
  foreach ($rawLine in ($gitAttributesText -split "`r?`n")) {
    $line = $rawLine.Trim()
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $fields = [regex]::Split($line,'\s+')
    Assert-Check 'git_repository_attributes_rule_tokens_exact' (
      $fields.Count -eq 3 -and $fields[1] -ceq 'text' -and $fields[2] -ceq 'eol=lf'
    ) $line
    $patternHasher = [Security.Cryptography.SHA256]::Create()
    try {
      $patternDigest = ([BitConverter]::ToString(
          $patternHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($fields[0]))
        )).Replace('-','').ToLowerInvariant()
    }
    finally { $patternHasher.Dispose() }
    [void]$measuredGitAttributePatternSha256.Add($patternDigest)
  }
  Assert-Check 'git_repository_attributes_measured_rules_exact' (
    $measuredGitAttributePatternSha256.Count -eq 15 -and
    ($measuredGitAttributePatternSha256.ToArray() -join [char]31) -ceq
      ($expectedGitAttributePatternSha256 -join [char]31)
  ) $measuredGitAttributePatternSha256.ToArray()
  $script:GitRepositoryAttributesLivePin = [ordered]@{
    path=$gitAttributesPath
    sha256=([string]$gitRepositoryAttributesPin.sha256).ToLowerInvariant()
    bytes=[int64]$gitRepositoryAttributesPin.bytes
    git_top_path=[IO.Path]::GetFullPath($expectedGitTopAttributesPath)
    git_info_path=[IO.Path]::GetFullPath($expectedGitInfoAttributesPath)
  }
  Assert-GitRepositoryAttributesLive
  $gitAttributesReadbackPin = Get-PropertyValue $gitRepositoryAttributesPin 'readback'
  Assert-ExactObjectKeys 'git_repository_attributes_readback_pin_keys_exact' `
    $gitAttributesReadbackPin @('path','sha256','schema')
  $gitAttributesReadbackPath = [IO.Path]::GetFullPath([string]$gitAttributesReadbackPin.path)
  Assert-Check 'git_repository_attributes_readback_pin_exact' (
    [string]$gitAttributesReadbackPin.schema -ceq
      's8-v4-x1-phase-b2-r7s1-git-repository-attributes-readback/v1' -and
    [IO.File]::Exists($gitAttributesReadbackPath) -and
    (Get-Sha256 $gitAttributesReadbackPath) -ceq
      ([string]$gitAttributesReadbackPin.sha256).ToLowerInvariant()
  ) $gitAttributesReadbackPin
  $gitAttributesReadback = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (
    [IO.File]::ReadAllText($gitAttributesReadbackPath)
  )
  Assert-ExactObjectKeys 'git_repository_attributes_readback_keys_exact' `
    $gitAttributesReadback @(
      'schema','status','captured_at','path','sha256','bytes','rule_count',
      'pattern_sha256','attribute_tokens','forbidden_attributes_absent',
      'git_top_level_attributes_absent','git_info_attributes_absent',
      'system_attributes_disabled','policy_sha256'
    )
  Assert-Check 'git_repository_attributes_readback_projection_exact' (
    [string]$gitAttributesReadback.schema -ceq [string]$gitAttributesReadbackPin.schema -and
    [string]$gitAttributesReadback.status -ceq 'verified' -and
    [IO.Path]::GetFullPath([string]$gitAttributesReadback.path) -eq $gitAttributesPath -and
    [string]$gitAttributesReadback.sha256 -ceq
      ([string]$gitRepositoryAttributesPin.sha256).ToLowerInvariant() -and
    [int64]$gitAttributesReadback.bytes -eq [int64]$gitRepositoryAttributesPin.bytes -and
    [int]$gitAttributesReadback.rule_count -eq 15 -and
    (@($gitAttributesReadback.pattern_sha256) -join [char]31) -ceq
      ($expectedGitAttributePatternSha256 -join [char]31) -and
    (@($gitAttributesReadback.attribute_tokens) -join [char]31) -ceq
      ('text' + [char]31 + 'eol=lf') -and
    $gitAttributesReadback.forbidden_attributes_absent -eq $true -and
    $gitAttributesReadback.git_top_level_attributes_absent -eq $true -and
    $gitAttributesReadback.git_info_attributes_absent -eq $true -and
    $gitAttributesReadback.system_attributes_disabled -eq $true -and
    [string]$gitAttributesReadback.policy_sha256 -ceq
      'db24c3f5727a22a0bf36060f7fbd5cae9a4d466d38f436bc05f135b2639777c5'
  ) $gitAttributesReadback
  Set-GitEnvironmentFence
  $windowsTcbPin = Get-PropertyValue $manifest.toolchain 'windows_tcb'
  $script:GitRemoteCwd = [IO.Path]::GetDirectoryName(
    [IO.Path]::GetFullPath([string](Get-PropertyValue $windowsTcbPin 'system32_path'))
  )
  Assert-Check 'git_remote_cwd_outside_repository' (
    [IO.Directory]::Exists($script:GitRemoteCwd) -and
    -not $script:GitRemoteCwd.StartsWith(
      $repo.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )
  ) $script:GitRemoteCwd

  $dockerClientConfig = Get-PropertyValue $manifest.toolchain 'docker_client_config'
  Assert-ExactObjectKeys 'docker_client_config_keys_exact' $dockerClientConfig @(
    'path','sha256','bytes','context_metadata','policy','readback'
  )
  $dockerClientPath = [IO.Path]::GetFullPath([string]$dockerClientConfig.path)
  $dockerContextMetadata = Get-PropertyValue $dockerClientConfig 'context_metadata'
  Assert-ExactObjectKeys 'docker_context_metadata_keys_exact' $dockerContextMetadata @(
    'path','sha256','bytes'
  )
  $dockerMetadataPath = [IO.Path]::GetFullPath([string]$dockerContextMetadata.path)
  Assert-Check 'docker_client_config_pins_exact' (
    $dockerClientPath -eq 'C:\Users\opop0\.docker\config.json' -and
    [string]$dockerClientConfig.sha256 -ceq
      '7b2ec346b548b5bdf0bcd95923e800fe50ac50f0b2678e874fc18124ac5b22b6' -and
    [int64]$dockerClientConfig.bytes -eq 78 -and
    $dockerMetadataPath -eq
      'C:\Users\opop0\.docker\contexts\meta\fe9c6bd7a66301f49ca9b6a70b217107cd1284598bfc254700c989b916da791e\meta.json' -and
    [string]$dockerContextMetadata.sha256 -ceq
      '162ea41b361225a824608cf6c714d7710d69aa3c645bfbbf98104b4fce06cd09' -and
    [int64]$dockerContextMetadata.bytes -eq 318 -and
    [IO.File]::Exists($dockerClientPath) -and
    (Get-Sha256 $dockerClientPath) -ceq [string]$dockerClientConfig.sha256 -and
    [IO.File]::Exists($dockerMetadataPath) -and
    (Get-Sha256 $dockerMetadataPath) -ceq [string]$dockerContextMetadata.sha256
  ) $dockerClientPath
  $dockerTlsPath = 'C:\Users\opop0\.docker\contexts\tls\fe9c6bd7a66301f49ca9b6a70b217107cd1284598bfc254700c989b916da791e'
  Assert-Check 'docker_context_tls_material_directory_absent' (
    -not [IO.File]::Exists($dockerTlsPath) -and -not [IO.Directory]::Exists($dockerTlsPath)
  ) $dockerTlsPath
  $dockerPolicy = Get-PropertyValue $dockerClientConfig 'policy'
  Assert-ExactObjectKeys 'docker_client_config_policy_keys_exact' $dockerPolicy @(
    'schema','top_level_keys','auth_entries','credential_store_present',
    'credential_store_value_exposed','current_context','endpoint_identity',
    'tls_material_directory_absent','registry_operations_allowed','child_environment',
    'docker_global_arguments','standalone_compose_context_transport',
    'standalone_compose_required_argument_names'
  )
  $dockerEndpoint = Get-PropertyValue $dockerPolicy 'endpoint_identity'
  $dockerChildEnvironment = Get-PropertyValue $dockerPolicy 'child_environment'
  $dockerSetVariables = Get-PropertyValue $dockerChildEnvironment 'set_variables'
  Assert-Check 'docker_client_config_policy_exact' (
    [string]$dockerPolicy.schema -ceq 's8-v4-x1-phase-b2-r7s1-docker-client-config-policy/v1' -and
    (@($dockerPolicy.top_level_keys) -join [char]31) -ceq 'auths' + [char]31 + 'credsStore' + [char]31 + 'currentContext' -and
    [int]$dockerPolicy.auth_entries -eq 0 -and
    $dockerPolicy.credential_store_present -eq $true -and
    $dockerPolicy.credential_store_value_exposed -eq $false -and
    [string]$dockerPolicy.current_context -ceq 'desktop-linux' -and
    [string]$dockerEndpoint.scheme -ceq 'npipe' -and
    [string]$dockerEndpoint.endpoint_sha256 -ceq
      '30341252ca9aa2b298da11cd8527fdfbf8ab30a2f3b5a3c871188c778b20af30' -and
    $dockerEndpoint.skip_tls_verify -eq $false -and
    $dockerPolicy.tls_material_directory_absent -eq $true -and
    $dockerPolicy.registry_operations_allowed -eq $false -and
    (@($dockerChildEnvironment.scrub_prefixes) -join ',') -ceq 'COMPOSE_,DOCKER_' -and
    (@($dockerChildEnvironment.scrub_names) -join ',') -ceq
      'ALL_PROXY,CURL_CA_BUNDLE,HTTP_PROXY,HTTPS_PROXY,NO_PROXY,SSL_CERT_DIR,SSL_CERT_FILE' -and
    $dockerChildEnvironment.case_insensitive -eq $true -and
    [string]$dockerSetVariables.DOCKER_CONFIG -ceq 'C:\Users\opop0\.docker' -and
    [string]$dockerSetVariables.DOCKER_CONTEXT -ceq 'desktop-linux' -and
    [string]$dockerSetVariables.DOCKER_CLI_HINTS -ceq 'false' -and
    [string]$dockerSetVariables.COMPOSE_DISABLE_ENV_FILE -ceq '1' -and
    [string]$dockerSetVariables.COMPOSE_ANSI -ceq 'never' -and
    [string]$dockerSetVariables.COMPOSE_PROGRESS -ceq 'plain' -and
    (@($dockerPolicy.docker_global_arguments) -join [char]31) -ceq
      '--config' + [char]31 + 'C:\Users\opop0\.docker' + [char]31 + '--context' + [char]31 + 'desktop-linux' -and
    [string]$dockerPolicy.standalone_compose_context_transport -ceq 'child_environment_only' -and
    (@($dockerPolicy.standalone_compose_required_argument_names) -join ',') -ceq
      '-p,-f,--project-directory'
  ) $dockerPolicy
  $dockerReadbackPin = Get-PropertyValue $dockerClientConfig 'readback'
  $dockerReadbackPath = [IO.Path]::GetFullPath([string]$dockerReadbackPin.path)
  Assert-Check 'docker_client_config_readback_pin_exact' (
    [string]$dockerReadbackPin.schema -ceq
      's8-v4-x1-phase-b2-r7s1-docker-client-config-readback/v1' -and
    [IO.File]::Exists($dockerReadbackPath) -and
    (Get-Sha256 $dockerReadbackPath) -ceq [string]$dockerReadbackPin.sha256
  ) $dockerReadbackPin
  $dockerReadback = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (
    [IO.File]::ReadAllText($dockerReadbackPath)
  )
  Assert-ExactObjectKeys 'docker_client_config_readback_keys_exact' $dockerReadback @(
    'schema','status','captured_at','path','sha256','bytes','top_level_keys','auth_entries',
    'credential_store_present','credential_store_value_exposed','current_context',
    'context_metadata','endpoint_identity','tls_material_directory_absent','policy_sha256'
  )
  Assert-Check 'docker_client_config_readback_projection_exact' (
    [string]$dockerReadback.status -ceq 'verified' -and
    [string]$dockerReadback.path -ceq [string]$dockerClientConfig.path -and
    [string]$dockerReadback.sha256 -ceq [string]$dockerClientConfig.sha256 -and
    [int64]$dockerReadback.bytes -eq [int64]$dockerClientConfig.bytes -and
    [int]$dockerReadback.auth_entries -eq 0 -and
    $dockerReadback.credential_store_present -eq $true -and
    $dockerReadback.credential_store_value_exposed -eq $false -and
    [string]$dockerReadback.current_context -ceq 'desktop-linux' -and
    $dockerReadback.tls_material_directory_absent -eq $true -and
    [string]$dockerReadback.policy_sha256 -ceq
      'd89c8fef52f897450814e443e489dabe2456b05dbbe2b79468eeb2361704a453'
  ) $dockerReadback

  $kubernetesClientConfig = Get-PropertyValue $manifest.toolchain 'kubernetes_client_config'
  Assert-ExactObjectKeys 'kubernetes_client_config_keys_exact' $kubernetesClientConfig @(
    'path','sha256','bytes','policy','readback'
  )
  $kubernetesClientPath = [IO.Path]::GetFullPath([string]$kubernetesClientConfig.path)
  Assert-Check 'kubernetes_client_config_pins_exact' (
    $kubernetesClientPath -eq 'C:\Users\opop0\.kube\config' -and
    [string]$kubernetesClientConfig.sha256 -ceq
      '0d9a540954fb7b9b1bf016cffd399022d1d19f2bd0617a0562912611edf9d085' -and
    [int64]$kubernetesClientConfig.bytes -eq 5692 -and
    [IO.File]::Exists($kubernetesClientPath) -and
    (Get-Sha256 $kubernetesClientPath) -ceq [string]$kubernetesClientConfig.sha256
  ) $kubernetesClientPath
  $kubernetesPolicy = Get-PropertyValue $kubernetesClientConfig 'policy'
  $kubeContextIdentity = Get-PropertyValue $kubernetesPolicy 'context_identity'
  $kubeClusterIdentity = Get-PropertyValue $kubernetesPolicy 'cluster_identity'
  $kubeServerIdentity = Get-PropertyValue $kubeClusterIdentity 'server_identity'
  $kubeUserIdentity = Get-PropertyValue $kubernetesPolicy 'user_identity'
  $kubeChildEnvironment = Get-PropertyValue $kubernetesPolicy 'child_environment'
  Assert-Check 'kubernetes_client_config_policy_exact' (
    [string]$kubernetesPolicy.schema -ceq
      's8-v4-x1-phase-b2-r7s1-kubernetes-client-config-policy/v1' -and
    [string]$kubernetesPolicy.current_context -ceq 'docker-desktop' -and
    [int]$kubernetesPolicy.object_counts.contexts -eq 1 -and
    [int]$kubernetesPolicy.object_counts.clusters -eq 1 -and
    [int]$kubernetesPolicy.object_counts.users -eq 1 -and
    [string]$kubeContextIdentity.name -ceq 'docker-desktop' -and
    [string]$kubeContextIdentity.cluster -ceq 'docker-desktop' -and
    [string]$kubeContextIdentity.user -ceq 'docker-desktop' -and
    [string]$kubeClusterIdentity.name -ceq 'docker-desktop' -and
    [string]$kubeServerIdentity.scheme -ceq 'https' -and
    [string]$kubeServerIdentity.host -ceq 'kubernetes.docker.internal' -and
    [int]$kubeServerIdentity.port -eq 6443 -and
    [string]$kubeServerIdentity.server_sha256 -ceq
      'd963afe1090a97c0b5c0fe1bc6fe3a44637e469418675ed817bf676970ebde84' -and
    [string]$kubeUserIdentity.name -ceq 'docker-desktop' -and
    (@($kubernetesPolicy.forbidden_fields_absent) -join ',') -ceq
      'exec,auth-provider,proxy-url,token,username,password' -and
    $kubernetesPolicy.multiple_config_merge_forbidden -eq $true -and
    $kubernetesPolicy.embedded_material_presence.certificate_authority_data -eq $true -and
    $kubernetesPolicy.embedded_material_presence.client_certificate_data -eq $true -and
    $kubernetesPolicy.embedded_material_presence.client_key_data -eq $true -and
    $kubernetesPolicy.embedded_material_presence.serialized_values -eq $false -and
    (@($kubeChildEnvironment.scrub_prefixes) -join ',') -ceq 'KUBE,SSH_' -and
    (@($kubeChildEnvironment.scrub_names) -join ',') -ceq
      'ALL_PROXY,CURL_CA_BUNDLE,GIT_ASKPASS,HTTP_PROXY,HTTPS_PROXY,NO_PROXY,SSL_CERT_DIR,SSL_CERT_FILE,SSH_ASKPASS' -and
    (@($kubeChildEnvironment.scrub_suffixes) -join ',') -ceq 'ASKPASS' -and
    [string]$kubeChildEnvironment.set_variables.KUBECONFIG -ceq 'C:\Users\opop0\.kube\config' -and
    (@($kubernetesPolicy.required_global_arguments) -join [char]31) -ceq
      '--kubeconfig' + [char]31 + 'C:\Users\opop0\.kube\config' + [char]31 +
      '--context' + [char]31 + 'docker-desktop' + [char]31 + '--request-timeout=8s'
  ) $kubernetesPolicy
  $kubernetesReadbackPin = Get-PropertyValue $kubernetesClientConfig 'readback'
  $kubernetesReadbackPath = [IO.Path]::GetFullPath([string]$kubernetesReadbackPin.path)
  Assert-Check 'kubernetes_client_config_readback_pin_exact' (
    [string]$kubernetesReadbackPin.schema -ceq
      's8-v4-x1-phase-b2-r7s1-kubernetes-client-config-readback/v1' -and
    [IO.File]::Exists($kubernetesReadbackPath) -and
    (Get-Sha256 $kubernetesReadbackPath) -ceq [string]$kubernetesReadbackPin.sha256
  ) $kubernetesReadbackPin
  $kubernetesReadback = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (
    [IO.File]::ReadAllText($kubernetesReadbackPath)
  )
  Assert-ExactObjectKeys 'kubernetes_client_config_readback_keys_exact' $kubernetesReadback @(
    'schema','status','captured_at','path','sha256','bytes','current_context','object_counts',
    'context_identity','cluster_identity','user_identity','forbidden_fields_absent',
    'multiple_config_merge_forbidden','embedded_material_presence','policy_sha256'
  )
  Assert-Check 'kubernetes_client_config_readback_projection_exact' (
    [string]$kubernetesReadback.status -ceq 'verified' -and
    [string]$kubernetesReadback.path -ceq [string]$kubernetesClientConfig.path -and
    [string]$kubernetesReadback.sha256 -ceq [string]$kubernetesClientConfig.sha256 -and
    [int64]$kubernetesReadback.bytes -eq [int64]$kubernetesClientConfig.bytes -and
    [string]$kubernetesReadback.current_context -ceq 'docker-desktop' -and
    $kubernetesReadback.multiple_config_merge_forbidden -eq $true -and
    [string]$kubernetesReadback.policy_sha256 -ceq
      'b08ec73c1df2c1c5df5cd3e4f2e0565a7775293fbe189ee0554a7e8f288230a1'
  ) $kubernetesReadback
  Assert-Check 'offline_python_tool_pin_live_match' (
    [IO.Path]::GetFullPath([string]$pythonToolPin.path) -eq $pythonPath -and
    (Get-Sha256 $pythonPath) -ceq ([string]$pythonToolPin.sha256).ToLowerInvariant() -and
    [IO.FileInfo]::new($pythonPath).Length -eq [int64]$pythonToolPin.bytes
  ) $pythonPath
  $dockerComposePath = [IO.Path]::GetFullPath([string]$dockerComposeToolPin.path)
  Assert-Check 'offline_docker_compose_direct_path_exact' (
    $dockerComposePath -ceq
      'C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe'
  ) $dockerComposePath
  Assert-Check 'offline_docker_compose_tool_pin_live_match' (
    (Test-Path -LiteralPath $dockerComposePath -PathType Leaf) -and
    (Get-Sha256 $dockerComposePath) -ceq
      ([string]$dockerComposeToolPin.sha256).ToLowerInvariant() -and
    [IO.FileInfo]::new($dockerComposePath).Length -eq [int64]$dockerComposeToolPin.bytes
  ) $dockerComposePath

  $containerPsql = Get-PropertyValue $manifest.toolchain 'container_psql'
  Assert-ExactObjectKeys 'container_psql_keys_exact' $containerPsql @(
    'container_name','image_digest','realpath','sha256','bytes','version','execution_scope','readback'
  )
  $containerPsqlScope = Get-PropertyValue $containerPsql 'execution_scope'
  Assert-ExactObjectKeys 'container_psql_execution_scope_keys_exact' $containerPsqlScope @(
    'schema','windows_job_accounting','docker_daemon_container_exec_tcb',
    'linux_container_descendants_job_accounted','command_policy',
    'timeout_or_residual_followup_allowed'
  )
  Assert-Check 'container_psql_execution_scope_exact' (
    [string]$containerPsqlScope.schema -ceq
      's8-v4-x1-phase-b2-r7s1-docker-container-exec-tcb/v1' -and
    [string]$containerPsqlScope.windows_job_accounting -ceq
      'docker_cli_and_windows_descendants_only' -and
    $containerPsqlScope.docker_daemon_container_exec_tcb -eq $true -and
    $containerPsqlScope.linux_container_descendants_job_accounted -eq $false -and
    [string]$containerPsqlScope.command_policy -ceq
      'exact_read_only_psql_select_allowlist_no_psqlrc' -and
    $containerPsqlScope.timeout_or_residual_followup_allowed -eq $false
  ) $containerPsqlScope
  $containerPsqlReadbackPin = Get-PropertyValue $containerPsql 'readback'
  Assert-ExactObjectKeys 'container_psql_readback_pin_keys_exact' $containerPsqlReadbackPin @(
    'path','sha256','schema'
  )
  $containerPsqlReadbackPath = [IO.Path]::GetFullPath([string]$containerPsqlReadbackPin.path)
  Assert-Check 'container_psql_readback_pin_exact' (
    [string]$containerPsqlReadbackPin.schema -ceq
      's8-v4-x1-phase-b2-r7s1-container-psql-readback/v1' -and
    (Test-Path -LiteralPath $containerPsqlReadbackPath -PathType Leaf) -and
    (Get-Sha256 $containerPsqlReadbackPath) -ceq
      ([string]$containerPsqlReadbackPin.sha256).ToLowerInvariant()
  ) $containerPsqlReadbackPin
  $containerPsqlReadback = [IO.File]::ReadAllText($containerPsqlReadbackPath) |
    ConvertFrom-Json -ErrorAction Stop
  Assert-ExactObjectKeys 'container_psql_readback_keys_exact' $containerPsqlReadback @(
    'schema','status','captured_at','container_name','image_digest','realpath','sha256',
    'bytes','version','execution_scope'
  )
  Assert-Check 'container_psql_readback_projection_exact' (
    [string]$containerPsqlReadback.schema -ceq [string]$containerPsqlReadbackPin.schema -and
    [string]$containerPsqlReadback.status -ceq 'verified' -and
    [string]$containerPsqlReadback.container_name -ceq [string]$containerPsql.container_name -and
    [string]$containerPsqlReadback.image_digest -ceq [string]$containerPsql.image_digest -and
    [string]$containerPsqlReadback.realpath -ceq [string]$containerPsql.realpath -and
    [string]$containerPsqlReadback.sha256 -ceq [string]$containerPsql.sha256 -and
    [int64]$containerPsqlReadback.bytes -eq [int64]$containerPsql.bytes -and
    [string]$containerPsqlReadback.version -ceq [string]$containerPsql.version -and
    (($containerPsqlReadback.execution_scope | ConvertTo-Json -Depth 8 -Compress) -ceq
      ($containerPsqlScope | ConvertTo-Json -Depth 8 -Compress))
  ) $containerPsqlReadback
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
    Assert-ExactObjectKeys "manifest_${name}_keys_exact" $runtimeEntry @(
      'path','sha256','worktree_blob_oid','head_blob_oid','bytes'
    )
    $pathValue = [string](Get-PropertyValue $runtimeEntry 'path')
    $shaValue = ([string](Get-PropertyValue $runtimeEntry 'sha256')).ToLowerInvariant()
    $worktreeBlobValue = ([string](Get-PropertyValue $runtimeEntry 'worktree_blob_oid')).ToLowerInvariant()
    $headBlobValue = ([string](Get-PropertyValue $runtimeEntry 'head_blob_oid')).ToLowerInvariant()
    Assert-ExactSha "manifest_${name}_sha_format" $shaValue
    Assert-ExactBlob "manifest_${name}_worktree_blob_format" $worktreeBlobValue
    Assert-ExactBlob "manifest_${name}_head_blob_format" $headBlobValue
    $componentPath = [IO.Path]::GetFullPath($pathValue)
    Assert-Check "${name}_is_inside_repository" ($componentPath.StartsWith($repo.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) $componentPath
    Assert-Check "${name}_exists" (Test-Path -LiteralPath $componentPath -PathType Leaf) $componentPath
    $observedSha = Get-Sha256 $componentPath
    Assert-Check "manifest_${name}_sha" ($observedSha -eq $shaValue) $observedSha
    $observedHeadBlob = Get-GitBlobOid $revision $componentPath $gitTop
    $observedWorktreeBlob = Get-WorktreeBlobOid $componentPath
    $observedNormalizedWorktreeBlob = Get-GitNormalizedWorktreeBlobOid $componentPath $gitTop
    Assert-Check "manifest_${name}_head_blob" ($observedHeadBlob -eq $headBlobValue) $observedHeadBlob
    Assert-Check "manifest_${name}_worktree_blob" (
      $observedWorktreeBlob -eq $worktreeBlobValue
    ) $observedWorktreeBlob
    Assert-Check "manifest_${name}_normalized_worktree_matches_head_blob" (
      $observedNormalizedWorktreeBlob -eq $headBlobValue
    ) $observedNormalizedWorktreeBlob
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
  $remoteText = Invoke-GitRemoteRead $expectedBranch
  $remoteRevision = @($remoteText -split '\s+')[0].ToLowerInvariant()
  $trackedStatus = Invoke-GitRead @('status', '--porcelain=v1', '--untracked-files=no')
  $untrackedStatus = Invoke-GitRead @(
    '-c', 'core.quotepath=false', 'status', '--porcelain=v1', '-z', '--untracked-files=all'
  )
  $untrackedPaths = [Collections.Generic.List[string]]::new()
  foreach ($record in @($untrackedStatus -split "`0" | Where-Object { $_.Length -gt 0 })) {
    Assert-Check 'untracked_status_record_exact' (
      $record.StartsWith('?? ', [StringComparison]::Ordinal)
    ) $record
    [void]$untrackedPaths.Add($record.Substring(3))
  }
  $orderedUntrackedPaths = @($untrackedPaths | Sort-Object -CaseSensitive)
  $untrackedHasher = [Security.Cryptography.SHA256]::Create()
  $untrackedStream = [IO.MemoryStream]::new()
  try {
    foreach ($pathEntry in $orderedUntrackedPaths) {
      $pathBytes = [Text.Encoding]::UTF8.GetBytes($pathEntry)
      $untrackedStream.Write($pathBytes, 0, $pathBytes.Length)
      $untrackedStream.WriteByte(0)
    }
    $untrackedDigest = (-join @(
        $untrackedHasher.ComputeHash($untrackedStream.ToArray()) |
          ForEach-Object { $_.ToString('x2') }
      ))
  }
  finally {
    $untrackedStream.Dispose()
    $untrackedHasher.Dispose()
  }
  $untrackedCount = $orderedUntrackedPaths.Count
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
    @($manifest.parent_checkpoints).Count -eq 10
  ) @($manifest.parent_checkpoints).Count
  Assert-Check 'collector_call_contract_count_mismatch' (
    [int]$manifest.call_contract.collectors.windows_fresh_collector -eq 0 -and
    [int]$manifest.call_contract.collectors.wsl_fresh_collector -eq 0
  ) $manifest.call_contract.collectors

  $external = Get-PropertyValue $manifest 'external_terminal_fencing'
  Assert-ExactObjectKeys 'external_terminal_fencing_keys_exact' $external @(
    'target_source','target_identity','successor_binding','decision_authority','snapshots',
    'exact_link_scans','terminal_decision','trusted_checkpoint'
  )
  Assert-Check 'external_terminal_fencing_source_exact' (
    [string]$external.target_source -ceq 'mlflow_running_rows'
  ) $external.target_source
  Assert-Check 'external_terminal_fencing_authority_exact' (
    [string]$external.decision_authority -ceq
      'phase-b2-r7s1-independent-terminal-fencing-review'
  ) $external.decision_authority
  Assert-ExactObjectKeys 'external_terminal_fencing_identity_keys_exact' $external.target_identity @(
    'run_id','status','lifecycle_stage','start_time','end_time'
  )
  Assert-ExactObjectKeys 'external_terminal_fencing_successor_binding_keys_exact' $external.successor_binding @(
    'run_id','attempt_id','commit','tree','nonce','parent_map_sha256',
    'staging_path','output_path','emergency_seal_path'
  )
  $attemptGuid = [Guid]::Empty
  $attemptId = [string]$external.successor_binding.attempt_id
  $attemptValid = [Guid]::TryParseExact($attemptId, 'D', [ref]$attemptGuid)
  $expectedEmergencyPath = Join-Path (
    [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath([string]$manifest.output.path))
  ) ("$([string]$manifest.bundle_id)-emergency-seal")
  Assert-Check 'external_terminal_fencing_successor_binding_exact' (
    [string]$external.successor_binding.run_id -ceq [string]$manifest.bundle_id -and
    $attemptValid -and $attemptGuid.ToString('D') -ceq $attemptId -and
    $attemptId -cne [string]$manifest.bundle_id -and
    [string]$external.successor_binding.commit -ceq $revision -and
    [string]$external.successor_binding.tree -ceq $tree -and
    [string]$external.successor_binding.nonce -cmatch '^[0-9a-f]{64}$' -and
    [string]$external.successor_binding.parent_map_sha256 -cmatch '^[0-9a-f]{64}$' -and
    [IO.Path]::GetFullPath([string]$external.successor_binding.staging_path) -eq $bundle -and
    [IO.Path]::GetFullPath([string]$external.successor_binding.output_path) -eq
      [IO.Path]::GetFullPath([string]$manifest.output.path) -and
    [IO.Path]::GetFullPath([string]$external.successor_binding.emergency_seal_path) -eq
      [IO.Path]::GetFullPath($expectedEmergencyPath)
  ) $external.successor_binding
  Assert-Check 'external_terminal_fencing_identity_exact_state' (
    [string]$external.target_identity.run_id -cmatch '^[0-9a-f]{32}$' -and
    [string]$external.target_identity.status -ceq 'RUNNING' -and
    [string]$external.target_identity.lifecycle_stage -ceq 'active' -and
    -not [string]::IsNullOrWhiteSpace([string]$external.target_identity.start_time) -and
    [string]$external.target_identity.end_time -ceq ''
  ) $external.target_identity
  Assert-Check 'external_terminal_fencing_snapshot_count_exact' (
    @($external.snapshots).Count -eq 2
  ) @($external.snapshots).Count
  Assert-Check 'external_terminal_fencing_link_scan_count_exact' (
    @($external.exact_link_scans).Count -eq 2
  ) @($external.exact_link_scans).Count
  Assert-Check 'external_terminal_fencing_decision_required' ($null -ne $external.terminal_decision) 'present'
  Assert-Check 'external_terminal_fencing_checkpoint_required' ($null -ne $external.trusted_checkpoint) 'present'
  Assert-Check 'external_terminal_fencing_expected_checkpoint_sha_exact' (
    ([string]$external.trusted_checkpoint.sha256).ToLowerInvariant() -ceq
      $ExpectedTrustedCheckpointSha256.ToLowerInvariant()
  ) $ExpectedTrustedCheckpointSha256
  $externalPaths = [Collections.Generic.List[string]]::new()
  foreach ($pin in @($external.snapshots) + @($external.exact_link_scans)) {
    Assert-ExactObjectKeys 'external_terminal_fencing_source_pin_keys_exact' $pin @(
      'path','sha256','schema','captured_at','ordinal','source_revision'
    )
    Assert-ExactSha 'external_terminal_fencing_pin_sha_format' $pin.sha256
    $pinPath = [IO.Path]::GetFullPath([string]$pin.path)
    Assert-Check 'external_terminal_fencing_pin_exists' (Test-Path -LiteralPath $pinPath -PathType Leaf) $pinPath
    Assert-Check 'external_terminal_fencing_pin_sha_exact' (
      (Get-Sha256 $pinPath) -ceq ([string]$pin.sha256).ToLowerInvariant()
    ) $pin.sha256
    Assert-Check 'external_terminal_fencing_pin_outside_bundle' (
      -not $pinPath.StartsWith($bundle.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
    ) $pinPath
    [void]$externalPaths.Add($pinPath)
  }
  foreach ($pin in @($external.terminal_decision, $external.trusted_checkpoint)) {
    Assert-ExactObjectKeys 'external_terminal_fencing_authority_pin_keys_exact' $pin @(
      'path','sha256','schema'
    )
    Assert-ExactSha 'external_terminal_fencing_pin_sha_format' $pin.sha256
    $pinPath = [IO.Path]::GetFullPath([string]$pin.path)
    Assert-Check 'external_terminal_fencing_pin_exists' (Test-Path -LiteralPath $pinPath -PathType Leaf) $pinPath
    Assert-Check 'external_terminal_fencing_pin_sha_exact' (
      (Get-Sha256 $pinPath) -ceq ([string]$pin.sha256).ToLowerInvariant()
    ) $pin.sha256
    Assert-Check 'external_terminal_fencing_pin_outside_bundle' (
      -not $pinPath.StartsWith($bundle.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
    ) $pinPath
    [void]$externalPaths.Add($pinPath)
  }
  Assert-Check 'external_terminal_fencing_paths_distinct' (
    @($externalPaths | Select-Object -Unique).Count -eq 6
  ) $externalPaths
  $decisionPayload = [IO.File]::ReadAllText([string]$external.terminal_decision.path) |
    ConvertFrom-Json -ErrorAction Stop
  Assert-ExactObjectKeys 'external_terminal_fencing_decision_keys_exact' $decisionPayload @(
    'schema','target_source','target_identity','successor_binding','decision',
    'decision_authority','issued_at','future_dispatch_fenced','supporting_sha256'
  )
  Assert-Check 'external_terminal_fencing_decision_exact' (
    [string]$decisionPayload.schema -ceq 's8-v4-x1-phase-b2-r7s1-terminal-fencing-decision/v1' -and
    [string]$decisionPayload.target_source -ceq 'mlflow_running_rows' -and
    [string]$decisionPayload.decision -ceq 'proven_terminal_fenced' -and
    [string]$decisionPayload.decision_authority -ceq [string]$external.decision_authority -and
    $decisionPayload.future_dispatch_fenced -eq $true -and
    (($decisionPayload.successor_binding | ConvertTo-Json -Depth 8 -Compress) -ceq
      ($external.successor_binding | ConvertTo-Json -Depth 8 -Compress))
  ) $decisionPayload
  Assert-ExactObjectKeys 'external_terminal_fencing_decision_support_keys_exact' $decisionPayload.supporting_sha256 @(
    'historical_snapshot_1','historical_snapshot_2','exact_link_scan_1','exact_link_scan_2',
    'historical_snapshot_1_target_activity_sha256',
    'historical_snapshot_2_target_activity_sha256','successor_binding_sha256'
  )
  Assert-ExactSha 'external_terminal_fencing_successor_binding_support_sha' (
    $decisionPayload.supporting_sha256.successor_binding_sha256
  )
  $checkpointPayload = [IO.File]::ReadAllText([string]$external.trusted_checkpoint.path) |
    ConvertFrom-Json -ErrorAction Stop
  Assert-ExactObjectKeys 'external_terminal_fencing_checkpoint_keys_exact' $checkpointPayload @(
    'schema','checkpointed_at','expires_at','decision_authority','independent_approval',
    'successor_binding','target_source','target_identity_sha256','decision_sha256',
    'supporting_sha256','fence_readback'
  )
  Assert-Check 'external_terminal_fencing_checkpoint_exact' (
    [string]$checkpointPayload.schema -ceq
      's8-v4-x1-phase-b2-r7s1-trusted-terminal-fencing-checkpoint/v1' -and
    [string]$checkpointPayload.decision_authority -ceq [string]$external.decision_authority -and
    [string]$checkpointPayload.decision_sha256 -ceq
      ([string]$external.terminal_decision.sha256).ToLowerInvariant() -and
    $checkpointPayload.fence_readback.future_dispatch_fenced -eq $true -and
    [string]$checkpointPayload.fence_readback.fence_state -ceq 'fenced'
  ) $checkpointPayload

  $timeoutNames = @(
    'kubectl_timeout_seconds', 'wrapper_timeout_seconds',
    'restore_deadline_seconds', 'residual_repoll_seconds', 'stream_drain_seconds'
  )
  $previousPythonPath = $env:PYTHONPATH
  $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
  try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $runtimeProbe = 'import json,sys;sys.path.insert(0,sys.argv[1]);from dataclasses import asdict;from evm.scale_validation.phase_b2_r7_process import TimeoutContract;print(json.dumps(asdict(TimeoutContract()),sort_keys=True))'
    $runtimeOutput = @(
      & $pythonPath -I -S -B -c $runtimeProbe (Join-Path $projectRoot 'src') 2>&1
    )
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
  Assert-ExactObjectKeys 'process_containment_keys_exact' $manifest.process_containment @(
    'provider','create_suspended','assign_before_resume','breakaway_allowed','kill_on_job_close',
    'terminate_job_object_allowed','job_accounting_authoritative','stdio_drain_before_followup',
    'residual_repoll_seconds','force_termination_attempts','wsl_run_uuid_and_process_group',
    'wsl_proc_residual_check','scope_boundaries'
  )
  Assert-Check 'process_containment_no_forced_termination' (
    [string]$manifest.process_containment.provider -ceq 'windows_job_object' -and
    $manifest.process_containment.create_suspended -eq $true -and
    $manifest.process_containment.assign_before_resume -eq $true -and
    $manifest.process_containment.breakaway_allowed -eq $false -and
    $manifest.process_containment.kill_on_job_close -eq $false -and
    $manifest.process_containment.terminate_job_object_allowed -eq $false -and
    [int64]$manifest.process_containment.force_termination_attempts -eq 0
  ) $manifest.process_containment
  $scopeBoundaries = Get-PropertyValue $manifest.process_containment 'scope_boundaries'
  Assert-ExactObjectKeys 'process_containment_scope_boundary_keys_exact' $scopeBoundaries @(
    'windows','wsl','docker_container_exec'
  )
  Assert-ExactObjectKeys 'process_containment_windows_scope_keys_exact' $scopeBoundaries.windows @(
    'scope','accounting','wsl_linux_descendants_job_accounted',
    'container_linux_descendants_job_accounted'
  )
  Assert-Check 'process_containment_windows_scope_exact' (
    [string]$scopeBoundaries.windows.scope -ceq 'windows_job_object' -and
    [string]$scopeBoundaries.windows.accounting -ceq
      'windows_root_child_grandchild_reparent_only' -and
    $scopeBoundaries.windows.wsl_linux_descendants_job_accounted -eq $false -and
    $scopeBoundaries.windows.container_linux_descendants_job_accounted -eq $false
  ) $scopeBoundaries.windows
  Assert-ExactObjectKeys 'process_containment_wsl_scope_keys_exact' $scopeBoundaries.wsl @(
    'scope','windows_job_accounting','linux_descendants_job_accounted','post_scan_required'
  )
  Assert-Check 'process_containment_wsl_scope_exact' (
    [string]$scopeBoundaries.wsl.scope -ceq 'wsl_uuid_process_group' -and
    [string]$scopeBoundaries.wsl.windows_job_accounting -ceq 'wsl_launcher_only' -and
    $scopeBoundaries.wsl.linux_descendants_job_accounted -eq $false -and
    $scopeBoundaries.wsl.post_scan_required -eq $true
  ) $scopeBoundaries.wsl
  Assert-Check 'process_containment_docker_scope_exact' (
    (($scopeBoundaries.docker_container_exec | ConvertTo-Json -Depth 8 -Compress) -ceq
      ($containerPsqlScope | ConvertTo-Json -Depth 8 -Compress))
  ) $scopeBoundaries.docker_container_exec

  $b0Uid = [string]$manifest.expected_state.b0.uid
  Assert-ExactObjectKeys 'b0_keys_exact' $manifest.expected_state.b0 @(
    'uid','uid_basis','image','ready_url','predict_url','sample_image_uri'
  )
  Assert-Check 'b0_uid_well_formed' ($b0Uid -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') $b0Uid
  Assert-Check 'b0_uid_exact' ($b0Uid -ceq 'cfdab424-dcc5-4d5f-a46f-ae7530441ef4') $b0Uid
  Assert-Check 'b0_image_exact' (
    [string]$manifest.expected_state.b0.image -ceq 'enterprise-vision-mlops-efficientnet-serving@sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a'
  ) $manifest.expected_state.b0.image
  Assert-Check 'b0_identity_endpoints_and_sample_exact' (
    [string]$manifest.expected_state.b0.uid_basis -ceq
      'tracked canonical status evidence predating r4 and immutable deployment identity' -and
    [string]$manifest.expected_state.b0.ready_url -ceq 'http://127.0.0.1:30800/ready' -and
    [string]$manifest.expected_state.b0.predict_url -ceq 'http://127.0.0.1:30800/predict' -and
    [string]$manifest.expected_state.b0.sample_image_uri -ceq
      '/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG'
  ) $manifest.expected_state.b0
  $expectedPrometheusJobs = @(
    'evm-api','evm-b0-production','evm-otel-collector','evm-task-queue-worker','prometheus'
  )
  $expectedResiduePaths = @(
    'F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/prometheus-targets/s8-v4-x1-triton.json',
    'F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/prometheus-targets/s8-v4-x1-api.json'
  )
  Assert-Check 'runtime_endpoint_and_residue_pins_exact' (
    [string]$manifest.expected_state.api_base_url -ceq 'http://127.0.0.1:8000' -and
    [string]$manifest.expected_state.prometheus_targets_url -ceq
      'http://127.0.0.1:9090/api/v1/targets' -and
    @(Compare-Object $expectedPrometheusJobs @($manifest.expected_state.prometheus_jobs)).Count -eq 0 -and
    @($manifest.expected_state.prometheus_jobs).Count -eq $expectedPrometheusJobs.Count -and
    [string]$manifest.expected_state.gpu_lease_path -ceq
      'F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json' -and
    @($manifest.expected_state.active_job_roots).Count -eq 0 -and
    @($manifest.expected_state.active_claim_roots).Count -eq 0 -and
    @(Compare-Object $expectedResiduePaths @($manifest.expected_state.x1_residue_paths)).Count -eq 0 -and
    @($manifest.expected_state.x1_residue_paths).Count -eq $expectedResiduePaths.Count -and
    [string]$manifest.expected_state.x1_docker_name_filter -ceq 'name=evm-x1' -and
    ((@($manifest.expected_state.x1_ports) -join ',') -ceq '31120,31121,31122')
  ) $manifest.expected_state

  $compose = Get-PropertyValue $manifest.expected_state 'compose'
  Assert-ExactObjectKeys 'compose_keys_exact' $compose @(
    'project_name', 'config_path', 'config_sha256', 'long_lived_services',
    'one_shot_services', 'service_pins', 'stability'
  )
  Assert-Check 'compose_project_exact' ([string]$compose.project_name -ceq 'enterprise-vision-mlops') $compose.project_name
  $composePath = [IO.Path]::GetFullPath([string]$compose.config_path)
  $expectedProjectComposePath = [IO.Path]::GetFullPath((Join-Path $projectRoot 'docker-compose.yml'))
  Assert-Check 'compose_path_project_subdir_exact' (
    $composePath -eq $expectedProjectComposePath
  ) $composePath
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
  $schemaOutput = @(& $pythonPath -I -S -B -c $schemaProbe $schemaSource 2>&1)
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
  Assert-Check 'kubernetes_failed_pod_count_exact' (@($kubernetes.allowed_historical_failed_pods).Count -eq 14) @($kubernetes.allowed_historical_failed_pods).Count
  $failedIdentities = [Collections.Generic.List[string]]::new()
  $podStatusReasonCount = 0
  $ownerJobReasonCount = 0
  foreach ($pod in @($kubernetes.allowed_historical_failed_pods)) {
    Assert-ExactObjectKeys 'kubernetes_failed_pod_keys_exact' $pod @(
      'uid','name','namespace','reason','reason_source','owner_uid',
      'owner_kind','owner_name','owner_controller'
    )
    Assert-Check 'kubernetes_failed_pod_uid_format' (
      [string]$pod.uid -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' -and
      [string]$pod.owner_uid -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ) $pod
    $b0Terminal = (
      [string]$pod.namespace -ceq 'evm-production' -and
      [string]$pod.name -clike 'evm-b0-production-*' -and
      [string]$pod.reason -ceq 'UnexpectedAdmissionError' -and
      [string]$pod.reason_source -ceq 'pod.status.reason' -and
      [string]$pod.owner_kind -ceq 'ReplicaSet' -and $pod.owner_controller -eq $true
    )
    $trainingTerminal = (
      [string]$pod.namespace -ceq 'evm-training' -and
      [string]$pod.name -clike 'evm-lifecycle-train-*' -and
      [string]$pod.reason -ceq 'BackoffLimitExceeded' -and
      [string]$pod.reason_source -ceq 'owner_job.status.conditions[type=Failed].reason' -and
      [string]$pod.owner_kind -ceq 'Job' -and $pod.owner_controller -eq $true
    )
    Assert-Check 'kubernetes_failed_pod_identity_exact' ($b0Terminal -or $trainingTerminal) $pod
    if ($b0Terminal) { $podStatusReasonCount++ }
    if ($trainingTerminal) { $ownerJobReasonCount++ }
    [void]$failedIdentities.Add("$($pod.namespace)|$($pod.name)|$($pod.uid)")
  }
  Assert-Check 'kubernetes_failed_pod_allowlist_unique' (
    @($failedIdentities | Select-Object -Unique).Count -eq $failedIdentities.Count
  ) $failedIdentities
  Assert-Check 'kubernetes_failed_pod_taxonomy_exact' (
    $podStatusReasonCount -eq 11 -and $ownerJobReasonCount -eq 3
  ) "pod_status=$podStatusReasonCount;owner_job=$ownerJobReasonCount"

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
  Assert-Check 'parent_checkpoint_count_exact' ($parents.Count -eq 10) $parents.Count
  Assert-Check 'parent_role_order_exact' (
    [string]::Join('|', @($parents | ForEach-Object { [string]$_.role })) -ceq [string]::Join('|', $requiredParentRoles)
  ) @($parents | ForEach-Object { $_.role })
  $parentPaths = [Collections.Generic.List[string]]::new()
  $parentByRole = [ordered]@{}
  foreach ($parent in $parents) {
    Assert-ExactObjectKeys "parent_keys_$($parent.role)" $parent @(
      'role','path','sha256','kind','schema','run_id','immutable','must_not_execute'
    )
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
  Assert-Check 'parent_paths_distinct' (@($parentPaths | Select-Object -Unique).Count -eq 10) $parentPaths
  Assert-Check 'external_terminal_fencing_parent_paths_distinct' (
    @(Compare-Object -ReferenceObject @($parentPaths) -DifferenceObject @($externalPaths) -IncludeEqual |
      Where-Object SideIndicator -eq '==').Count -eq 0
  ) $externalPaths
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
  $linkOutput = @(& $pythonPath -I -S -B -c $linkProbe $manifestPath $readbackParent.path $indexParent.path $readbackParent.sha256 2>&1)
  Assert-Check 'post_manual_runtime_state_and_index_link' ($LASTEXITCODE -eq 0) ($linkOutput -join [Environment]::NewLine)

  $coreValidationProbe = @'
import inspect,json,pathlib,sys
manifest=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
sys.path.insert(0,sys.argv[6])
from evm.scale_validation.phase_b2_r7s1 import validate_r7s1_manifest
kwargs={
  'expected_revision':sys.argv[3],
  'repository_root':pathlib.Path(sys.argv[2]),
  'expected_untracked_path_set_sha256':sys.argv[4],
  'expected_trusted_checkpoint_sha256':sys.argv[5],
}
if 'verify_attestations' in inspect.signature(validate_r7s1_manifest).parameters:
  kwargs['verify_attestations']=True
try:
  validate_r7s1_manifest(manifest,**kwargs)
except Exception as exc:
  print(f'{type(exc).__name__}:{exc}')
  raise SystemExit(3) from None
print('PASS')
'@
  $previousPythonPath = $env:PYTHONPATH
  $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
  try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $coreValidationOutput = @(
      & $pythonPath -I -S -B -c $coreValidationProbe $manifestPath $repo $revision $untrackedDigest $ExpectedTrustedCheckpointSha256 (Join-Path $projectRoot 'src') 2>&1
    )
    Assert-Check 'core_validate_r7s1_manifest_integration' (
      $LASTEXITCODE -eq 0 -and ($coreValidationOutput -join '').Trim() -ceq 'PASS'
    ) ($coreValidationOutput -join [Environment]::NewLine)
  }
  finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
  }

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
    $outerText.IndexOf('r7s1-outer-invocation-reservation.json', [StringComparison]::Ordinal) -ge 0 -and
    $outerText.IndexOf('r7s1-outer-invocation-reservation.json', [StringComparison]::Ordinal) -lt $outerInvokeIndex
  ) 'outer reservation before bridge invocation'
  $bridgeReservationIndex = $bridgeText.IndexOf('Write-CreateNewJson $bridgeReservation', [StringComparison]::Ordinal)
  Assert-Check 'bridge_no_uncontained_validator_or_probe_child' (
    -not $bridgeText.Contains('& $ValidatorPath') -and
    -not $bridgeText.Contains('whoami.exe') -and
    -not $bridgeText.Contains('Invoke-GitRead') -and
    -not $bridgeText.Contains('$untrackedProbe') -and
    $bridgeReservationIndex -ge 0 -and $bridgeReservationIndex -lt $runnerInvokeIndex
  ) 'bridge reservation -> sole runner child'
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
    python_distribution_final_remeasurement = '$pythonDistributionFinal = Get-DistributionTreeIdentity $PinnedPythonDistributionRoot ''python'''
    git_distribution_final_remeasurement = '$gitDistributionFinal = Get-DistributionTreeIdentity $PinnedGitDistributionRoot ''git'''
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
  Assert-Check 'bridge_final_distribution_remeasurement_binds_serialized_observation' (
    $bridgeText.Contains('[string]$pythonObservation.distribution_tree_sha256 -cne $pythonDistributionFinal.sha256') -and
    $bridgeText.Contains('[int]$pythonObservation.file_count -ne $pythonDistributionFinal.file_count') -and
    $bridgeText.Contains('[string]$gitObservation.distribution_tree_sha256 -cne $gitDistributionFinal.sha256') -and
    $bridgeText.Contains('[int]$gitObservation.file_count -ne $gitDistributionFinal.file_count')
  ) 'final tree/count equals serialized launcher observation'
  $bridgePathFenceIndex = $bridgeText.IndexOf('# R7S1_PATH_FENCE_BRIDGE_FINAL',[StringComparison]::Ordinal)
  $bridgePrewritePathFenceIndex = $bridgeText.IndexOf('# R7S1_PATH_FENCE_BRIDGE_PREWRITE',[StringComparison]::Ordinal)
  $bridgeReservationPathIndex = $bridgeText.IndexOf('$bridgeReservation = Join-Path $PSScriptRoot',[StringComparison]::Ordinal)
  $bridgeReservationWriteIndex = $bridgeText.IndexOf('Write-CreateNewJson $bridgeReservation',[StringComparison]::Ordinal)
  $bridgeFinalPythonIndex = $bridgeText.IndexOf('$pythonDistributionFinal = Get-DistributionTreeIdentity',[StringComparison]::Ordinal)
  $bridgeFinalOuterHashIndex = $bridgeText.IndexOf('outer_sha256_mismatch_immediate_before_runner',[StringComparison]::Ordinal)
  Assert-Check 'bridge_bound_path_fence_at_invocation_boundary' (
    $bridgePathFenceIndex -gt $bridgeFinalPythonIndex -and
    $bridgePathFenceIndex -lt $bridgeFinalOuterHashIndex -and
    $bridgePathFenceIndex -lt $runnerInvokeIndex -and
    $bridgeText.Contains("Assert-BoundRunLocation `$PSScriptRoot `$PinnedStagingPath") -and
    $bridgeText.Contains("Assert-BoundRunLocation `$OutputDirectory `$PinnedOutputPath") -and
    $bridgeText.Contains("Assert-BoundRunLocation `$PinnedEmergencySealPath `$PinnedEmergencySealPath")
  ) $bridgePathFenceIndex
  Assert-Check 'bridge_bound_path_fence_before_any_reservation_write' (
    $bridgePrewritePathFenceIndex -ge 0 -and
    $bridgePrewritePathFenceIndex -lt $bridgeReservationPathIndex -and
    $bridgePrewritePathFenceIndex -lt $bridgeReservationWriteIndex -and
    ($bridgeText.Split(@('Assert-BoundRunLocation $'),[StringSplitOptions]::None).Count - 1) -eq 6
  ) $bridgePrewritePathFenceIndex

  $combinedSource = $outerText + "`n" + $bridgeText + "`n" + $manifestText + "`n" + $runtimeSource
  $forbiddenPatterns = [ordered]@{
    terminate_job_object = '\bTerminateJobObject\b'
    kill_on_job_close = '\bJOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE\b'
    terminate_process = '\bTerminateProcess\b'
    taskkill = '(?im)\btaskkill(?:\.exe)?\b'
    stop_process_force = '(?im)\bstop-process\b[^\r\n]*\b-force\b'
    python_kill = '(?im)(?:\.kill|\.terminate|os\.kill)\s*\('
    docker_reset_prune_down_up = '(?im)\bdocker(?:\.exe)?\s+(?:(?:compose\s+)?(?:down|up)\b|(?:system\s+)?prune\b|reset\b)'
    docker_compose_standalone_destructive = '(?im)\bdocker-compose(?:\.exe)?\s+(?:down|up|rm|kill|restart)\b'
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
    schema_version = 'evm.s8_v4.x1_phase_b2_r7s1_bundle_validation.v1'
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
    schema_version = 'evm.s8_v4.x1_phase_b2_r7s1_bundle_validation.v1'
    status = 'FAIL'
    validated_at = [DateTime]::UtcNow.ToString('o')
    error = $_.Exception.Message
    passed_check_count = $checks.Count
    checks = $checks
  } | ConvertTo-Json -Depth 14 -Compress
  exit 2
}
