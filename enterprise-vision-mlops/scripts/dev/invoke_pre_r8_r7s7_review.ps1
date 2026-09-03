[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExpectedOuterSha256,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$PythonSha256,
    [Parameter(Mandatory = $true)][string]$PowerShellSha256,
    [Parameter(Mandatory = $true)][string]$PublisherPath,
    [Parameter(Mandatory = $true)][string]$PublisherSha256,
    [Parameter(Mandatory = $true)][string]$RunnerPath,
    [Parameter(Mandatory = $true)][string]$RunnerSha256,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$ExternalWorkOrder,
    [Parameter(Mandatory = $true)][string]$ExternalWorkOrderSha256,
    [string[]]$PublisherArguments = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($null -eq ('Evm.PreR8R7S7Native' -as [type])) {
    Add-Type -Namespace Evm -Name PreR8R7S7Native -MemberDefinition @'
[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
public struct ByHandleFileInformation {
    public uint FileAttributes;
    public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
    public uint VolumeSerialNumber;
    public uint FileSizeHigh;
    public uint FileSizeLow;
    public uint NumberOfLinks;
    public uint FileIndexHigh;
    public uint FileIndexLow;
}
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet=System.Runtime.InteropServices.CharSet.Unicode, SetLastError=true)]
public static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFile(
    string name, uint access, uint share, System.IntPtr security,
    uint disposition, uint flags, System.IntPtr template);
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)]
[return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
public static extern bool GetFileInformationByHandle(
    Microsoft.Win32.SafeHandles.SafeFileHandle handle,
    out ByHandleFileInformation information);
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet=System.Runtime.InteropServices.CharSet.Unicode, SetLastError=true)]
public static extern uint GetFinalPathNameByHandle(
    Microsoft.Win32.SafeHandles.SafeFileHandle handle,
    System.Text.StringBuilder path, uint length, uint flags);
'@
}

function Get-RetainedHandleIdentity {
    param([Parameter(Mandatory = $true)]$SafeHandle)
    $Information = New-Object Evm.PreR8R7S7Native+ByHandleFileInformation
    if (-not [Evm.PreR8R7S7Native]::GetFileInformationByHandle($SafeHandle, [ref]$Information)) {
        throw ('retained_handle_identity_failed_win32_' + [Runtime.InteropServices.Marshal]::GetLastWin32Error())
    }
    $PathBuffer = [Text.StringBuilder]::new(32768)
    $PathLength = [Evm.PreR8R7S7Native]::GetFinalPathNameByHandle($SafeHandle, $PathBuffer, $PathBuffer.Capacity, 0)
    if (($PathLength -eq 0) -or ($PathLength -ge $PathBuffer.Capacity)) {
        throw ('retained_handle_final_path_failed_win32_' + [Runtime.InteropServices.Marshal]::GetLastWin32Error())
    }
    $CreationHigh = ([Int64]$Information.CreationTime.dwHighDateTime) -band 0xffffffffL
    $CreationLow = ([Int64]$Information.CreationTime.dwLowDateTime) -band 0xffffffffL
    $FileIndexHigh = ([Int64]$Information.FileIndexHigh) -band 0xffffffffL
    $FileIndexLow = ([Int64]$Information.FileIndexLow) -band 0xffffffffL
    $CreationTime = ($CreationHigh -shl 32) -bor $CreationLow
    $FileId = ([UInt64]$FileIndexHigh -shl 32) -bor [UInt64]$FileIndexLow
    return [pscustomobject]@{
        FinalPath = $PathBuffer.ToString()
        VolumeSerialNumber = ([Int64]$Information.VolumeSerialNumber) -band 0xffffffffL
        FileId = [UInt64]$FileId
        CreationTime = [Int64]$CreationTime
    }
}

