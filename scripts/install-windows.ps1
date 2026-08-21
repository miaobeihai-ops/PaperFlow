[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$CheckOnly,
    [switch]$InstallMissing,
    [string]$VaultPath,
    [string]$DataRoot
)

$ErrorActionPreference = 'Stop'

function Test-AbsoluteWindowsPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return $Path -match '^[A-Za-z]:[\\/]'
}

function Assert-NoReparsePointInDataRootAncestors {
    param([Parameter(Mandatory = $true)][string]$Path)

    $currentPath = [System.IO.Path]::GetFullPath($Path)
    while ($null -ne $currentPath) {
        if (Test-Path -LiteralPath $currentPath) {
            $item = Get-Item -LiteralPath $currentPath -Force
            $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            if ($isReparsePoint) {
                throw "DataRoot ancestor must not be a reparse point: $currentPath"
            }
        }
        $parent = [System.IO.Directory]::GetParent($currentPath)
        if ($null -eq $parent) {
            break
        }
        $currentPath = $parent.FullName
    }
}

function Test-PathsOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second
    )

    $firstFullPath = [System.IO.Path]::GetFullPath($First).TrimEnd('\', '/')
    $secondFullPath = [System.IO.Path]::GetFullPath($Second).TrimEnd('\', '/')
    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($firstFullPath, $secondFullPath)) {
        return $true
    }
    $firstPrefix = $firstFullPath + [System.IO.Path]::DirectorySeparatorChar
    $secondPrefix = $secondFullPath + [System.IO.Path]::DirectorySeparatorChar
    return ($firstFullPath.StartsWith($secondPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $secondFullPath.StartsWith($firstPrefix, [System.StringComparison]::OrdinalIgnoreCase))
}

$DataRootSupplied = $PSBoundParameters.ContainsKey('DataRoot')
if ($DataRootSupplied) {
    if ([string]::IsNullOrWhiteSpace($DataRoot)) {
        throw 'DataRoot must be a non-empty absolute path.'
    }
    if ($DataRoot.Contains("`r") -or $DataRoot.Contains("`n")) {
        throw 'DataRoot cannot contain CR or LF characters.'
    }
    if ($DataRoot.Contains(';')) {
        throw 'DataRoot cannot contain a semicolon because it must remain one PATH entry.'
    }
    if (-not (Test-AbsoluteWindowsPath -Path $DataRoot)) {
        throw 'DataRoot must be a drive-absolute local path.'
    }
    try {
        $ResolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
    }
    catch {
        throw "DataRoot is not a valid absolute path: $($_.Exception.Message)"
    }
    Assert-NoReparsePointInDataRootAncestors -Path $ResolvedDataRoot
    if (Test-Path -LiteralPath $ResolvedDataRoot) {
        $dataRootItem = Get-Item -LiteralPath $ResolvedDataRoot -Force
        $dataRootIsReparsePoint = ($dataRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if ($dataRootIsReparsePoint) {
            throw "DataRoot must not be a reparse point: $ResolvedDataRoot"
        }
        if (-not $dataRootItem.PSIsContainer -or -not ($dataRootItem -is [System.IO.DirectoryInfo])) {
            throw "DataRoot must be a normal directory when it already exists: $ResolvedDataRoot"
        }
    }
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPaperFlowExe = Join-Path $VenvDir 'Scripts\paperflow.exe'
$VenvPaperFlowCmd = Join-Path $VenvDir 'Scripts\paperflow.cmd'
$VenvPaperFlow = $VenvPaperFlowExe
$RequirementsLock = Join-Path $ProjectRoot 'requirements.lock'
$LegacyPaperFlowHome = Join-Path $env:LOCALAPPDATA 'PaperFlow'
$LegacyBinDir = Join-Path $LegacyPaperFlowHome 'bin'
$LegacyWrapperPath = Join-Path $LegacyBinDir 'paperflow.cmd'
$LegacyConfigDir = Join-Path $env:APPDATA 'PaperFlow'
$LegacyConfigPath = Join-Path $LegacyConfigDir 'config.toml'
if ($DataRootSupplied) {
    $PaperFlowHome = $ResolvedDataRoot
    $BinDir = Join-Path $ResolvedDataRoot 'bin'
    $ConfigDir = Join-Path $ResolvedDataRoot 'config'
    $CacheDir = Join-Path $ResolvedDataRoot 'cache'
    $TempDir = Join-Path $ResolvedDataRoot 'tmp'
}
else {
    $PaperFlowHome = $LegacyPaperFlowHome
    $BinDir = $LegacyBinDir
    $ConfigDir = $LegacyConfigDir
    $CacheDir = $null
    $TempDir = $null
}
$WrapperPath = Join-Path $BinDir 'paperflow.cmd'
$ConfigPath = Join-Path $ConfigDir 'config.toml'
$SkillSource = Join-Path $ProjectRoot '.agents\skills\paperflow'
$SkillTarget = Join-Path $env:USERPROFILE '.agents\skills\paperflow'

if ($DataRootSupplied) {
    $overlapCandidates = @(
        @{ Path = $ProjectRoot; Name = 'ProjectRoot' },
        @{ Path = $SkillSource; Name = 'SkillSource' },
        @{ Path = $SkillTarget; Name = 'SkillTarget' }
    )
    foreach ($candidate in $overlapCandidates) {
        if (Test-PathsOverlap -First $ResolvedDataRoot -Second $candidate.Path) {
            throw "DataRoot must not overlap $($candidate.Name): $($candidate.Path)"
        }
    }
}

$script:PythonCommand = $null
$script:PythonPrefixArguments = @()

. (Join-Path $PSScriptRoot 'install-windows-path.ps1')

$AllowedWingetPackages = @{
    Git = 'Git.Git'
    Python = 'Python.Python.3.11'
    Zotero = 'DigitalScholar.Zotero'
    Obsidian = 'Obsidian.Obsidian'
}

function Test-CommandAvailable {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Find-Python311 {
    $candidates = @(
        @{ Command = 'py'; Prefix = @() },
        @{ Command = 'py'; Prefix = @('-3.11') },
        @{ Command = 'python'; Prefix = @() },
        @{ Command = 'python3'; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        try {
            $prefix = @($candidate.Prefix)
            $versionText = & $command.Source @prefix -c 'import platform; print(platform.python_version())' 2>$null
            $version = [version](($versionText | Select-Object -First 1).Trim())
            if ($version -ge [version]'3.11') {
                $script:PythonCommand = $command.Source
                $script:PythonPrefixArguments = $prefix
                return $version.ToString()
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function Resolve-PaperFlowConfigVaultPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($script:PythonCommand)) {
        throw 'Python 3.11 or newer is required to validate the effective PaperFlow config.'
    }
    $sourcePath = Join-Path $ProjectRoot 'src'
    $pythonCode = 'from pathlib import Path; import sys; sys.path.insert(0, sys.argv[2]); from paperflow.config import load_local_config; print(load_local_config(Path(sys.argv[1])).vault_path)'
    $originalErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:PythonCommand @script:PythonPrefixArguments -c $pythonCode $Path $sourcePath 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $originalErrorActionPreference
    }
    if ($exitCode -ne 0) {
        $detail = ($output | Out-String).Trim()
        throw "Effective PaperFlow config is invalid: $Path. $detail"
    }
    $vaultPath = [string]($output | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($vaultPath)) {
        throw "Effective PaperFlow config is invalid: $Path. vault_path was not resolved."
    }
    try {
        return [System.IO.Path]::GetFullPath($vaultPath)
    }
    catch {
        throw "Effective PaperFlow config is invalid: $Path. vault_path is not valid: $($_.Exception.Message)"
    }
}

function Test-DesktopApplication {
    param(
        [string]$CommandName,
        [string[]]$CandidatePaths
    )
    if (Test-CommandAvailable $CommandName) {
        return $true
    }
    foreach ($candidatePath in $CandidatePaths) {
        if ($candidatePath -and (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
            return $true
        }
    }
    return $false
}

function Get-InstallationState {
    $pythonVersion = Find-Python311
    $gitOk = Test-CommandAvailable 'git'
    $codexOk = (Test-CommandAvailable 'codex.cmd') -or (Test-CommandAvailable 'codex')
    $zoteroPaths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Zotero\zotero.exe'),
        (Join-Path $env:ProgramFiles 'Zotero\zotero.exe')
    )
    $obsidianPaths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Obsidian\Obsidian.exe'),
        (Join-Path $env:ProgramFiles 'Obsidian\Obsidian.exe')
    )
    if (${env:ProgramFiles(x86)}) {
        $zoteroPaths += Join-Path ${env:ProgramFiles(x86)} 'Zotero\zotero.exe'
        $obsidianPaths += Join-Path ${env:ProgramFiles(x86)} 'Obsidian\Obsidian.exe'
    }
    $zoteroOk = Test-DesktopApplication -CommandName 'zotero' -CandidatePaths $zoteroPaths
    $obsidianOk = Test-DesktopApplication -CommandName 'obsidian' -CandidatePaths $obsidianPaths
    $vaultOk = $false
    $vaultStatus = 'not provided; config will not be written'
    if ($VaultPath) {
        $vaultOk = Test-Path -LiteralPath $VaultPath -PathType Container
        $vaultStatus = if ($vaultOk) { 'existing directory' } else { 'path is not an existing directory' }
    }

    return [pscustomobject]@{
        Git = $gitOk
        Python = $null -ne $pythonVersion
        PythonVersion = $pythonVersion
        Codex = $codexOk
        Zotero = $zoteroOk
        Obsidian = $obsidianOk
        Vault = $vaultOk
        VaultStatus = $vaultStatus
    }
}

function Show-InstallationState {
    param($State)
    $rows = @(
        [pscustomobject]@{ Component = 'Git'; Status = $(if ($State.Git) { 'OK' } else { 'MISSING' }); Detail = 'required' },
        [pscustomobject]@{ Component = 'Python'; Status = $(if ($State.Python) { 'OK' } else { 'MISSING' }); Detail = $(if ($State.PythonVersion) { "version $($State.PythonVersion)" } else { '3.11+ required' }) },
        [pscustomobject]@{ Component = 'Codex'; Status = $(if ($State.Codex) { 'OK' } else { 'MISSING' }); Detail = 'required; install manually from official instructions' },
        [pscustomobject]@{ Component = 'Zotero'; Status = $(if ($State.Zotero) { 'OK' } else { 'MISSING' }); Detail = 'desktop app' },
        [pscustomobject]@{ Component = 'Obsidian'; Status = $(if ($State.Obsidian) { 'OK' } else { 'MISSING' }); Detail = 'desktop app' },
        [pscustomobject]@{ Component = 'Vault'; Status = $(if ($State.Vault) { 'OK' } else { 'OPTIONAL' }); Detail = $State.VaultStatus },
        [pscustomobject]@{ Component = 'Sidebar'; Status = 'MANUAL'; Detail = 'verify Zotero AI Sidebar separately if desired' }
    )
    $rows | Format-Table -AutoSize | Out-Host
}

function Install-WingetPackage {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Git.Git', 'Python.Python.3.11', 'DigitalScholar.Zotero', 'Obsidian.Obsidian')]
        [string]$PackageId
    )

    if ($PSCmdlet.ShouldProcess($PackageId, 'Install exact package with winget')) {
        winget install --id $PackageId --exact
        if ($LASTEXITCODE -ne 0) {
            throw "winget failed for allowlisted package $PackageId"
        }
    }
}

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = Get-PaperFlowUserPath
    $env:Path = @($env:Path, $machinePath, $userPath) -join ';'
}

function Set-EffectiveConfigPreviewState {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)]
        [ValidateSet('destination', 'legacy', 'explicit', 'none')]
        [string]$Source
    )

    $State.Vault = $Source -ne 'none'
    $State.VaultStatus = switch ($Source) {
        'destination' { 'destination config will be preserved' }
        'legacy' { 'legacy config will be migrated' }
        'explicit' { 'explicit Vault will generate config' }
        default { 'no effective config; config will not be written' }
    }
}

