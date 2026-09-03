[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkOrderPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedWorkOrderSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedOuterSha256,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedGlobalRunId,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedRunUuid,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedAttemptUuid,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedCommit,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedTree,

    [Parameter(Mandatory = $true)]
    [UInt64]$ExpectedCanonicalRootVolumeSerial,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string]$ExpectedCanonicalRootFileIdHex,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedCanonicalRootSecurityDescriptorSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$canonicalSealRoot = 'F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-windows-qualification'
$canonicalWorkOrderRoot = 'F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-windows-qualification-work-orders'
$outerStage = 'parameter_validation'
$outerProcessLaunchAttempts = 0
$terminalRecord = $null
$terminalSealPublicationStage = 'not_started'
$FILE_FLAG_OPEN_REPARSE_POINT = [UInt32]0x00200000
$FILE_FLAG_BACKUP_SEMANTICS = [UInt32]0x02000000

$expectedRoles = @(
    'admission_source',
    'codex',
    'command_processor',
    'evm_package_init_source',
    'fixture',
    'interpreter',
    'powershell',
    'preparer',
    'qualifier',
    'r7s3_handle_io_source',
    'r7s4_handle_io_source',
    'runner_source',
    'scale_validation_package_init_source',
    'trusted_outer',
    'work_order_gate'
)
$openHandles = [System.Collections.Generic.List[System.IO.FileStream]]::new()

$assemblyName = [System.Reflection.AssemblyName]::new('R7S7QualificationOuterNative')
$assemblyBuilder = [AppDomain]::CurrentDomain.DefineDynamicAssembly(
    $assemblyName,
    [System.Reflection.Emit.AssemblyBuilderAccess]::Run
)
$moduleBuilder = $assemblyBuilder.DefineDynamicModule('R7S7QualificationOuterNative')
$nativeBuilder = $moduleBuilder.DefineType(
    'R7S7QualificationOuterNativeMethods',
    [System.Reflection.TypeAttributes]'Public,Abstract,Sealed'
)
function Add-InMemoryPInvoke {
    param(
        [string]$Name,
        [Type]$ReturnType,
        [Type[]]$ParameterTypes,
        [System.Runtime.InteropServices.CharSet]$CharSet = [System.Runtime.InteropServices.CharSet]::Auto
    )
    $method = $nativeBuilder.DefinePInvokeMethod(
        $Name,
        'kernel32.dll',
        [System.Reflection.MethodAttributes]'Public,Static,PinvokeImpl',
        [System.Reflection.CallingConventions]::Standard,
        $ReturnType,
        $ParameterTypes,
        [System.Runtime.InteropServices.CallingConvention]::Winapi,
        $CharSet
    )
    $method.SetImplementationFlags(
        $method.GetMethodImplementationFlags() -bor [System.Reflection.MethodImplAttributes]::PreserveSig
    )
}
Add-InMemoryPInvoke 'CreateFileW' ([IntPtr]) @(
    [string], [UInt32], [UInt32], [IntPtr], [UInt32], [UInt32], [IntPtr]
) ([System.Runtime.InteropServices.CharSet]::Unicode)
Add-InMemoryPInvoke 'GetFileInformationByHandleEx' ([bool]) @(
    [IntPtr], [int], [IntPtr], [UInt32]
)
Add-InMemoryPInvoke 'GetFinalPathNameByHandleW' ([UInt32]) @(
    [IntPtr], [System.Text.StringBuilder], [UInt32], [UInt32]
) ([System.Runtime.InteropServices.CharSet]::Unicode)
Add-InMemoryPInvoke 'GetFileType' ([UInt32]) @([IntPtr])
Add-InMemoryPInvoke 'FlushFileBuffers' ([bool]) @([IntPtr])
Add-InMemoryPInvoke 'SetFileInformationByHandle' ([bool]) @(
    [IntPtr], [int], [IntPtr], [UInt32]
)
Add-InMemoryPInvoke 'CloseHandle' ([bool]) @([IntPtr])
$nativeMethods = $nativeBuilder.CreateType()
$directoryGuards = [System.Collections.Generic.List[object]]::new()
$directoryGuardByPath = @{}

function New-BoundTemporaryStream {
    param([Parameter(Mandatory = $true)][string]$Path)

    $desiredAccess = [UInt32](0x80000000L -bor 0x40000000L -bor 0x00010000 -bor 0x00020000 -bor 0x00100000)
    $share = [UInt32]0x00000001
    $handle = $nativeMethods::CreateFileW(
        $Path, $desiredAccess, $share, [IntPtr]::Zero, 1, [UInt32]0x00000080, [IntPtr]::Zero
    )
    if ($handle -eq [IntPtr]::Zero -or $handle -eq [IntPtr](-1)) {
        throw "bound_create_new:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    try {
        $safeHandle = [Microsoft.Win32.SafeHandles.SafeFileHandle]::new($handle, $true)
        return [System.IO.FileStream]::new(
            $safeHandle,
            [System.IO.FileAccess]::ReadWrite,
            4096,
            $false
        )
    }
    catch {
        [void]$nativeMethods::CloseHandle($handle)
        throw
    }
}