function Assert-RetainedIdentityUnchanged {
    param(
        [Parameter(Mandatory = $true)]$SafeHandle,
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $Observed = Get-RetainedHandleIdentity -SafeHandle $SafeHandle
    if (
        $Observed.FinalPath -cne $Expected.FinalPath -or
        $Observed.VolumeSerialNumber -ne $Expected.VolumeSerialNumber -or
        $Observed.FileId -ne $Expected.FileId -or
        $Observed.CreationTime -ne $Expected.CreationTime
    ) {
        throw "${Label}_retained_identity_changed"
    }
}

function Assert-RetainedPinnedFileUnchanged {
    param(
        [Parameter(Mandatory = $true)]$Pinned,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-RetainedIdentityUnchanged -SafeHandle $Pinned.Stream.SafeFileHandle -Expected $Pinned.Identity -Label $Label
    $Pinned.Stream.Position = 0
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $HashBytes = $Hasher.ComputeHash($Pinned.Stream)
    }
    finally {
        $Hasher.Dispose()
    }
    $ObservedSha256 = [BitConverter]::ToString($HashBytes).Replace('-', '').ToLowerInvariant()
    $Pinned.Stream.Position = 0
    if ($ObservedSha256 -cne $Pinned.Sha256) {
        throw "${Label}_retained_sha256_changed"
    }
}

function Add-RetainedDirectoryChain {
    param(
        [Parameter(Mandatory = $true)][string]$LeafPath,
        [Parameter(Mandatory = $true)][string]$BoundaryPath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][hashtable]$Seen,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][Collections.ArrayList]$Locks,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $Boundary = [IO.Path]::GetFullPath($BoundaryPath).TrimEnd('\')
    $Current = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($LeafPath))
    while ($true) {
        if (-not $Current.StartsWith($Boundary + '\', [StringComparison]::OrdinalIgnoreCase) -and $Current -cne $Boundary) {
            throw "${Label}_directory_chain_outside_boundary"
        }
        $Key = $Current.ToLowerInvariant()
        if (-not $Seen.ContainsKey($Key)) {
            $DirectoryItem = Get-Item -LiteralPath $Current -Force
            if ((-not $DirectoryItem.PSIsContainer) -or (($DirectoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
                throw "${Label}_directory_chain_reparse_forbidden"
            }
            $Handle = [Evm.PreR8R7S7Native]::CreateFile(
                $DirectoryItem.FullName,
                0x80,
                1,
                [IntPtr]::Zero,
                3,
                0x02000000,
                [IntPtr]::Zero
            )
            if ($Handle.IsInvalid) {
                throw ("${Label}_directory_handle_failed_win32_" + [Runtime.InteropServices.Marshal]::GetLastWin32Error())
            }
            $Identity = Get-RetainedHandleIdentity -SafeHandle $Handle
            [void]$Locks.Add([pscustomobject]@{
                Path = $DirectoryItem.FullName
                SafeHandle = $Handle
                Identity = $Identity
                ShareMode = 'read_only_no_write_no_delete_share'
            })
            $Seen[$Key] = $true
        }
        if ($Current -ceq $Boundary) {
            break
        }
        $Current = [IO.Path]::GetDirectoryName($Current)
    }
}

function Resolve-RegularPinnedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "${Label}_sha256_invalid"
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer -and (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0)) {
        $Resolved = $Item.FullName
    } else {
        throw "${Label}_regular_non_reparse_file_required"
    }
    # FileShare.Read deliberately withholds write and delete sharing.  The
    # returned handle stays open through the child lifetime, closing the
    # hash-to-launch replacement window for every executable/code input.
    $Stream = [IO.FileStream]::new(
        $Resolved,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $HashBytes = $Hasher.ComputeHash($Stream)
    }
    finally {
        $Hasher.Dispose()
    }
    $ObservedSha256 = [BitConverter]::ToString($HashBytes).Replace('-', '').ToLowerInvariant()
    if ($ObservedSha256 -cne $ExpectedSha256) {
        $Stream.Dispose()
        throw "${Label}_sha256_mismatch"
    }
    $Stream.Position = 0
    return [pscustomobject]@{
        Path = $Resolved
        Sha256 = $ObservedSha256
        Stream = $Stream
        Identity = (Get-RetainedHandleIdentity -SafeHandle $Stream.SafeFileHandle)
        ShareMode = 'read_only_no_write_no_delete_share'
    }
}

function Assert-NonReparseDirectoryChain {
    param(
        [Parameter(Mandatory = $true)][string]$LeafPath,
        [Parameter(Mandatory = $true)][string]$BoundaryPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $Boundary = [IO.Path]::GetFullPath($BoundaryPath).TrimEnd('\')
    $Current = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($LeafPath))
    while ($true) {
        if (-not $Current.StartsWith($Boundary + '\', [StringComparison]::OrdinalIgnoreCase) -and $Current -cne $Boundary) {
            throw "${Label}_directory_chain_outside_boundary"
        }
        $DirectoryItem = Get-Item -LiteralPath $Current -Force
        if ((-not $DirectoryItem.PSIsContainer) -or (($DirectoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "${Label}_directory_chain_reparse_forbidden"
        }
        if ($Current -cne $Boundary) {
            $Current = [IO.Path]::GetDirectoryName($Current)
        } else {
            break
        }
    }
}

$OuterLock = Resolve-RegularPinnedFile -Path $PSCommandPath -ExpectedSha256 $ExpectedOuterSha256 -Label 'outer'
$PythonLock = Resolve-RegularPinnedFile -Path $PythonPath -ExpectedSha256 $PythonSha256 -Label 'python'
$PublisherLock = Resolve-RegularPinnedFile -Path $PublisherPath -ExpectedSha256 $PublisherSha256 -Label 'publisher'
$RunnerLock = Resolve-RegularPinnedFile -Path $RunnerPath -ExpectedSha256 $RunnerSha256 -Label 'runner'
$WorkOrderLock = Resolve-RegularPinnedFile -Path $ExternalWorkOrder -ExpectedSha256 $ExternalWorkOrderSha256 -Label 'external_work_order'
$PowerShellLock = Resolve-RegularPinnedFile -Path ([Diagnostics.Process]::GetCurrentProcess().MainModule.FileName) -ExpectedSha256 $PowerShellSha256 -Label 'powershell'
$Outer = $OuterLock.Path
$Python = $PythonLock.Path
$Publisher = $PublisherLock.Path
$Runner = $RunnerLock.Path
$WorkOrder = $WorkOrderLock.Path
$PowerShell = $PowerShellLock.Path
$WorkOrderLock.Stream.Position = 0
$WorkOrderReader = [IO.StreamReader]::new(
    $WorkOrderLock.Stream,
    [Text.UTF8Encoding]::new($false, $true),
    $true,
    4096,
    $true
)
try {
    $WorkOrderPayload = $WorkOrderReader.ReadToEnd() | ConvertFrom-Json
}
finally {
    $WorkOrderReader.Dispose()
    $WorkOrderLock.Stream.Position = 0
}
if (($WorkOrderPayload.authority_scope -cne 'internal_non_authoritative') -or ($WorkOrderPayload.authority_verified -ne $false)) {
    throw 'external_work_order_authority_scope_invalid'
}
if (
    ($WorkOrderPayload.immutable_checkout_namespace_authority -ne $false) -or
    ($WorkOrderPayload.runtime_stdlib_native_closure_verified -ne $false)
) {
    throw 'internal_unproven_runtime_closure_contract_invalid'
}
$ValidationRunUuid = ([Guid]([string]$WorkOrderPayload.validation_run_uuid)).ToString()
if ($ValidationRunUuid -cne [string]$WorkOrderPayload.validation_run_uuid) {
    throw 'external_work_order_validation_run_uuid_not_canonical'
}
$InputBatchDirectory = [IO.Path]::GetDirectoryName($WorkOrder)
$InputParentDirectory = [IO.Path]::GetDirectoryName($InputBatchDirectory)
$ExpectedPycachePrefix = [IO.Path]::GetFullPath(
    [IO.Path]::Combine($InputParentDirectory, ".pre-r8-r7s7-pycache-${ValidationRunUuid}")
)
$PycachePrefix = [IO.Path]::GetFullPath([string]$WorkOrderPayload.pycache_prefix)
if ($PycachePrefix -cne $ExpectedPycachePrefix) {
    throw 'external_work_order_pycache_prefix_not_run_unique'
}
if (Test-Path -LiteralPath $PycachePrefix) {
    throw 'external_work_order_pycache_prefix_must_not_exist'
}
$MatchingPythonBindings = @(
    $WorkOrderPayload.tool_file_bindings.PSObject.Properties |
        Where-Object {
            $_.Name -like 'python_*' -and
            ([IO.Path]::GetFullPath([string]$_.Value.path) -ieq $Python)
        }
)
if ($MatchingPythonBindings.Count -lt 1) {
    throw 'python_work_order_binding_missing'
}
$SitePaths = @($MatchingPythonBindings | ForEach-Object { [IO.Path]::GetFullPath([string]$_.Value.site_packages.path) } | Sort-Object -Unique)
if ($SitePaths.Count -ne 1) {
    throw 'python_site_packages_work_order_binding_ambiguous'
}
$PythonDirectory = [IO.Path]::GetDirectoryName($Python)
if ([IO.Path]::GetFileName($PythonDirectory.TrimEnd('\')) -ieq 'Scripts') {
    $PythonEnvironmentRoot = [IO.Path]::GetDirectoryName($PythonDirectory)
} else {
    $PythonEnvironmentRoot = $PythonDirectory
}
$DerivedSitePackages = [IO.Path]::GetFullPath(
    [IO.Path]::Combine($PythonEnvironmentRoot, 'Lib', 'site-packages')
)
if ($SitePaths[0] -cne $DerivedSitePackages) {
    throw 'python_site_packages_not_derived_from_pinned_python'
}
$SiteItem = Get-Item -LiteralPath $DerivedSitePackages -Force
if ((-not $SiteItem.PSIsContainer) -or (($SiteItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw 'python_site_packages_regular_non_reparse_directory_required'
}
$SitePackages = $SiteItem.FullName
$SiteIdentityScript = @'
import json, os, pathlib, stat, sys
path = pathlib.Path(sys.argv[1]).resolve(strict=True)
value = os.lstat(path)
if not stat.S_ISDIR(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
    raise SystemExit('site_packages_regular_non_reparse_directory_required')
print(json.dumps({'path': str(path), 'device': value.st_dev, 'file_id': value.st_ino, 'creation_time_ns': value.st_ctime_ns}, separators=(',', ':'), sort_keys=True))
'@
if ($SiteIdentityScript -match '[^\x00-\x7f]') {
    throw 'python_site_packages_identity_source_must_be_ascii'
}
$SavedErrorActionPreference = $ErrorActionPreference
$SavedOutputEncoding = $OutputEncoding
$ErrorActionPreference = 'Continue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $LASTEXITCODE = $null
    $SiteIdentityRaw = @($SiteIdentityScript | & $Python -I -B -S -X "pycache_prefix=$PycachePrefix" - $SitePackages 2>&1)
    $SiteIdentityExitCode = $LASTEXITCODE
}
finally {
    $OutputEncoding = $SavedOutputEncoding
    $ErrorActionPreference = $SavedErrorActionPreference
}
if ($null -eq $SiteIdentityExitCode) {
    throw 'python_site_packages_identity_process_not_started'
}
if ($SiteIdentityExitCode -ne 0 -or $SiteIdentityRaw.Count -ne 1) {
    throw 'python_site_packages_identity_probe_failed'
}
$SiteIdentity = $SiteIdentityRaw[0] | ConvertFrom-Json
foreach ($Binding in $MatchingPythonBindings) {
    $ExpectedSite = $Binding.Value.site_packages
    if (
        ([IO.Path]::GetFullPath([string]$ExpectedSite.path) -cne [string]$SiteIdentity.path) -or
        ([Int64]$ExpectedSite.device -ne [Int64]$SiteIdentity.device) -or
        ([Int64]$ExpectedSite.file_id -ne [Int64]$SiteIdentity.file_id) -or
        ([Int64]$ExpectedSite.creation_time_ns -ne [Int64]$SiteIdentity.creation_time_ns) -or
        ([string]$ExpectedSite.pth_processing -cne 'disabled_by_python_no_site')
    ) {
        throw 'python_site_packages_work_order_identity_mismatch'
    }
}
$RootItem = Get-Item -LiteralPath $ProjectRoot -Force
if ((-not $RootItem.PSIsContainer) -or (($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw 'project_root_regular_non_reparse_directory_required'
}
$Root = $RootItem.FullName
$DirectoryLocks = [Collections.ArrayList]::new()
$DirectoryLockPaths = @{}
Add-RetainedDirectoryChain -LeafPath $Outer -BoundaryPath $Root -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'project_code'
Add-RetainedDirectoryChain -LeafPath $Publisher -BoundaryPath $Root -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'project_code'
Add-RetainedDirectoryChain -LeafPath $Runner -BoundaryPath $Root -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'project_code'
Add-RetainedDirectoryChain -LeafPath $Python -BoundaryPath $PythonEnvironmentRoot -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'python_tool'
Add-RetainedDirectoryChain -LeafPath $PowerShell -BoundaryPath ([IO.Path]::GetDirectoryName($PowerShell)) -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'powershell_tool'
Add-RetainedDirectoryChain -LeafPath $WorkOrder -BoundaryPath $InputParentDirectory -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'work_order'
$ExpectedCodeBindingNames = @(
    'evm_init',
    'phase_b2_r7s3_process',
    'phase_b2_r7s3_handle_io',
    'phase_b2_r7s4_authority',
    'phase_b2_r7s4_evidence',
    'phase_b2_r7s4_handle_io',
    'phase_b2_r7s5_admission',
    'phase_b2_r7s5_ci',
    'phase_b2_r7s5_dual_clock',
    'phase_b2_r7s5_etw',
    'phase_b2_r7s5_gate',
    'phase_b2_r7s5_reservation',
    'phase_b2_r7s5_windows_wsl',
    'phase_b2_r7s5_evidence',
    'phase_b2_r7s6_evidence',
    'publisher',
    'scale_validation_init',
    'trusted_outer',
    'validation_runner'
)
$ExpectedCodeRelativePaths = @{
    evm_init = 'src\evm\__init__.py'
    scale_validation_init = 'src\evm\scale_validation\__init__.py'
    phase_b2_r7s3_handle_io = 'src\evm\scale_validation\phase_b2_r7s3_handle_io.py'
    phase_b2_r7s3_process = 'src\evm\scale_validation\phase_b2_r7s3_process.py'
    phase_b2_r7s4_authority = 'src\evm\scale_validation\phase_b2_r7s4_authority.py'
    phase_b2_r7s4_evidence = 'src\evm\scale_validation\phase_b2_r7s4_evidence.py'
    phase_b2_r7s4_handle_io = 'src\evm\scale_validation\phase_b2_r7s4_handle_io.py'
    phase_b2_r7s5_admission = 'src\evm\scale_validation\phase_b2_r7s5_admission.py'
    phase_b2_r7s5_ci = 'src\evm\scale_validation\phase_b2_r7s5_ci.py'
    phase_b2_r7s5_dual_clock = 'src\evm\scale_validation\phase_b2_r7s5_dual_clock.py'
    phase_b2_r7s5_etw = 'src\evm\scale_validation\phase_b2_r7s5_etw.py'
    phase_b2_r7s5_evidence = 'src\evm\scale_validation\phase_b2_r7s5_evidence.py'
    phase_b2_r7s5_gate = 'src\evm\scale_validation\phase_b2_r7s5_gate.py'
    phase_b2_r7s5_reservation = 'src\evm\scale_validation\phase_b2_r7s5_reservation.py'
    phase_b2_r7s5_windows_wsl = 'src\evm\scale_validation\phase_b2_r7s5_windows_wsl.py'
    phase_b2_r7s6_evidence = 'src\evm\scale_validation\phase_b2_r7s6_evidence.py'
    publisher = 'scripts\dev\publish_pre_r8_r7s5_review.py'
    validation_runner = 'scripts\dev\run_pre_r8_r7s5_validation.py'
    trusted_outer = 'scripts\dev\invoke_pre_r8_r7s7_review.ps1'
}
$ObservedCodeBindingNames = @($WorkOrderPayload.code_file_bindings.PSObject.Properties.Name | Sort-Object)
if (
    $ObservedCodeBindingNames.Count -ne $ExpectedCodeBindingNames.Count -or
    (Compare-Object -ReferenceObject $ExpectedCodeBindingNames -DifferenceObject $ObservedCodeBindingNames)
) {
    throw 'external_work_order_code_file_binding_set_not_exact'
}
$CodeLocks = @()
$CodePaths = @{}
$RootPrefix = $Root.TrimEnd('\') + '\'
foreach ($Property in $WorkOrderPayload.code_file_bindings.PSObject.Properties) {
    $BindingPath = [IO.Path]::GetFullPath([string]$Property.Value.path)
    $ExpectedBindingPath = [IO.Path]::GetFullPath([IO.Path]::Combine($Root, [string]$ExpectedCodeRelativePaths[$Property.Name]))
    if ($BindingPath -cne $ExpectedBindingPath) {
        throw 'external_work_order_code_file_role_path_mismatch'
    }
    if (-not $BindingPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'external_work_order_code_file_outside_project_root'
    }
    $NormalizedPath = $BindingPath.ToLowerInvariant()
    if ($CodePaths.ContainsKey($NormalizedPath)) {
        throw 'external_work_order_code_file_duplicate_path'
    }
    $CodePaths[$NormalizedPath] = $true
    $Lock = Resolve-RegularPinnedFile -Path $BindingPath -ExpectedSha256 ([string]$Property.Value.sha256) -Label ("code_file_" + $Property.Name)
    if ([Int64](Get-Item -LiteralPath $Lock.Path -Force).Length -ne [Int64]$Property.Value.bytes) {
        $Lock.Stream.Dispose()
        throw 'external_work_order_code_file_size_mismatch'
    }
    Add-RetainedDirectoryChain -LeafPath $Lock.Path -BoundaryPath $Root -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'project_code'
    $CodeLocks += $Lock
}
if (
    ([IO.Path]::GetFullPath([string]$WorkOrderPayload.code_file_bindings.publisher.path) -cne $Publisher) -or
    ([IO.Path]::GetFullPath([string]$WorkOrderPayload.code_file_bindings.validation_runner.path) -cne $Runner) -or
    ([IO.Path]::GetFullPath([string]$WorkOrderPayload.code_file_bindings.trusted_outer.path) -cne $Outer)
) {
    throw 'external_work_order_primary_code_path_mismatch'
}
$ToolContentLocks = @()
$ToolExecutableLocks = @()
foreach ($ToolProperty in @($WorkOrderPayload.tool_file_bindings.PSObject.Properties | Where-Object { $_.Name -like 'python_*' })) {
    $ToolBinding = $ToolProperty.Value
    $ToolExecutableLock = Resolve-RegularPinnedFile -Path ([string]$ToolBinding.path) -ExpectedSha256 ([string]$ToolBinding.sha256) -Label ("tool_executable_" + $ToolProperty.Name)
    $ToolExecutableLocks += $ToolExecutableLock
    $ToolPythonDirectory = [IO.Path]::GetDirectoryName($ToolExecutableLock.Path)
    if ([IO.Path]::GetFileName($ToolPythonDirectory.TrimEnd('\')) -ieq 'Scripts') {
        $ToolEnvironmentRoot = [IO.Path]::GetDirectoryName($ToolPythonDirectory)
    } else {
        $ToolEnvironmentRoot = $ToolPythonDirectory
    }
    Add-RetainedDirectoryChain -LeafPath $ToolExecutableLock.Path -BoundaryPath $ToolEnvironmentRoot -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'python_tool'
    $ToolSitePackages = [IO.Path]::GetFullPath([IO.Path]::Combine($ToolEnvironmentRoot, 'Lib', 'site-packages'))
    if (
        [IO.Path]::GetFullPath([string]$ToolBinding.site_packages.path) -cne $ToolSitePackages -or
        [IO.Path]::GetFullPath([string]$ToolBinding.python_tool_module.site_packages_path) -cne $ToolSitePackages
    ) {
        throw 'python_tool_content_site_packages_mismatch'
    }
    $ToolSiteItem = Get-Item -LiteralPath $ToolSitePackages -Force
    if ((-not $ToolSiteItem.PSIsContainer) -or (($ToolSiteItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw 'python_tool_content_site_packages_reparse_forbidden'
    }
    $Records = @($ToolBinding.python_tool_module.content_files)
    if ($Records.Count -ne [Int64]$ToolBinding.python_tool_module.content_file_count -or $Records.Count -lt 1) {
        throw 'python_tool_content_file_count_mismatch'
    }
    $Distribution = [string]$ToolBinding.python_tool_module.distribution
    if ($Distribution -ceq 'pytest') {
        $ExpectedDependencies = @('colorama', 'iniconfig', 'packaging', 'pluggy', 'pytest')
    } elseif ($Distribution -ceq 'ruff') {
        $ExpectedDependencies = @('ruff')
    } else {
        throw 'python_tool_distribution_not_allowed'
    }
    $Dependencies = @($ToolBinding.python_tool_module.dependency_distributions)
    $ObservedDependencyNames = @($Dependencies | ForEach-Object { [string]$_.name } | Sort-Object)
    if (
        $ObservedDependencyNames.Count -ne $ExpectedDependencies.Count -or
        (Compare-Object -ReferenceObject $ExpectedDependencies -DifferenceObject $ObservedDependencyNames)
    ) {
        throw 'python_tool_dependency_closure_not_exact'
    }
    $SeenRelativePaths = @{}
    $ContentLocksByRelativePath = @{}
    $CanonicalRecords = @()
    [Int64]$ObservedTotalBytes = 0
    $PreviousRelativePath = $null
    foreach ($Record in $Records) {
        $RecordKeys = @($Record.PSObject.Properties.Name | Sort-Object)
        if ($RecordKeys.Count -ne 3 -or (Compare-Object -ReferenceObject @('bytes', 'path', 'sha256') -DifferenceObject $RecordKeys)) {
            throw 'python_tool_content_record_keys_not_exact'
        }
        $RelativePath = [string]$Record.path
        if (
            [IO.Path]::IsPathRooted($RelativePath) -or
            $RelativePath -notmatch '^[A-Za-z0-9._/-]+$' -or
            $RelativePath.Contains('\') -or
            @($RelativePath.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0
        ) {
            throw 'python_tool_content_relative_path_invalid'
        }
        if (($null -ne $PreviousRelativePath) -and ([StringComparer]::Ordinal.Compare($PreviousRelativePath, $RelativePath) -ge 0)) {
            throw 'python_tool_content_records_not_strictly_sorted'
        }
        $PreviousRelativePath = $RelativePath
        $NormalizedRelativePath = $RelativePath.ToLowerInvariant()
        if ($SeenRelativePaths.ContainsKey($NormalizedRelativePath)) {
            throw 'python_tool_content_duplicate_path'
        }
        $SeenRelativePaths[$NormalizedRelativePath] = $true
        $ContentPath = [IO.Path]::GetFullPath([IO.Path]::Combine($ToolSitePackages, $RelativePath.Replace('/', '\')))
        if (-not $ContentPath.StartsWith($ToolSitePackages.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'python_tool_content_path_escape'
        }
        Assert-NonReparseDirectoryChain -LeafPath $ContentPath -BoundaryPath $ToolSitePackages -Label 'python_tool_content'
        $ContentLock = Resolve-RegularPinnedFile -Path $ContentPath -ExpectedSha256 ([string]$Record.sha256) -Label 'python_tool_content'
        if ([Int64](Get-Item -LiteralPath $ContentLock.Path -Force).Length -ne [Int64]$Record.bytes) {
            $ContentLock.Stream.Dispose()
            throw 'python_tool_content_file_size_mismatch'
        }
        Add-RetainedDirectoryChain -LeafPath $ContentLock.Path -BoundaryPath $ToolSitePackages -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'python_tool_content'
        $ToolContentLocks += $ContentLock
        $ContentLocksByRelativePath[$RelativePath] = $ContentLock
        $ObservedTotalBytes += [Int64]$Record.bytes
        $CanonicalRecords += [ordered]@{
            bytes = [Int64]$Record.bytes
            path = $RelativePath
            sha256 = ([string]$Record.sha256).ToLowerInvariant()
        }
    }
    foreach ($Dependency in $Dependencies) {
        $DependencyKeys = @($Dependency.PSObject.Properties.Name | Sort-Object)
        if ($DependencyKeys.Count -ne 3 -or (Compare-Object -ReferenceObject @('dist_info_path', 'name', 'version') -DifferenceObject $DependencyKeys)) {
            throw 'python_tool_dependency_record_keys_not_exact'
        }
        $DependencyName = [string]$Dependency.name
        $DependencyPath = [IO.Path]::GetFullPath([string]$Dependency.dist_info_path)
        if (-not $DependencyPath.StartsWith($ToolSitePackages.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'python_tool_dependency_dist_info_outside_site_packages'
        }
        $DependencyItem = Get-Item -LiteralPath $DependencyPath -Force
        if ((-not $DependencyItem.PSIsContainer) -or (($DependencyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw 'python_tool_dependency_dist_info_regular_directory_required'
        }
        $MetadataPath = [IO.Path]::Combine($DependencyPath, 'METADATA')
        $MetadataRelativePath = $MetadataPath.Substring($ToolSitePackages.TrimEnd('\').Length + 1).Replace('\', '/')
        if (-not $ContentLocksByRelativePath.ContainsKey($MetadataRelativePath)) {
            throw 'python_tool_dependency_metadata_not_content_bound'
        }
        $MetadataText = [IO.File]::ReadAllText($MetadataPath, [Text.UTF8Encoding]::new($false, $true))
        $NameMatches = [Text.RegularExpressions.Regex]::Matches($MetadataText, '(?m)^Name: ([^\r\n]+)\r?$')
        $VersionMatches = [Text.RegularExpressions.Regex]::Matches($MetadataText, '(?m)^Version: ([^\r\n]+)\r?$')
        if ($NameMatches.Count -ne 1 -or $VersionMatches.Count -ne 1) {
            throw 'python_tool_dependency_metadata_invalid'
        }
        $MetadataName = $NameMatches[0].Groups[1].Value.Trim().ToLowerInvariant() -replace '[-_.]+', '-'
        if ($MetadataName -cne $DependencyName -or $VersionMatches[0].Groups[1].Value.Trim() -cne [string]$Dependency.version) {
            throw 'python_tool_dependency_metadata_binding_mismatch'
        }
        $ImportAnchor = if ($DependencyName -ceq 'pytest') { '_pytest/__init__.py' } else { $DependencyName + '/__init__.py' }
        if (-not $ContentLocksByRelativePath.ContainsKey($ImportAnchor)) {
            throw 'python_tool_dependency_import_anchor_not_content_bound'
        }
    }
    $CanonicalInventory = (ConvertTo-Json -InputObject @($CanonicalRecords) -Compress -Depth 4) + "`n"
    $InventoryHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $InventoryHashBytes = $InventoryHasher.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($CanonicalInventory))
    }
    finally {
        $InventoryHasher.Dispose()
    }
    $ObservedInventorySha256 = [BitConverter]::ToString($InventoryHashBytes).Replace('-', '').ToLowerInvariant()
    if (
        $ObservedTotalBytes -ne [Int64]$ToolBinding.python_tool_module.content_total_bytes -or
        $ObservedInventorySha256 -cne [string]$ToolBinding.python_tool_module.content_inventory_sha256
    ) {
        throw 'python_tool_content_aggregate_mismatch'
    }
    foreach ($OriginProperty in $ToolBinding.python_tool_module.module_origins.PSObject.Properties) {
        $Origin = $OriginProperty.Value
        $OriginRelativePath = [string]$Origin.relative_path
        $OriginMatch = @($Records | Where-Object { ([string]$_.path -ceq $OriginRelativePath) })
        if (
            $OriginMatch.Count -ne 1 -or
            [IO.Path]::GetFullPath([string]$Origin.path) -cne [IO.Path]::GetFullPath([IO.Path]::Combine($ToolSitePackages, $OriginRelativePath.Replace('/', '\'))) -or
            [string]$Origin.sha256 -cne [string]$OriginMatch[0].sha256 -or
            [Int64]$Origin.bytes -ne [Int64]$OriginMatch[0].bytes
        ) {
            throw 'python_tool_module_origin_content_binding_mismatch'
        }
    }
    if ($null -ne $ToolBinding.python_tool_module.launcher_binding) {
        $ExpectedLauncher = [IO.Path]::GetFullPath([IO.Path]::Combine($ToolEnvironmentRoot, 'Scripts', 'ruff.exe'))
        if ([IO.Path]::GetFullPath([string]$ToolBinding.python_tool_module.launcher_binding.path) -cne $ExpectedLauncher) {
            throw 'python_tool_launcher_path_mismatch'
        }
        $LauncherLock = Resolve-RegularPinnedFile -Path $ExpectedLauncher -ExpectedSha256 ([string]$ToolBinding.python_tool_module.launcher_binding.sha256) -Label 'python_tool_launcher'
        if ([Int64](Get-Item -LiteralPath $LauncherLock.Path -Force).Length -ne [Int64]$ToolBinding.python_tool_module.launcher_binding.bytes) {
            $LauncherLock.Stream.Dispose()
            throw 'python_tool_launcher_size_mismatch'
        }
        Add-RetainedDirectoryChain -LeafPath $LauncherLock.Path -BoundaryPath $ToolEnvironmentRoot -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'python_tool_launcher'
        $ToolContentLocks += $LauncherLock
    }
}
$GitBinding = $WorkOrderPayload.tool_file_bindings.git
if ($null -eq $GitBinding) {
    throw 'external_work_order_git_binding_missing'
}
$GitLock = Resolve-RegularPinnedFile -Path ([string]$GitBinding.path) -ExpectedSha256 ([string]$GitBinding.sha256) -Label 'git'
Add-RetainedDirectoryChain -LeafPath $GitLock.Path -BoundaryPath ([IO.Path]::GetDirectoryName($GitLock.Path)) -Seen $DirectoryLockPaths -Locks $DirectoryLocks -Label 'git_tool'
$ImportActiveUntrackedRaw = [string]::Concat(@(& $GitLock.Path -C $Root ls-files --others --exclude-standard -z -- . src))
if ($LASTEXITCODE -ne 0) {
    throw 'preimport_untracked_inventory_failed'
}
$ImportActiveIgnoredRaw = [string]::Concat(@(& $GitLock.Path -C $Root ls-files --others --ignored --exclude-standard -z -- . src))
if ($LASTEXITCODE -ne 0) {
    throw 'preimport_ignored_inventory_failed'
}
$ImportActiveUntracked = @(
    @($ImportActiveUntrackedRaw -split "`0") + @($ImportActiveIgnoredRaw -split "`0") |
        Where-Object { -not [string]::IsNullOrEmpty($_) } |
        Sort-Object -Unique
)
if (
    @($ImportActiveUntracked | Where-Object {
        $_ -match '(?i)(^|/)(conftest|pytest|ruff|sitecustomize|usercustomize)\.py$' -or
        $_ -match '(?i)\.(py|pyc|pyo|pyd|so|pth)$' -or
        [IO.Path]::GetFileName($_) -in @('.ruff.toml', 'pyproject.toml', 'pytest.ini', 'ruff.toml', 'setup.cfg', 'tox.ini')
    }).Count -ne 0
) {
    throw 'preimport_untracked_import_shadow_forbidden'
}
$PublisherRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($Publisher)))
$RunnerRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($Runner)))
if ($PublisherRoot -cne $Root) {
    throw 'publisher_project_origin_mismatch'
}
if ($RunnerRoot -cne $Root) {
    throw 'runner_project_origin_mismatch'
}

# -S is mandatory: -I alone still processes installed editable .pth files.
$Bootstrap = @'
import pathlib, runpy, sys
root = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
publisher = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
runner = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
site_packages = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
pycache_prefix = pathlib.Path(sys.argv.pop(1))
if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1 or sys.flags.no_site != 1:
    raise SystemExit('trusted_outer_requires_python_I_B_S')
if sys.pycache_prefix != str(pycache_prefix) or pycache_prefix.exists():
    raise SystemExit('trusted_outer_pycache_prefix_contract_mismatch')
if publisher.parents[2] != root or runner.parents[2] != root:
    raise SystemExit('trusted_outer_module_origin_mismatch')
stdlib_paths = [item for item in sys.path if item not in {str(root), str(root / 'src'), str(site_packages)}]
sys.path[:] = [*stdlib_paths, str(root / 'src'), str(root), str(site_packages)]
sys.argv[0] = str(publisher)
try:
    namespace = runpy.run_path(str(publisher), run_name='__evm_internal_non_authoritative_outer__')
    entry = namespace['_main_internal_non_authoritative']
    raise SystemExit(entry(sys.argv[1:], outer_invocation_authority_unproven=True))
finally:
    if pycache_prefix.exists():
        raise SystemExit('trusted_outer_pycache_prefix_created')
'@
if ($Bootstrap -match '[^\x00-\x7f]') {
    throw 'trusted_outer_bootstrap_source_must_be_ascii'
}

$PreviousScope = $env:EVM_PRE_R8_REVIEW_ENTRY_AUTHORITY_SCOPE
try {
    $env:EVM_PRE_R8_REVIEW_ENTRY_AUTHORITY_SCOPE = 'trusted_outer_internal_non_authoritative'
    $BoundPublisherArguments = @($PublisherArguments) + @(
        '--external-work-order', $WorkOrder,
        '--external-work-order-sha256', $ExternalWorkOrderSha256,
        '--trusted-outer', $Outer,
        '--trusted-outer-sha256', $ExpectedOuterSha256
    )
    $LateUntrackedRaw = [string]::Concat(@(& $GitLock.Path -C $Root ls-files --others --exclude-standard -z -- . src))
    if ($LASTEXITCODE -ne 0) {
        throw 'prelaunch_untracked_inventory_failed'
    }
    $LateIgnoredRaw = [string]::Concat(@(& $GitLock.Path -C $Root ls-files --others --ignored --exclude-standard -z -- . src))
    if ($LASTEXITCODE -ne 0) {
        throw 'prelaunch_ignored_inventory_failed'
    }
    $LateImportActive = @(
        @($LateUntrackedRaw -split "`0") + @($LateIgnoredRaw -split "`0") |
            Where-Object {
                -not [string]::IsNullOrEmpty($_) -and (
                    $_ -match '(?i)(^|/)(conftest|pytest|ruff|sitecustomize|usercustomize)\.py$' -or
                    $_ -match '(?i)\.(py|pyc|pyo|pyd|so|pth)$' -or
                    [IO.Path]::GetFileName($_) -in @('.ruff.toml', 'pyproject.toml', 'pytest.ini', 'ruff.toml', 'setup.cfg', 'tox.ini')
                )
            }
    )
    if ($LateImportActive.Count -ne 0) {
        throw 'prelaunch_import_shadow_forbidden'
    }
    foreach ($PinnedDirectory in $DirectoryLocks) {
        Assert-RetainedIdentityUnchanged -SafeHandle $PinnedDirectory.SafeHandle -Expected $PinnedDirectory.Identity -Label 'prelaunch_directory'
    }
    foreach ($PinnedFile in @($ToolContentLocks) + @($ToolExecutableLocks) + @($CodeLocks) + @($GitLock, $PowerShellLock, $WorkOrderLock, $RunnerLock, $PublisherLock, $PythonLock, $OuterLock)) {
        Assert-RetainedPinnedFileUnchanged -Pinned $PinnedFile -Label 'prelaunch_file'
    }
    $SavedErrorActionPreference = $ErrorActionPreference
    $SavedOutputEncoding = $OutputEncoding
    $ErrorActionPreference = 'Continue'
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    try {
        $LASTEXITCODE = $null
        $Bootstrap | & $Python -I -B -S -X "pycache_prefix=$PycachePrefix" - $Root $Publisher $Runner $SitePackages $PycachePrefix @BoundPublisherArguments
        $PublisherExitCode = $LASTEXITCODE
    }
    finally {
        $OutputEncoding = $SavedOutputEncoding
        $ErrorActionPreference = $SavedErrorActionPreference
    }
    if ($null -eq $PublisherExitCode) {
        throw 'trusted_outer_publisher_process_not_started'
    }
    if (Test-Path -LiteralPath $PycachePrefix) {
        throw 'trusted_outer_pycache_prefix_created'
    }
    exit $PublisherExitCode
}
finally {
    if ($null -eq $PreviousScope) {
        Remove-Item Env:EVM_PRE_R8_REVIEW_ENTRY_AUTHORITY_SCOPE -ErrorAction SilentlyContinue
    } else {
        $env:EVM_PRE_R8_REVIEW_ENTRY_AUTHORITY_SCOPE = $PreviousScope
    }
    foreach ($Pinned in @($ToolContentLocks) + @($ToolExecutableLocks) + @($CodeLocks) + @($GitLock, $PowerShellLock, $WorkOrderLock, $RunnerLock, $PublisherLock, $PythonLock, $OuterLock)) {
        if (($null -ne $Pinned) -and ($null -ne $Pinned.Stream)) {
            $Pinned.Stream.Dispose()
        }
    }
    foreach ($PinnedDirectory in $DirectoryLocks) {
        if (($null -ne $PinnedDirectory) -and ($null -ne $PinnedDirectory.SafeHandle)) {
            $PinnedDirectory.SafeHandle.Dispose()
        }
    }
}