function Get-PaperFlowUserPath {
    return [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Set-PaperFlowUserPath {
    param([Parameter(Mandatory = $true)][AllowNull()][AllowEmptyString()]$Value)

    [Environment]::SetEnvironmentVariable('Path', $Value, 'User')
}

function Assert-PersistedPaperFlowUserPath {
    param([Parameter(Mandatory = $true)][AllowNull()][AllowEmptyString()]$ExpectedValue)

    $persistedValue = Get-PaperFlowUserPath
    if (-not [System.StringComparer]::Ordinal.Equals($persistedValue, $ExpectedValue)) {
        throw 'User PATH did not persist the intended PaperFlow migration; exact legacy files were preserved.'
    }
}

function Set-PaperFlowUserPathTransaction {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$IntendedValue,
        [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyString()]$OriginalValue
    )

    try {
        Set-PaperFlowUserPath -Value $IntendedValue
        Assert-PersistedPaperFlowUserPath -ExpectedValue $IntendedValue
    }
    catch {
        $migrationError = $_.Exception.Message
        try {
            Set-PaperFlowUserPath -Value $OriginalValue
            Assert-PersistedPaperFlowUserPath -ExpectedValue $OriginalValue
        }
        catch {
            throw "User PATH migration failed and rollback verification failed; manual PATH repair is required. Migration error: $migrationError Rollback error: $($_.Exception.Message)"
        }
        throw "User PATH migration failed; original user PATH was restored and verified. Migration error: $migrationError"
    }
}

function Invoke-SelectedPython {
    param([string[]]$Arguments)
    & $script:PythonCommand @script:PythonPrefixArguments @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Python command failed.'
    }
}