function Rename-BoundNoReplace {
    param(
        [Parameter(Mandatory = $true)][System.IO.FileStream]$Stream,
        [Parameter(Mandatory = $true)]$DirectoryGuard,
        [Parameter(Mandatory = $true)][string]$FinalLeaf
    )

    if (
        [System.IO.Path]::GetFileName($FinalLeaf) -cne $FinalLeaf -or
        [string]::IsNullOrWhiteSpace($FinalLeaf)
    ) {
        throw 'bound_rename_final_leaf_invalid'
    }
    # SetFileInformationByHandle rejects a non-null RootDirectory for
    # FILE_RENAME_INFO on supported desktop Windows builds.  Bind the target
    # namespace instead by retaining the canonical directory and every
    # ancestor without delete sharing, then pass its exact final path.
    $targetPath = [System.IO.Path]::Combine($DirectoryGuard.FinalPath, $FinalLeaf)
    if (
        (Get-NormalizedWindowsPath ([System.IO.Path]::GetDirectoryName($targetPath))) -cne
            (Get-NormalizedWindowsPath $DirectoryGuard.FinalPath)
    ) {
        throw 'bound_rename_target_parent_mismatch'
    }
    $nameBytes = [System.Text.Encoding]::Unicode.GetBytes($targetPath)
    $rootOffset = if ([IntPtr]::Size -eq 8) { 8 } else { 4 }
    $lengthOffset = $rootOffset + [IntPtr]::Size
    $nameOffset = $lengthOffset + 4
    # Over-allocate a trailing UTF-16 NUL for filesystem filters that inspect
    # FileName as a string; FileNameLength still excludes it.
    $total = $nameOffset + $nameBytes.Length + 2
    $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal($total)
    try {
        for ($index = 0; $index -lt $total; $index++) {
            [Runtime.InteropServices.Marshal]::WriteByte($buffer, $index, 0)
        }
        # FILE_RENAME_INFO starts with BOOLEAN ReplaceIfExists.  False is the
        # strict no-replace policy.
        [Runtime.InteropServices.Marshal]::WriteByte($buffer, 0, 0)
        [Runtime.InteropServices.Marshal]::WriteIntPtr(
            $buffer, $rootOffset, [IntPtr]::Zero
        )
        [Runtime.InteropServices.Marshal]::WriteInt32(
            $buffer, $lengthOffset, $nameBytes.Length
        )
        [Runtime.InteropServices.Marshal]::Copy(
            $nameBytes, 0, [IntPtr]::Add($buffer, $nameOffset), $nameBytes.Length
        )
        if (-not $nativeMethods::SetFileInformationByHandle(
            $Stream.SafeFileHandle.DangerousGetHandle(),
            3,
            $buffer,
            [UInt32]$total
        )) {
            throw "bound_rename_no_replace:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer)
    }
}

function Close-DirectoryGuard {
    param($Guard)
    if ($null -ne $Guard -and $Guard.Handle -ne [IntPtr]::Zero) {
        [void]$nativeMethods::CloseHandle($Guard.Handle)
    }
}

function Flush-DirectoryGuard {
    param([Parameter(Mandatory = $true)]$Guard)
    if (-not $nativeMethods::FlushFileBuffers($Guard.Handle)) {
        throw "FlushFileBuffers:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
}

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Test-IsFullyQualifiedWindowsPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    # Windows PowerShell 5.1 targets .NET Framework, where
    # Path.IsPathFullyQualified is unavailable.  This work order intentionally
    # permits canonical drive-rooted paths only (no drive-relative, UNC or
    # device-namespace aliases).
    return $Path -cmatch '^[A-Za-z]:\\'
}

function Get-PathSecurityIdentity {
    param([Parameter(Mandatory = $true)][string]$Path)

    $security = Get-Acl -LiteralPath $Path
    $raw = $security.GetSecurityDescriptorBinaryForm()
    $control = [BitConverter]::ToUInt16($raw, 2)
    return [ordered]@{
        owner_sid = $security.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        security_descriptor_sha256 = Get-BytesSha256 $raw
        dacl_present = (($control -band 0x0004) -ne 0)
        dacl_protected = (($control -band 0x1000) -ne 0)
    }
}

function Get-NativeHandleIdentity {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$Handle,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $fileIdBuffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(24)
    $standardBuffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(24)
    $attributeBuffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(8)
    $basicBuffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(40)
    try {
        if (-not $nativeMethods::GetFileInformationByHandleEx($Handle, 18, $fileIdBuffer, 24)) {
            throw "GetFileInformationByHandleEx:file-id:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
        if (-not $nativeMethods::GetFileInformationByHandleEx($Handle, 1, $standardBuffer, 24)) {
            throw "GetFileInformationByHandleEx:standard:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
        if (-not $nativeMethods::GetFileInformationByHandleEx($Handle, 9, $attributeBuffer, 8)) {
            throw "GetFileInformationByHandleEx:attribute:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
        if (-not $nativeMethods::GetFileInformationByHandleEx($Handle, 0, $basicBuffer, 40)) {
            throw "GetFileInformationByHandleEx:basic:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
        $volumeSerial = [BitConverter]::ToUInt64(
            [BitConverter]::GetBytes(
                [Runtime.InteropServices.Marshal]::ReadInt64($fileIdBuffer, 0)
            ),
            0
        )
        $fileIdBytes = [byte[]]::new(16)
        [Runtime.InteropServices.Marshal]::Copy(
            [IntPtr]::Add($fileIdBuffer, 8), $fileIdBytes, 0, 16
        )
        $pathBuffer = [System.Text.StringBuilder]::new(32768)
        $pathCount = $nativeMethods::GetFinalPathNameByHandleW(
            $Handle, $pathBuffer, [UInt32]$pathBuffer.Capacity, 0
        )
        if ($pathCount -eq 0 -or $pathCount -ge $pathBuffer.Capacity) {
            throw "GetFinalPathNameByHandleW:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
        $finalPath = $pathBuffer.ToString()
        if ($finalPath.StartsWith('\\?\')) { $finalPath = $finalPath.Substring(4) }
        $attributes = [UInt32][Runtime.InteropServices.Marshal]::ReadInt32($attributeBuffer, 0)
        $reparseTag = [UInt32][Runtime.InteropServices.Marshal]::ReadInt32($attributeBuffer, 4)
        $security = Get-PathSecurityIdentity -Path $Path
        $creationFileTime = [Runtime.InteropServices.Marshal]::ReadInt64($basicBuffer, 0)
        return [ordered]@{
            final_path = $finalPath
            volume_serial_number = $volumeSerial
            file_id_hex = ([BitConverter]::ToString($fileIdBytes)).Replace('-', '').ToLowerInvariant()
            owner_sid = $security.owner_sid
            security_descriptor_sha256 = $security.security_descriptor_sha256
            dacl_present = $security.dacl_present
            dacl_protected = $security.dacl_protected
            link_count = [UInt32][Runtime.InteropServices.Marshal]::ReadInt32($standardBuffer, 16)
            reparse_tag = $reparseTag
            file_type = $nativeMethods::GetFileType($Handle)
            creation_time_ns = [Int64](($creationFileTime - 116444736000000000L) * 100L)
            is_directory = (($attributes -band 0x00000010) -ne 0)
        }
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($basicBuffer)
        [Runtime.InteropServices.Marshal]::FreeHGlobal($attributeBuffer)
        [Runtime.InteropServices.Marshal]::FreeHGlobal($standardBuffer)
        [Runtime.InteropServices.Marshal]::FreeHGlobal($fileIdBuffer)
    }
}

function Assert-IdentityMatchesExpected {
    param(
        [Parameter(Mandatory = $true)]$Observed,
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$Directory
    )

    $keys = @(
        'volume_serial_number', 'file_id_hex', 'owner_sid',
        'security_descriptor_sha256', 'dacl_present', 'dacl_protected',
        'link_count', 'reparse_tag', 'file_type'
    )
    if ($Directory) { $keys += 'is_directory' } else { $keys += 'creation_time_ns' }
    if (
        (Get-NormalizedWindowsPath ([string]$Observed.final_path)) -cne
            (Get-NormalizedWindowsPath ([string]$Expected.final_path))
    ) {
        throw "retained_identity_final_path_mismatch:$Role"
    }
    foreach ($key in $keys) {
        if ($Observed.$key -ne $Expected.$key) {
            throw "retained_identity_field_mismatch:$Role`:$key"
        }
    }
}

function Assert-SameFileIdentityContinuity {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )

    foreach ($key in @(
        'volume_serial_number', 'file_id_hex', 'owner_sid',
        'security_descriptor_sha256', 'dacl_present', 'dacl_protected',
        'link_count', 'reparse_tag', 'file_type', 'creation_time_ns',
        'is_directory'
    )) {
        if ($Before.$key -ne $After.$key) {
            throw "outer_seal_same_handle_identity_mismatch:$key"
        }
    }
    if (
        $Before.file_type -ne 1 -or
        $Before.is_directory -ne $false -or
        $Before.reparse_tag -ne 0 -or
        $Before.link_count -ne 1 -or
        $Before.dacl_present -ne $true
    ) {
        throw 'outer_seal_unsafe_file_identity'
    }
}

function Open-MeasuredDirectoryGuard {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $key = Get-NormalizedWindowsPath $resolved
    if ($directoryGuardByPath.ContainsKey($key)) {
        return $directoryGuardByPath[$key]
    }
    $access = [UInt32](0x00000001 -bor 0x00020000 -bor 0x00100000)
    $share = [UInt32](0x00000001 -bor 0x00000002)
    $flags = [UInt32]($FILE_FLAG_OPEN_REPARSE_POINT -bor $FILE_FLAG_BACKUP_SEMANTICS)
    $handle = $nativeMethods::CreateFileW(
        $resolved, $access, $share, [IntPtr]::Zero, 3, $flags, [IntPtr]::Zero
    )
    if ($handle -eq [IntPtr]::Zero -or $handle -eq [IntPtr](-1)) {
        throw "directory_guard_open_failed:$Role`:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    try {
        $identity = Get-NativeHandleIdentity -Handle $handle -Path $resolved
        if (
            $identity.file_type -ne 1 -or
            $identity.is_directory -ne $true -or
            $identity.reparse_tag -ne 0 -or
            $identity.dacl_present -ne $true
        ) {
            throw "unsafe_directory_guard_identity:$Role"
        }
        $guard = [pscustomobject]@{
            Handle = $handle
            Identity = $identity
            Path = $resolved
            Role = $Role
        }
        $directoryGuards.Add($guard)
        $directoryGuardByPath[$key] = $guard
        return $guard
    }
    catch {
        [void]$nativeMethods::CloseHandle($handle)
        throw
    }
}

function Open-AncestorDirectoryGuards {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($resolved)
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "directory_guard_root_missing:$Role"
    }
    $current = $root
    [void](Open-MeasuredDirectoryGuard -Path $current -Role "$Role`:volume-root")
    $relative = $resolved.Substring($root.Length).Trim('\')
    if (-not [string]::IsNullOrWhiteSpace($relative)) {
        foreach ($component in $relative.Split('\')) {
            $current = [System.IO.Path]::Combine($current, $component)
            [void](Open-MeasuredDirectoryGuard -Path $current -Role "$Role`:ancestor")
        }
    }
}

function Open-PinnedDirectoryGuard {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$Role
    )

    Open-AncestorDirectoryGuards -Path ([string]$Expected.final_path) -Role $Role
    $key = Get-NormalizedWindowsPath ([string]$Expected.final_path)
    $guard = $directoryGuardByPath[$key]
    Assert-IdentityMatchesExpected `
        -Observed $guard.Identity `
        -Expected $Expected `
        -Role $Role `
        -Directory
    return $guard
}

function Get-DirectoryGuardEvidence {
    $rows = @()
    foreach ($guard in $directoryGuards) {
        $rows += [ordered]@{
            role = $guard.Role
            final_path = $guard.Identity.final_path
            volume_serial_number = $guard.Identity.volume_serial_number
            file_id_hex = $guard.Identity.file_id_hex
            owner_sid = $guard.Identity.owner_sid
            security_descriptor_sha256 = $guard.Identity.security_descriptor_sha256
            dacl_present = $guard.Identity.dacl_present
            dacl_protected = $guard.Identity.dacl_protected
            reparse_tag = $guard.Identity.reparse_tag
            retained_no_delete_share = $true
        }
    }
    return @($rows)
}

function Open-CanonicalSealRootGuard {
    $resolved = [System.IO.Path]::GetFullPath($canonicalSealRoot)
    if ((Get-NormalizedWindowsPath $resolved) -cne (Get-NormalizedWindowsPath $canonicalSealRoot)) {
        throw 'canonical_seal_root_path_mismatch'
    }
    $attributes = [System.IO.File]::GetAttributes($resolved)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'canonical_seal_root_reparse_forbidden'
    }
    Open-AncestorDirectoryGuards -Path $resolved -Role 'canonical_seal_root'
    $access = [UInt32](0x40000000 -bor 0x00000001 -bor 0x00020000 -bor 0x00100000)
    $share = [UInt32](0x00000001 -bor 0x00000002)
    $flags = [UInt32]($FILE_FLAG_OPEN_REPARSE_POINT -bor $FILE_FLAG_BACKUP_SEMANTICS)
    $handle = $nativeMethods::CreateFileW(
        $resolved, $access, $share, [IntPtr]::Zero, 3, $flags, [IntPtr]::Zero
    )
    if ($handle -eq [IntPtr]::Zero -or $handle -eq [IntPtr](-1)) {
        throw "CreateFileW:win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    $guard = $null
    try {
        $identity = Get-NativeHandleIdentity -Handle $handle -Path $resolved
        $guard = [pscustomobject]@{
            Handle = $handle
            VolumeSerialNumber = $identity.volume_serial_number
            FileIdHex = $identity.file_id_hex
            FinalPath = $identity.final_path
            Identity = $identity
        }
        if (
            $guard.VolumeSerialNumber -ne $ExpectedCanonicalRootVolumeSerial -or
            $guard.FileIdHex -cne $ExpectedCanonicalRootFileIdHex -or
            (Get-NormalizedWindowsPath $guard.FinalPath) -cne (Get-NormalizedWindowsPath $resolved)
        ) {
            throw 'canonical_seal_root_oob_identity_mismatch'
        }
        if (
            $identity.security_descriptor_sha256 -cne $ExpectedCanonicalRootSecurityDescriptorSha256 -or
            $identity.dacl_present -ne $true -or
            $identity.dacl_protected -ne $true -or
            $identity.is_directory -ne $true -or
            $identity.reparse_tag -ne 0
        ) {
            throw 'canonical_seal_root_oob_security_mismatch'
        }
        return $guard
    }
    catch {
        if ($null -ne $guard) { Close-DirectoryGuard $guard }
        else { [void]$nativeMethods::CloseHandle($handle) }
        throw
    }
}

function Publish-OuterTerminalSeal {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('failure', 'emergency')][string]$Kind,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Payload
    )

    if (
        $ExpectedRunUuid -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        $ExpectedAttemptUuid -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ) {
        throw 'canonical_uuid4_required_for_outer_seal'
    }
    $leaf = "$ExpectedRunUuid.$ExpectedAttemptUuid.outer-$Kind-seal.json"
    $temporaryLeaf = ".$leaf.partial"
    $finalPath = [System.IO.Path]::Combine($canonicalSealRoot, $leaf)
    $temporaryPath = [System.IO.Path]::Combine($canonicalSealRoot, $temporaryLeaf)
    $script:terminalSealPublicationStage = "${Kind}_open_canonical_parent"
    $guard = Open-CanonicalSealRootGuard
    $stream = $null
    try {
        if ([System.IO.File]::Exists($finalPath) -or [System.IO.File]::Exists($temporaryPath)) {
            throw "outer_${Kind}_seal_collision"
        }
        $script:terminalSealPublicationStage = "${Kind}_serialize"
        $raw = [System.Text.UTF8Encoding]::new($false).GetBytes(
            (($Payload | ConvertTo-Json -Compress -Depth 12) + "`n")
        )
        $script:terminalSealPublicationStage = "${Kind}_create_new"
        $stream = New-BoundTemporaryStream -Path $temporaryPath
        $script:terminalSealPublicationStage = "${Kind}_pre_rename_file_identity"
        $preRenameIdentity = Get-NativeHandleIdentity `
            -Handle $stream.SafeFileHandle.DangerousGetHandle() `
            -Path $temporaryPath
        if (
            (Get-NormalizedWindowsPath $preRenameIdentity.final_path) -cne
                (Get-NormalizedWindowsPath $temporaryPath)
        ) {
            throw "outer_${Kind}_seal_temporary_handle_path_mismatch"
        }
        $script:terminalSealPublicationStage = "${Kind}_write"
        $stream.Write($raw, 0, $raw.Length)
        $script:terminalSealPublicationStage = "${Kind}_file_flush"
        $stream.Flush($true)
        $script:terminalSealPublicationStage = "${Kind}_same_handle_readback"
        $stream.Position = 0
        $readback = [byte[]]::new($raw.Length)
        if ($stream.Read($readback, 0, $readback.Length) -ne $raw.Length) {
            throw "outer_${Kind}_seal_short_read"
        }
        if ((Get-BytesSha256 $readback) -cne (Get-BytesSha256 $raw)) {
            throw "outer_${Kind}_seal_readback_mismatch"
        }
        $script:terminalSealPublicationStage = "${Kind}_atomic_move_no_replace"
        Rename-BoundNoReplace `
            -Stream $stream `
            -DirectoryGuard $guard `
            -FinalLeaf $leaf
        $script:terminalSealPublicationStage = "${Kind}_same_handle_post_rename_readback"
        $stream.Position = 0
        $postRenameReadback = [byte[]]::new($raw.Length)
        if ($stream.Read($postRenameReadback, 0, $postRenameReadback.Length) -ne $raw.Length) {
            throw "outer_${Kind}_seal_post_rename_short_read"
        }
        if ((Get-BytesSha256 $postRenameReadback) -cne (Get-BytesSha256 $raw)) {
            throw "outer_${Kind}_seal_post_rename_readback_mismatch"
        }
        $renamedPathBuffer = [System.Text.StringBuilder]::new(32768)
        $renamedPathCount = $nativeMethods::GetFinalPathNameByHandleW(
            $stream.SafeFileHandle.DangerousGetHandle(),
            $renamedPathBuffer,
            [UInt32]$renamedPathBuffer.Capacity,
            0
        )
        if ($renamedPathCount -eq 0 -or $renamedPathCount -ge $renamedPathBuffer.Capacity) {
            throw "outer_${Kind}_seal_final_handle_path_unavailable"
        }
        $renamedHandlePath = $renamedPathBuffer.ToString()
        if ($renamedHandlePath.StartsWith('\\?\')) {
            $renamedHandlePath = $renamedHandlePath.Substring(4)
        }
        if (
            (Get-NormalizedWindowsPath $renamedHandlePath) -cne
                (Get-NormalizedWindowsPath $finalPath)
        ) {
            throw "outer_${Kind}_seal_final_handle_path_mismatch"
        }
        $script:terminalSealPublicationStage = "${Kind}_post_rename_file_identity"
        $postRenameIdentity = Get-NativeHandleIdentity `
            -Handle $stream.SafeFileHandle.DangerousGetHandle() `
            -Path $finalPath
        if (
            (Get-NormalizedWindowsPath $postRenameIdentity.final_path) -cne
                (Get-NormalizedWindowsPath $finalPath)
        ) {
            throw "outer_${Kind}_seal_post_rename_identity_path_mismatch"
        }
        Assert-SameFileIdentityContinuity `
            -Before $preRenameIdentity `
            -After $postRenameIdentity
        $script:terminalSealPublicationStage = "${Kind}_directory_flush"
        Flush-DirectoryGuard $guard
        $script:terminalSealPublicationStage = "${Kind}_published"
        return [ordered]@{
            kind = $Kind
            path = $finalPath
            sha256 = Get-BytesSha256 $raw
            bytes = $raw.Length
            create_new_count = 1
            atomic_move_no_replace_count = 1
            file_flush_count = 1
            directory_flush_count = 1
            same_handle_pre_and_post_rename_readback = $true
            same_handle_final_path_readback = $true
            same_handle_file_identity_continuity = $true
            pre_rename_file_identity = $preRenameIdentity
            post_rename_file_identity = $postRenameIdentity
            publication_directory_identity = $guard.Identity
            target_namespace_guarded_by_retained_handles = $true
            path_rename_fallback_count = 0
            overwrite_count = 0
            delete_count = 0
        }
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
        Close-DirectoryGuard $guard
    }
}

function Get-OuterPartialObservation {
    param([Parameter(Mandatory = $true)][ValidateSet('failure', 'emergency')][string]$Kind)

    $leaf = "$ExpectedRunUuid.$ExpectedAttemptUuid.outer-$Kind-seal.json"
    $temporaryPath = [System.IO.Path]::Combine($canonicalSealRoot, ".$leaf.partial")
    $finalPath = [System.IO.Path]::Combine($canonicalSealRoot, $leaf)
    foreach ($candidate in @($finalPath, $temporaryPath)) {
        if (-not [System.IO.File]::Exists($candidate)) { continue }
        try {
            $guard = Open-CanonicalSealRootGuard
            $stream = $null
            try {
                $stream = [System.IO.FileStream]::new(
                    $candidate,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Read,
                    [System.IO.FileShare]::Read
                )
                return [ordered]@{
                    status = if ($candidate -ceq $finalPath) { 'final_renamed' } else { 'partial' }
                    path = $candidate
                    sha256 = Get-HandleSha256 -Stream $stream
                    bytes = $stream.Length
                    preserved_unmodified = $true
                    cleanup_attempted = $false
                }
            }
            finally {
                if ($null -ne $stream) { $stream.Dispose() }
                Close-DirectoryGuard $guard
            }
        }
        catch {
            return [ordered]@{
                status = 'present_but_readback_unproven'
                path_recorded = $false
                sha256 = $null
                bytes = $null
                preserved_unmodified = $true
                cleanup_attempted = $false
            }
        }
    }
    return [ordered]@{
        status = 'not_started_or_no_partial_observed'
        path_recorded = $false
        sha256 = $null
        bytes = $null
        preserved_unmodified = $true
        cleanup_attempted = $false
    }
}

function Get-HandleSha256 {
    param([Parameter(Mandatory = $true)][System.IO.FileStream]$Stream)

    $originalPosition = $Stream.Position
    try {
        $Stream.Position = 0
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($algorithm.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant()
        }
        finally {
            $algorithm.Dispose()
        }
    }
    finally {
        $Stream.Position = $originalPosition
    }
}

function Open-PinnedReadHandle {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][string]$Role,
        $ExpectedIdentity = $null
    )

    if (-not (Test-IsFullyQualifiedWindowsPath $Path)) {
        throw "absolute_pin_path_required:$Role"
    }
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $stream = [System.IO.FileStream]::new(
        $resolved,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        $actualSha256 = Get-HandleSha256 -Stream $stream
        if ($actualSha256 -cne $ExpectedSha256) {
            throw "pin_sha256_mismatch:$Role"
        }
        $identity = Get-NativeHandleIdentity `
            -Handle $stream.SafeFileHandle.DangerousGetHandle() `
            -Path $resolved
        if ($null -ne $ExpectedIdentity) {
            Assert-IdentityMatchesExpected `
                -Observed $identity `
                -Expected $ExpectedIdentity `
                -Role $Role
        }
        $openHandles.Add($stream)
        return [pscustomobject]@{
            Role = $Role
            Path = $resolved
            Sha256 = $actualSha256
            Bytes = $stream.Length
            Stream = $stream
            Identity = $identity
            ShareMode = 'read_only_no_write_no_delete'
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Read-AllHandleBytes {
    param([Parameter(Mandatory = $true)][System.IO.FileStream]$Stream)

    if ($Stream.Length -gt 16MB) {
        throw 'work_order_size_limit_exceeded'
    }
    $buffer = [byte[]]::new([int]$Stream.Length)
    $Stream.Position = 0
    $offset = 0
    while ($offset -lt $buffer.Length) {
        $read = $Stream.Read($buffer, $offset, $buffer.Length - $offset)
        if ($read -le 0) {
            throw 'work_order_handle_short_read'
        }
        $offset += $read
    }
    $Stream.Position = 0
    return $buffer
}

function Get-NormalizedWindowsPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\').ToLowerInvariant()
}

function Assert-WorkOrderOriginBound {
    param(
        [Parameter(Mandatory = $true)]$HandleRecord,
        [Parameter(Mandatory = $true)][string]$DeclaredPath,
        [Parameter(Mandatory = $true)]$DeclaredParentIdentity
    )

    if (-not (Test-IsFullyQualifiedWindowsPath $DeclaredPath)) {
        throw 'work_order_declared_path_not_absolute'
    }
    $declaredResolved = [System.IO.Path]::GetFullPath($DeclaredPath)
    $canonicalLeaf = "windows-qualification-work-order-$ExpectedRunUuid-$ExpectedAttemptUuid.json"
    $canonicalDeclaredPath = [System.IO.Path]::Combine($canonicalWorkOrderRoot, $canonicalLeaf)
    if (
        (Get-NormalizedWindowsPath $declaredResolved) -cne
            (Get-NormalizedWindowsPath $canonicalDeclaredPath) -or
        (Get-NormalizedWindowsPath ([string]$DeclaredParentIdentity.final_path)) -cne
            (Get-NormalizedWindowsPath $canonicalWorkOrderRoot)
    ) {
        throw 'work_order_declared_path_not_canonical'
    }
    if (
        (Get-NormalizedWindowsPath $HandleRecord.Path) -cne
            (Get-NormalizedWindowsPath $declaredResolved) -or
        (Get-NormalizedWindowsPath $HandleRecord.Identity.final_path) -cne
            (Get-NormalizedWindowsPath $declaredResolved)
    ) {
        throw 'work_order_handle_path_origin_mismatch'
    }
    $actualParent = [System.IO.Path]::GetDirectoryName($HandleRecord.Identity.final_path)
    if (
        [string]::IsNullOrWhiteSpace($actualParent) -or
        (Get-NormalizedWindowsPath $actualParent) -cne
            (Get-NormalizedWindowsPath ([string]$DeclaredParentIdentity.final_path))
    ) {
        throw 'work_order_handle_parent_origin_mismatch'
    }
}

try {
    $outerStage = 'oob_identity_validation'
    if (
        $ExpectedGlobalRunId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        $ExpectedRunUuid -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        $ExpectedAttemptUuid -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ) {
        throw 'oob_uuid4_contract_mismatch'
    }
    $outerStage = 'work_order_sha256'
    $workOrderHandle = Open-PinnedReadHandle `
        -Path $WorkOrderPath `
        -ExpectedSha256 $ExpectedWorkOrderSha256 `
        -Role 'work_order'
    $workOrderRaw = Read-AllHandleBytes -Stream $workOrderHandle.Stream
    $outerStage = 'work_order_parse'
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $workOrder = $strictUtf8.GetString($workOrderRaw) | ConvertFrom-Json

    $actualRoles = @($workOrder.file_bindings.PSObject.Properties.Name | Sort-Object)
    if (($actualRoles -join "`n") -cne (($expectedRoles | Sort-Object) -join "`n")) {
        throw 'work_order_file_binding_roles_mismatch'
    }
    if (
        $workOrder.global_run_id -cne $ExpectedGlobalRunId -or
        $workOrder.run_uuid -cne $ExpectedRunUuid -or
        $workOrder.attempt_uuid -cne $ExpectedAttemptUuid -or
        $workOrder.commit -cne $ExpectedCommit -or
        $workOrder.tree -cne $ExpectedTree
    ) {
        throw 'work_order_independent_expectation_mismatch'
    }
    $outerStage = 'work_order_origin_binding'
    Assert-WorkOrderOriginBound `
        -HandleRecord $workOrderHandle `
        -DeclaredPath ([string]$workOrder.work_order_path) `
        -DeclaredParentIdentity $workOrder.work_order_parent_identity
    [void](Open-PinnedDirectoryGuard `
        -Expected $workOrder.work_order_parent_identity `
        -Role 'work_order_parent')
    $outerStage = 'preimport_directory_identity_guards'
    [void](Open-PinnedDirectoryGuard `
        -Expected $workOrder.normalized_invocation.working_directory_identity `
        -Role 'working_directory')
    [void](Open-PinnedDirectoryGuard `
        -Expected $workOrder.output_parent_identity `
        -Role 'output_parent')
    [void](Open-PinnedDirectoryGuard `
        -Expected $workOrder.pycache_parent_identity `
        -Role 'pycache_parent')
    foreach ($role in $expectedRoles) {
        [void](Open-PinnedDirectoryGuard `
            -Expected $workOrder.file_bindings.$role.parent_directory_identity `
            -Role "$role`:parent")
    }
    $outerStage = 'source_tool_pin'
    $pins = @{}
    foreach ($role in $expectedRoles) {
        $binding = $workOrder.file_bindings.$role
        if ($binding.role -cne "qualification:$role") {
            throw "work_order_binding_role_mismatch:$role"
        }
        $pin = Open-PinnedReadHandle `
            -Path ([string]$binding.final_path) `
            -ExpectedSha256 ([string]$binding.sha256) `
            -Role $role `
            -ExpectedIdentity $binding
        if ($pin.Bytes -ne [int64]$binding.bytes) {
            throw "work_order_binding_size_mismatch:$role"
        }
        $pins[$role] = $pin
    }

    $outerStage = 'trusted_outer_sha256'
    if (
        (Get-NormalizedWindowsPath $pins['trusted_outer'].Path) -cne
            (Get-NormalizedWindowsPath $PSCommandPath) -or
        $pins['trusted_outer'].Sha256 -cne $ExpectedOuterSha256
    ) {
        throw 'trusted_outer_path_or_independent_sha_mismatch'
    }
    $outerStage = 'powershell_pin'
    $currentPowerShell = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    if (
        (Get-NormalizedWindowsPath $pins['powershell'].Path) -cne
            (Get-NormalizedWindowsPath $currentPowerShell)
    ) {
        throw 'current_powershell_path_pin_mismatch'
    }
    $outerStage = 'pycache_preflight'
    $pycachePrefix = [System.IO.Path]::GetFullPath([string]$workOrder.pycache_prefix)
    if (
        -not (Test-IsFullyQualifiedWindowsPath $pycachePrefix) -or
        [System.IO.File]::Exists($pycachePrefix) -or
        [System.IO.Directory]::Exists($pycachePrefix)
    ) {
        throw 'pycache_prefix_not_fresh_and_absent'
    }

    # Re-hash every retained, read-only/no-delete-share handle at the final
    # boundary. No pinned path is reopened and all handles stay live until the
    # Python bootstrap returns.
    foreach ($pin in $pins.Values) {
        if ((Get-HandleSha256 -Stream $pin.Stream) -cne $pin.Sha256) {
            throw "retained_handle_sha_changed:$($pin.Role)"
        }
        $launchIdentity = Get-NativeHandleIdentity `
            -Handle $pin.Stream.SafeFileHandle.DangerousGetHandle() `
            -Path $pin.Path
        Assert-IdentityMatchesExpected `
            -Observed $launchIdentity `
            -Expected $pin.Identity `
            -Role "$($pin.Role):launch-boundary"
    }
    if ((Get-HandleSha256 -Stream $workOrderHandle.Stream) -cne $ExpectedWorkOrderSha256) {
        throw 'retained_work_order_sha_changed'
    }

    $outerStage = 'toolchain_runtime_closure_gate'
    if (
        $workOrder.toolchain_runtime_closure_state -cne 'unproven' -or
        -not (@($workOrder.reviewer_blockers) -ccontains 'python_runtime_transitive_closure_unproven')
    ) {
        throw 'toolchain_runtime_closure_contract_mismatch'
    }
    # The interpreter EXE alone does not bind the Python DLL, stdlib and native
    # extension import closure. Until an independently pinned retained-handle
    # inventory is provisioned, the internal qualification remains not_run.
    throw 'toolchain_runtime_closure_unproven'

    $outerStage = 'bootstrap_prepare'
    $bootstrap = @'
import hashlib, importlib, importlib.util, json, pathlib, sys

if (
    sys.flags.isolated != 1
    or sys.flags.no_user_site != 1
    or sys.flags.no_site != 1
    or sys.flags.dont_write_bytecode != 1
    or not sys.pycache_prefix
    or pathlib.Path(sys.pycache_prefix).exists()
):
    raise RuntimeError("trusted_outer_python_bootstrap_contract_mismatch")

def load_exact(name, path_value):
    path = pathlib.Path(path_value).resolve(strict=True)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module_spec_unavailable:{name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if pathlib.Path(module.__file__).resolve(strict=True) != path:
        raise RuntimeError(f"module_origin_mismatch:{name}")
    return module

def import_gate_exact(path_value):
    path = pathlib.Path(path_value).resolve(strict=True)
    source_root = path.parents[2]
    sys.path.insert(0, str(source_root))
    name = "evm.scale_validation.phase_b2_r7s7_qualification_work_order"
    module = importlib.import_module(name)
    if pathlib.Path(module.__file__).resolve(strict=True) != path:
        raise RuntimeError("module_origin_mismatch:work_order_gate")
    return module

work_order_path = pathlib.Path(sys.argv[1]).resolve(strict=True)
raw = work_order_path.read_bytes()
if hashlib.sha256(raw).hexdigest() != sys.argv[2]:
    raise RuntimeError("work_order_oob_digest_mismatch")
gate = import_gate_exact(sys.argv[3])
expectation = gate.QualificationWorkOrderExpectation(
    work_order_sha256=sys.argv[2],
    global_run_id=sys.argv[5],
    run_uuid=sys.argv[6],
    attempt_uuid=sys.argv[7],
    commit=sys.argv[8],
    tree=sys.argv[9],
)
token = gate.verify_internal_qualification_work_order(raw, expected=expectation)
order_json = json.loads(raw)
handle_io = importlib.import_module("evm.scale_validation.phase_b2_r7s3_handle_io")
api = handle_io.WindowsHandleApi()
retained_kernel_handles = []

def normalize(path_value):
    return str(pathlib.Path(path_value).resolve(strict=True)).lower()

def assert_directory_identity(expected):
    handle = api.open_directory(expected["final_path"])
    retained_kernel_handles.append(handle)
    observed = api.identity(handle).to_dict()
    comparable = {
        "final_path": observed["final_path"],
        "volume_serial_number": observed["volume_serial_number"],
        "file_id_hex": observed["file_id_hex"],
        "owner_sid": observed["owner_sid"],
        "security_descriptor_sha256": observed["security_descriptor_sha256"],
        "dacl_present": observed["dacl_present"],
        "dacl_protected": observed["dacl_protected"],
        "link_count": observed["link_count"],
        "reparse_tag": observed["reparse_tag"],
        "file_type": observed["file_type"],
        "is_directory": bool(observed["attributes"] & handle_io.FILE_ATTRIBUTE_DIRECTORY),
    }
    expected_comparable = {key: expected[key] for key in comparable}
    if normalize(comparable.pop("final_path")) != normalize(expected_comparable.pop("final_path")):
        raise RuntimeError("retained_directory_final_path_mismatch")
    if comparable != expected_comparable:
        raise RuntimeError("retained_directory_identity_mismatch")

def assert_file_identity(role, expected):
    handle = api.open_read(expected["final_path"])
    retained_kernel_handles.append(handle)
    observed = api.identity(handle).to_dict()
    raw_file = api.read_all(handle, observed["size"])
    comparable = {
        "volume_serial_number": observed["volume_serial_number"],
        "file_id_hex": observed["file_id_hex"],
        "sha256": __import__("hashlib").sha256(raw_file).hexdigest(),
        "bytes": len(raw_file),
        "owner_sid": observed["owner_sid"],
        "security_descriptor_sha256": observed["security_descriptor_sha256"],
        "dacl_present": observed["dacl_present"],
        "dacl_protected": observed["dacl_protected"],
        "link_count": observed["link_count"],
        "reparse_tag": observed["reparse_tag"],
        "file_type": observed["file_type"],
        "creation_time_ns": pathlib.Path(expected["final_path"]).stat().st_ctime_ns,
    }
    if normalize(observed["final_path"]) != normalize(expected["final_path"]):
        raise RuntimeError(f"retained_file_final_path_mismatch:{role}")
    if comparable != {key: expected[key] for key in comparable}:
        raise RuntimeError(f"retained_file_identity_mismatch:{role}")
    assert_directory_identity(expected["parent_directory_identity"])

try:
    assert_directory_identity(order_json["output_parent_identity"])
    assert_directory_identity(order_json["pycache_parent_identity"])
    assert_directory_identity(order_json["work_order_parent_identity"])
    assert_directory_identity(
        order_json["normalized_invocation"]["working_directory_identity"]
    )
    for role in gate.FILE_BINDING_ROLES:
        assert_file_identity(role, order_json["file_bindings"][role])
    qualifier = load_exact("r7s7_windows_qualifier", sys.argv[4])
    config = qualifier.QualificationConfig(**gate.qualification_config_projection(token))
    result = qualifier.run_internal_non_authoritative_once(config, work_order=token)
    print(json.dumps(result, allow_nan=False, separators=(",", ":"), sort_keys=True))
finally:
    for handle in reversed(retained_kernel_handles):
        api.close(handle)
'@
    $outerStage = 'python_launch_or_child_execution'
    $outerProcessLaunchAttempts = 1
    & ($pins['interpreter'].Path) '-I' '-B' '-S' '-X' "pycache_prefix=$pycachePrefix" '-c' $bootstrap `
        $workOrderHandle.Path `
        $ExpectedWorkOrderSha256 `
        $pins['work_order_gate'].Path `
        $pins['qualifier'].Path `
        $ExpectedGlobalRunId `
        $ExpectedRunUuid `
        $ExpectedAttemptUuid `
        $ExpectedCommit `
        $ExpectedTree
    if ($LASTEXITCODE -ne 0) {
        throw "qualification_bootstrap_failed:$LASTEXITCODE"
    }
    $outerStage = 'pycache_postcondition'
    if (
        [System.IO.File]::Exists($pycachePrefix) -or
        [System.IO.Directory]::Exists($pycachePrefix)
    ) {
        throw 'pycache_prefix_postcondition_violated'
    }
}
catch {
    $original = $_
    $safeCode = if ($original.Exception.Message -match '^[A-Za-z0-9:_-]{1,200}$') {
        $original.Exception.Message
    }
    else {
        'redacted_exception'
    }
    $baseCounts = [ordered]@{
        outer_invocation = 1
        outer_process_launch_attempt = $outerProcessLaunchAttempts
        automatic_retry = 0
        followup_probe = 0
        force_termination = 0
        success_marker = 0
        completion_marker = 0
        overwrite = 0
        delete = 0
    }
    $failurePayload = [ordered]@{
        schema = 'evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-outer-failure-seal.v1'
        authority = 'trusted_outer_internal_non_authoritative'
        status = 'failed'
        decision = 'NO-GO'
        credit = 'zero_credit'
        reviewer_pending = $true
        global_run_id = $ExpectedGlobalRunId
        run_uuid = $ExpectedRunUuid
        attempt_uuid = $ExpectedAttemptUuid
        commit = $ExpectedCommit
        tree = $ExpectedTree
        failed_stage = $outerStage
        error_type = $original.Exception.GetType().FullName
        error_code = $safeCode
        work_order_sha256 = $ExpectedWorkOrderSha256
        outer_sha256 = $ExpectedOuterSha256
        raw_work_order_recorded = $false
        secret_recorded = $false
        nonce_recorded = $false
        command_line_recorded = $false
        preimport_directory_guards = @(Get-DirectoryGuardEvidence)
        counts = $baseCounts
        production_go = $false
    }
    try {
        $failurePublication = Publish-OuterTerminalSeal -Kind 'failure' -Payload $failurePayload
        $terminalRecord = [ordered]@{
            schema = 'evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-outer-terminal.v1'
            status = 'sealed_failure'
            decision = 'NO-GO'
            credit = 'zero_credit'
            publication = $failurePublication
            production_go = $false
        }
    }
    catch {
        $failureSealError = $_
        $failureSealPublicationStage = $terminalSealPublicationStage
        $failureSealPartial = Get-OuterPartialObservation -Kind 'failure'
        $failureSealCode = if ($failureSealError.Exception.Message -match '^[A-Za-z0-9:_-]{1,200}$') {
            $failureSealError.Exception.Message
        }
        else {
            'redacted_exception'
        }
        $emergencyPayload = [ordered]@{
            schema = 'evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-outer-emergency-seal.v1'
            authority = 'trusted_outer_internal_non_authoritative'
            status = 'manual_intervention_required'
            decision = 'NO-GO'
            credit = 'zero_credit'
            reviewer_pending = $true
            global_run_id = $ExpectedGlobalRunId
            run_uuid = $ExpectedRunUuid
            attempt_uuid = $ExpectedAttemptUuid
            commit = $ExpectedCommit
            tree = $ExpectedTree
            original_failed_stage = $outerStage
            original_error_type = $original.Exception.GetType().FullName
            original_error_code = $safeCode
            failure_seal_error_type = $failureSealError.Exception.GetType().FullName
            failure_seal_error_code = $failureSealCode
            failure_seal_failed_publication_stage = $failureSealPublicationStage
            failure_seal_partial_artifact = $failureSealPartial
            failure_seal_attempt_count = 1
            emergency_seal_attempt_count = 1
            raw_work_order_recorded = $false
            secret_recorded = $false
            nonce_recorded = $false
            command_line_recorded = $false
            preimport_directory_guards = @(Get-DirectoryGuardEvidence)
            counts = $baseCounts
            production_go = $false
        }
        try {
            $emergencyPublication = Publish-OuterTerminalSeal `
                -Kind 'emergency' `
                -Payload $emergencyPayload
            $terminalRecord = [ordered]@{
                schema = 'evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-outer-terminal.v1'
                status = 'sealed_emergency'
                decision = 'NO-GO'
                credit = 'zero_credit'
                publication = $emergencyPublication
                production_go = $false
            }
        }
        catch {
            $emergencySealPublicationStage = $terminalSealPublicationStage
            $emergencySealPartial = Get-OuterPartialObservation -Kind 'emergency'
            $terminalRecord = [ordered]@{
                schema = 'evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-outer-terminal.v1'
                status = 'unsealed_manual_intervention_required'
                decision = 'NO-GO'
                credit = 'zero_credit'
                failed_stage = $outerStage
                failure_seal_attempt_count = 1
                emergency_seal_attempt_count = 1
                failure_seal_failed_publication_stage = $failureSealPublicationStage
                emergency_seal_failed_publication_stage = $emergencySealPublicationStage
                failure_seal_partial_artifact = $failureSealPartial
                emergency_seal_partial_artifact = $emergencySealPartial
                automatic_retry_count = 0
                outer_process_launch_attempt_count = $outerProcessLaunchAttempts
                success_marker_count = 0
                completion_marker_count = 0
                production_go = $false
            }
        }
    }
}
finally {
    for ($index = $openHandles.Count - 1; $index -ge 0; $index--) {
        $openHandles[$index].Dispose()
    }
    for ($index = $directoryGuards.Count - 1; $index -ge 0; $index--) {
        Close-DirectoryGuard $directoryGuards[$index]
    }
}

if ($null -ne $terminalRecord) {
    [Console]::Error.WriteLine(($terminalRecord | ConvertTo-Json -Compress -Depth 12))
    exit 2
}