function ConvertTo-TomlBasicString {
    param([string]$Value)
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'VaultPath cannot contain a newline.'
    }
    return $Value.Replace('\', '\\').Replace('"', '\"')
}

function Assert-SafeSkillTarget {
    if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        throw 'USERPROFILE is required to install the PaperFlow Skill.'
    }
    $expectedTarget = [System.IO.Path]::GetFullPath(
        (Join-Path $env:USERPROFILE '.agents\skills\paperflow')
    )
    $actualTarget = [System.IO.Path]::GetFullPath($SkillTarget)
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualTarget, $expectedTarget)) {
        throw 'Refusing to replace an unexpected Skill target.'
    }
}

function Assert-ValidSkillDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $manifestPath = Join-Path $Path 'SKILL.md'
    if (-not (Test-Path -LiteralPath $Path -PathType Container) -or
        -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "PaperFlow Skill source must be a directory containing a valid non-empty SKILL.md: $Path"
    }
    $manifestContent = [System.IO.File]::ReadAllText($manifestPath)
    if ([string]::IsNullOrWhiteSpace($manifestContent)) {
        throw "PaperFlow Skill source must be a directory containing a valid non-empty SKILL.md: $Path"
    }
}

function Assert-SafeSkillSiblingPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet('staging', 'backup')][string]$Kind
    )

    Assert-SafeSkillTarget
    $expectedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $SkillTarget))
    $actualPath = [System.IO.Path]::GetFullPath($Path)
    $actualParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $actualPath))
    $expectedNamePattern = "^\.paperflow-$Kind-[0-9a-f]{32}$"
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualParent, $expectedParent) -or
        ([System.IO.Path]::GetFileName($actualPath) -notmatch $expectedNamePattern)) {
        throw "Refusing to use an unexpected PaperFlow Skill $Kind path."
    }
}

function Assert-RegularFileOrMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if ($item.PSIsContainer -or -not ($item -is [System.IO.FileInfo]) -or $isReparsePoint) {
            throw "$Name must be a regular file or not exist: $Path"
        }
    }

    $existingParent = Split-Path -Parent $Path
    while (-not [string]::IsNullOrWhiteSpace($existingParent) -and
        -not (Test-Path -LiteralPath $existingParent)) {
        $nextParent = Split-Path -Parent $existingParent
        if ($nextParent -eq $existingParent) {
            break
        }
        $existingParent = $nextParent
    }
    if (-not [string]::IsNullOrWhiteSpace($existingParent) -and
        (Test-Path -LiteralPath $existingParent) -and
        -not (Test-Path -LiteralPath $existingParent -PathType Container)) {
        throw "$Name parent must be a directory; destination must be a regular file or not exist: $Path"
    }
}

function Assert-NormalDirectoryOrMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if (-not $item.PSIsContainer -or -not ($item -is [System.IO.DirectoryInfo]) -or $isReparsePoint) {
            throw "$Name must be a normal directory or not exist: $Path"
        }
    }
}

function Test-RegularNonReparseFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    return (-not $item.PSIsContainer -and ($item -is [System.IO.FileInfo]) -and -not $isReparsePoint)
}

function Copy-FileBytesAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $sourceFullPath = [System.IO.Path]::GetFullPath($Source)
    $destinationFullPath = [System.IO.Path]::GetFullPath($Destination)
    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($sourceFullPath, $destinationFullPath)) {
        throw 'Refusing to migrate a PaperFlow config onto itself.'
    }
    $destinationDirectory = Split-Path -Parent $Destination
    $configTempPath = Join-Path $destinationDirectory ('.paperflow-config-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllBytes($configTempPath, [System.IO.File]::ReadAllBytes($Source))
        [System.IO.File]::Move($configTempPath, $Destination)
    }
    finally {
        if (Test-Path -LiteralPath $configTempPath -PathType Leaf) {
            Remove-Item -LiteralPath $configTempPath -Force
        }
    }
}

function Write-FileBytesAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )

    $destinationDirectory = Split-Path -Parent $Destination
    [System.IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null
    $tempPath = Join-Path $destinationDirectory ('.paperflow-restore-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllBytes($tempPath, $Bytes)
        Move-Item -LiteralPath $tempPath -Destination $Destination -Force
    }
    finally {
        if (Test-Path -LiteralPath $tempPath -PathType Leaf) {
            Remove-Item -LiteralPath $tempPath -Force
        }
    }
}

function ConvertTo-CmdEmbeddedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return $Path.Replace('%', '%%')
}

function Remove-EmptyNormalDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return
    }
    $item = Get-Item -LiteralPath $Path -Force
    $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    if ($isReparsePoint -or @((Get-ChildItem -LiteralPath $Path -Force)).Count -ne 0) {
        return
    }
    try {
        Remove-Item -LiteralPath $Path -Force
    }
    catch {
        Write-Warning "Could not remove empty legacy PaperFlow directory '$Path': $($_.Exception.Message)"
    }
}

function Remove-LegacyPaperFlowFiles {
    param([Parameter(Mandatory = $true)][bool]$RemoveLegacyConfig)

    $candidates = @(
        @{ Path = $LegacyWrapperPath; NewPath = $WrapperPath }
    )
    if ($RemoveLegacyConfig) {
        $candidates += @{ Path = $LegacyConfigPath; NewPath = $ConfigPath }
    }
    $snapshots = @()
    foreach ($candidate in $candidates) {
        $legacyFullPath = [System.IO.Path]::GetFullPath($candidate.Path)
        $newFullPath = [System.IO.Path]::GetFullPath($candidate.NewPath)
        if ([System.StringComparer]::OrdinalIgnoreCase.Equals($legacyFullPath, $newFullPath) -or
            -not (Test-RegularNonReparseFile -Path $candidate.Path)) {
            continue
        }
        $backupPath = Join-Path (Split-Path -Parent $candidate.Path) ('.paperflow-legacy-' + [guid]::NewGuid().ToString('N') + '.bak')
        $snapshots += [pscustomobject]@{
            Path = $candidate.Path
            BackupPath = $backupPath
            Bytes = [System.IO.File]::ReadAllBytes($candidate.Path)
        }
    }

    try {
        foreach ($snapshot in $snapshots) {
            Move-Item -LiteralPath $snapshot.Path -Destination $snapshot.BackupPath
        }
        foreach ($snapshot in $snapshots) {
            Remove-Item -LiteralPath $snapshot.BackupPath -Force
        }
    }
    catch {
        $cleanupError = $_
        foreach ($snapshot in $snapshots) {
            try {
                if (Test-Path -LiteralPath $snapshot.BackupPath -PathType Leaf) {
                    if (-not (Test-Path -LiteralPath $snapshot.Path)) {
                        Move-Item -LiteralPath $snapshot.BackupPath -Destination $snapshot.Path
                    }
                    else {
                        Remove-Item -LiteralPath $snapshot.BackupPath -Force
                    }
                }
                elseif (-not (Test-Path -LiteralPath $snapshot.Path)) {
                    Write-FileBytesAtomically -Destination $snapshot.Path -Bytes $snapshot.Bytes
                }
            }
            catch {
                Write-Warning "Could not restore exact legacy file '$($snapshot.Path)': $($_.Exception.Message)"
            }
        }
        throw $cleanupError
    }

    foreach ($directory in @(
        $LegacyBinDir,
        $LegacyPaperFlowHome,
        $LegacyConfigDir
    )) {
        Remove-EmptyNormalDirectory -Path $directory
    }
}

function Assert-InstallDestinationPreflight {
    Assert-RegularFileOrMissing -Path $ConfigPath -Name 'PaperFlow config'
    Assert-RegularFileOrMissing -Path $WrapperPath -Name 'PaperFlow wrapper'
    if ($DataRootSupplied) {
        foreach ($directory in @(
            @{ Path = $BinDir; Name = 'PaperFlow bin directory' },
            @{ Path = $ConfigDir; Name = 'PaperFlow config directory' },
            @{ Path = $CacheDir; Name = 'PaperFlow cache directory' },
            @{ Path = $TempDir; Name = 'PaperFlow temp directory' }
        )) {
            Assert-NormalDirectoryOrMissing -Path $directory.Path -Name $directory.Name
        }
    }
}

function Assert-LegacyPaperFlowDirectorySafety {
    if (-not $DataRootSupplied) {
        return
    }

    foreach ($directory in @($LegacyPaperFlowHome, $LegacyBinDir, $LegacyConfigDir)) {
        if (-not (Test-Path -LiteralPath $directory)) {
            continue
        }
        $item = Get-Item -LiteralPath $directory -Force
        $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if (-not $item.PSIsContainer -or -not ($item -is [System.IO.DirectoryInfo]) -or $isReparsePoint) {
            throw "Legacy PaperFlow directory must be a normal non-reparse directory: $directory"
        }
    }
}

function Install-PaperFlowSkill {
    Assert-SafeSkillTarget
    Assert-ValidSkillDirectory -Path $SkillSource
    $skillParent = Split-Path -Parent $SkillTarget
    $stagingPath = Join-Path $skillParent ('.paperflow-staging-' + [guid]::NewGuid().ToString('N'))
    $backupPath = Join-Path $skillParent ('.paperflow-backup-' + [guid]::NewGuid().ToString('N'))
    Assert-SafeSkillSiblingPath -Path $stagingPath -Kind 'staging'
    Assert-SafeSkillSiblingPath -Path $backupPath -Kind 'backup'
    $backupCreated = $false
    $skillCommitted = $false

    try {
        [System.IO.Directory]::CreateDirectory($skillParent) | Out-Null
        Copy-Item -LiteralPath $SkillSource -Destination $stagingPath -Recurse -Force
        Assert-ValidSkillDirectory -Path $stagingPath
        if (Test-Path -LiteralPath $SkillTarget) {
            Move-Item -LiteralPath $SkillTarget -Destination $backupPath
            $backupCreated = $true
        }
        Move-Item -LiteralPath $stagingPath -Destination $SkillTarget
        $skillCommitted = $true
    }
    catch {
        $replacementError = $_
        if ($backupCreated) {
            try {
                if (-not (Test-Path -LiteralPath $SkillTarget)) {
                    Move-Item -LiteralPath $backupPath -Destination $SkillTarget
                    $backupCreated = $false
                }
            }
            catch {
                Write-Warning "Could not restore the previous PaperFlow Skill from its validated backup: $($_.Exception.Message)"
            }
        }
        if (Test-Path -LiteralPath $stagingPath) {
            Assert-SafeSkillSiblingPath -Path $stagingPath -Kind 'staging'
            Remove-Item -LiteralPath $stagingPath -Recurse -Force
        }
        throw $replacementError
    }

    if ($skillCommitted -and $backupCreated) {
        try {
            Assert-SafeSkillSiblingPath -Path $backupPath -Kind 'backup'
            Remove-Item -LiteralPath $backupPath -Recurse -Force
        }
        catch {
            Write-Warning "Could not remove the previous PaperFlow Skill backup; the committed new Skill remains active: $($_.Exception.Message)"
        }
    }
}

$state = Get-InstallationState

if ($DataRootSupplied) {
    $effectiveConfigPath = $null
    $effectiveConfigSource = 'none'
    Assert-RegularFileOrMissing -Path $ConfigPath -Name 'PaperFlow config'
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        $effectiveConfigPath = $ConfigPath
        $effectiveConfigSource = 'destination'
    }
    else {
        Assert-LegacyPaperFlowDirectorySafety
        if (Test-RegularNonReparseFile -Path $LegacyConfigPath) {
            $effectiveConfigPath = $LegacyConfigPath
            $effectiveConfigSource = 'legacy'
        }
    }

    $effectiveVaultPath = $null
    if ($null -ne $effectiveConfigPath) {
        $effectiveVaultPath = Resolve-PaperFlowConfigVaultPath -Path $effectiveConfigPath
    }
    elseif ($VaultPath) {
        if (-not $state.Vault) {
            throw 'VaultPath must be an existing directory.'
        }
        $effectiveVaultPath = (Resolve-Path -LiteralPath $VaultPath).Path
        $effectiveConfigSource = 'explicit'
    }

    if ($null -ne $effectiveVaultPath -and
        (Test-PathsOverlap -First $ResolvedDataRoot -Second $effectiveVaultPath)) {
        throw "DataRoot must not overlap effective Vault: $effectiveVaultPath"
    }
    Set-EffectiveConfigPreviewState -State $state -Source $effectiveConfigSource
}

Write-Host 'PaperFlow installation preview'
if ($DataRootSupplied) {
    Write-Host "DataRoot: $ResolvedDataRoot"
}
Show-InstallationState -State $state

if ($CheckOnly) {
    Write-Host 'CheckOnly: no files, packages, configuration, Skill, wrapper, or PATH were changed.'
    exit 0
}

if (-not $DataRootSupplied -and $VaultPath -and -not $state.Vault) {
    throw 'VaultPath must be an existing directory.'
}

Assert-ValidSkillDirectory -Path $SkillSource
Assert-SafeSkillTarget
Assert-InstallDestinationPreflight
Assert-LegacyPaperFlowDirectorySafety

if ($InstallMissing) {
    if (-not (Test-CommandAvailable 'winget')) {
        throw 'winget is required for -InstallMissing.'
    }
    foreach ($component in @('Git', 'Python', 'Zotero', 'Obsidian')) {
        if (-not $state.$component) {
            Install-WingetPackage -PackageId $AllowedWingetPackages[$component]
        }
    }
    Refresh-ProcessPath
    $state = Get-InstallationState
    if ($DataRootSupplied) {
        Set-EffectiveConfigPreviewState -State $state -Source $effectiveConfigSource
    }
    Write-Host 'Checks after requested installations'
    Show-InstallationState -State $state
}

if (-not $state.Codex) {
    Write-Warning 'Codex is missing. Install it using the official OpenAI Codex instructions; this script never installs Codex.'
}

$missingRequired = @()
foreach ($component in @('Git', 'Python')) {
    if (-not $state.$component) {
        $missingRequired += $component
    }
}
if ($missingRequired.Count -gt 0) {
    throw "Missing required prerequisites: $($missingRequired -join ', '). Re-run with -InstallMissing or install them manually."
}

if ($DataRootSupplied -and $PSCmdlet.ShouldProcess($ResolvedDataRoot, 'Create PaperFlow data directories')) {
    foreach ($directory in @($ResolvedDataRoot, $BinDir, $ConfigDir, $CacheDir, $TempDir)) {
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    if ($PSCmdlet.ShouldProcess($VenvDir, 'Create PaperFlow virtual environment')) {
        Invoke-SelectedPython -Arguments @('-m', 'venv', $VenvDir)
    }
}

if ((Test-Path -LiteralPath $VenvPython -PathType Leaf) -and $PSCmdlet.ShouldProcess($ProjectRoot, 'Install PaperFlow into the virtual environment')) {
    $tempWasPresent = Test-Path Env:TEMP
    $tmpWasPresent = Test-Path Env:TMP
    $pipNoCacheWasPresent = Test-Path Env:PIP_NO_CACHE_DIR
    $originalTemp = $env:TEMP
    $originalTmp = $env:TMP
    $originalPipNoCache = $env:PIP_NO_CACHE_DIR
    try {
        if ($DataRootSupplied) {
            $env:TEMP = $TempDir
            $env:TMP = $TempDir
            $env:PIP_NO_CACHE_DIR = '1'
        }
        & $VenvPython -m pip install --requirement $RequirementsLock
        if ($LASTEXITCODE -ne 0) {
            throw 'Locked runtime dependency installation failed.'
        }
        & $VenvPython -m pip install --no-deps --no-build-isolation $ProjectRoot
        if ($LASTEXITCODE -ne 0) {
            throw 'PaperFlow package installation failed.'
        }
    }
    finally {
        if ($tempWasPresent) { $env:TEMP = $originalTemp } else { [Environment]::SetEnvironmentVariable('TEMP', $null, 'Process') }
        if ($tmpWasPresent) { $env:TMP = $originalTmp } else { [Environment]::SetEnvironmentVariable('TMP', $null, 'Process') }
        if ($pipNoCacheWasPresent) { $env:PIP_NO_CACHE_DIR = $originalPipNoCache } else { [Environment]::SetEnvironmentVariable('PIP_NO_CACHE_DIR', $null, 'Process') }
    }
}

if (Test-Path -LiteralPath $VenvPaperFlowExe -PathType Leaf) {
    $VenvPaperFlow = $VenvPaperFlowExe
}
elseif (Test-Path -LiteralPath $VenvPaperFlowCmd -PathType Leaf) {
    $VenvPaperFlow = $VenvPaperFlowCmd
}

if ($PSCmdlet.ShouldProcess($SkillTarget, 'Copy PaperFlow Skill for the current user')) {
    Install-PaperFlowSkill
}

$legacyConfigMigratedThisRun = $false
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    Write-Host "Local config preserved: $ConfigPath"
}
elseif ($DataRootSupplied -and (Test-RegularNonReparseFile -Path $LegacyConfigPath)) {
    if ($PSCmdlet.ShouldProcess($ConfigPath, "Migrate exact legacy PaperFlow config from $LegacyConfigPath")) {
        [System.IO.Directory]::CreateDirectory($ConfigDir) | Out-Null
        Copy-FileBytesAtomically -Source $LegacyConfigPath -Destination $ConfigPath
        $legacyConfigMigratedThisRun = $true
        Write-Host "Legacy config migrated byte-for-byte: $ConfigPath"
    }
}
elseif ($VaultPath) {
        $resolvedVault = (Resolve-Path -LiteralPath $VaultPath).Path
        $escapedVault = ConvertTo-TomlBasicString -Value $resolvedVault
        $config = @"
vault_path = "$escapedVault"
top_n = 10
timezone = "Asia/Hong_Kong"
history_reports = 30
arxiv_categories = ["cs.RO", "cs.CV", "cs.AI", "cs.LG"]

[keywords]
robotics = 5
"3d reconstruction" = 8
"@
        if ($PSCmdlet.ShouldProcess($ConfigPath, 'Create local PaperFlow configuration')) {
            [System.IO.Directory]::CreateDirectory($ConfigDir) | Out-Null
            $configTempPath = Join-Path $ConfigDir ('.paperflow-config-' + [guid]::NewGuid().ToString('N') + '.tmp')
            try {
                [System.IO.File]::WriteAllText($configTempPath, $config, (New-Object System.Text.UTF8Encoding($false)))
                Move-Item -LiteralPath $configTempPath -Destination $ConfigPath -Force
            }
            finally {
                if (Test-Path -LiteralPath $configTempPath -PathType Leaf) {
                    Remove-Item -LiteralPath $configTempPath -Force
                }
            }
        }
}
else {
    Write-Warning 'No VaultPath was provided; local config.toml was not written.'
}

if ($PSCmdlet.ShouldProcess($WrapperPath, 'Create or update PaperFlow command wrapper')) {
    [System.IO.Directory]::CreateDirectory($BinDir) | Out-Null
    $wrapperCommand = ConvertTo-CmdEmbeddedPath -Path $VenvPaperFlow
    if ($DataRootSupplied) {
        $wrapperHome = ConvertTo-CmdEmbeddedPath -Path $ResolvedDataRoot
        $wrapperCache = ConvertTo-CmdEmbeddedPath -Path $CacheDir
        $wrapperTemp = ConvertTo-CmdEmbeddedPath -Path $TempDir
        $wrapper = @"
@echo off
setlocal DisableDelayedExpansion
set "PAPERFLOW_HOME=$wrapperHome"
set "PAPERFLOW_CACHE_DIR=$wrapperCache"
set "TMP=$wrapperTemp"
set "TEMP=$wrapperTemp"
"$wrapperCommand" %*
set "PAPERFLOW_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %PAPERFLOW_EXIT_CODE%
"@
        $wrapper = ($wrapper -replace "`r?`n", "`r`n") + "`r`n"
    }
    else {
        $wrapper = "@echo off`r`nsetlocal DisableDelayedExpansion`r`n`"$wrapperCommand`" %*`r`nset `"PAPERFLOW_EXIT_CODE=%ERRORLEVEL%`"`r`nendlocal & exit /b %PAPERFLOW_EXIT_CODE%`r`n"
    }
    $wrapperTempPath = Join-Path $BinDir ('.paperflow-wrapper-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllText($wrapperTempPath, $wrapper, (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $wrapperTempPath -Destination $WrapperPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $wrapperTempPath -PathType Leaf) {
            Remove-Item -LiteralPath $wrapperTempPath -Force
        }
    }
}

if (-not $WhatIfPreference -and
    (Test-Path -LiteralPath $WrapperPath -PathType Leaf)) {
    & $WrapperPath --json doctor
    $doctorExitCode = $LASTEXITCODE
    if ($doctorExitCode -ne 0) {
        if ($DataRootSupplied) {
            throw "PaperFlow doctor exited with code $doctorExitCode. Exact legacy wrapper and config were preserved."
        }
        else {
            Write-Warning "PaperFlow doctor exited with code $doctorExitCode. Review the doctor JSON output and resolve any required checks; optional Zotero, Obsidian, or missing Vault checks do not roll back the completed installation."
        }
    }
}
elseif ($DataRootSupplied -and -not $WhatIfPreference) {
    throw 'PaperFlow doctor could not run because the new wrapper is missing. Exact legacy files were preserved.'
}

$userPath = Get-PaperFlowUserPath
$pathMigrationCommitted = $false
if ($DataRootSupplied) {
    $pathUpdate = Set-PaperFlowPathEntry -CurrentPath ([string]$userPath) -BinDir $BinDir -LegacyBinDir $LegacyBinDir
    if ($pathUpdate.Changed) {
        if ($WhatIfPreference) {
            $null = $PSCmdlet.ShouldProcess('User PATH', "Migrate PaperFlow bin to $BinDir")
        }
        else {
            $answer = Read-Host "Replace the exact legacy PaperFlow bin with '$BinDir' in your user PATH? [y/N]"
            if ($answer -match '^(?i:y|yes)$') {
                if ($PSCmdlet.ShouldProcess('User PATH', "Migrate PaperFlow bin to $BinDir")) {
                    Set-PaperFlowUserPathTransaction -IntendedValue $pathUpdate.Value -OriginalValue $userPath
                    $pathMigrationCommitted = $true
                    Write-Host 'User PATH migrated. Open a new terminal before running paperflow.'
                }
            }
            else {
                Write-Host "PATH unchanged. Exact legacy wrapper and config were preserved; run '$WrapperPath' directly or migrate PATH later."
            }
        }
    }
    else {
        Assert-PersistedPaperFlowUserPath -ExpectedValue $pathUpdate.Value
        $pathMigrationCommitted = $true
        Write-Host 'PaperFlow bin directory is already correctly present in the user PATH.'
    }
}
else {
    $pathUpdate = Add-PaperFlowPathEntry -CurrentPath ([string]$userPath) -BinDir $BinDir
    if ($pathUpdate.Changed) {
        if ($WhatIfPreference) {
            $null = $PSCmdlet.ShouldProcess('User PATH', "Add $BinDir")
        }
        else {
            $answer = Read-Host "Add '$BinDir' to your user PATH? [y/N]"
            if ($answer -match '^(?i:y|yes)$') {
                if ($PSCmdlet.ShouldProcess('User PATH', "Add $BinDir")) {
                    Set-PaperFlowUserPathTransaction -IntendedValue $pathUpdate.Value -OriginalValue $userPath
                    Write-Host 'User PATH updated. Open a new terminal before running paperflow.'
                }
            }
            else {
                Write-Host "PATH unchanged. Run '$WrapperPath' directly or add the bin directory later."
            }
        }
    }
    else {
        Write-Host 'PaperFlow bin directory is already present in the user PATH.'
    }
}

if ($DataRootSupplied -and $pathMigrationCommitted -and
    $PSCmdlet.ShouldProcess('Exact legacy PaperFlow wrapper and config files', 'Remove after doctor and PATH migration succeeded')) {
    if (-not $legacyConfigMigratedThisRun -and
        (Test-RegularNonReparseFile -Path $ConfigPath) -and
        (Test-RegularNonReparseFile -Path $LegacyConfigPath)) {
        Write-Warning "New and legacy PaperFlow config.toml files were both preserved; manual reconciliation is required: '$ConfigPath' and '$LegacyConfigPath'."
    }
    Remove-LegacyPaperFlowFiles -RemoveLegacyConfig $legacyConfigMigratedThisRun
}

Write-Host 'PaperFlow installation steps completed.'
