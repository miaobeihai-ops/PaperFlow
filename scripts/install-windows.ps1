[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$CheckOnly,
    [switch]$InstallMissing,
    [string]$VaultPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPaperFlowExe = Join-Path $VenvDir 'Scripts\paperflow.exe'
$VenvPaperFlowCmd = Join-Path $VenvDir 'Scripts\paperflow.cmd'
$VenvPaperFlow = $VenvPaperFlowExe
$VenvPaperFlowDoctor = $VenvPaperFlowExe
$RequirementsLock = Join-Path $ProjectRoot 'requirements.lock'
$PaperFlowHome = Join-Path $env:LOCALAPPDATA 'PaperFlow'
$BinDir = Join-Path $PaperFlowHome 'bin'
$WrapperPath = Join-Path $BinDir 'paperflow.cmd'
$ConfigDir = Join-Path $env:APPDATA 'PaperFlow'
$ConfigPath = Join-Path $ConfigDir 'config.toml'
$SkillSource = Join-Path $ProjectRoot '.agents\skills\paperflow'
$SkillTarget = Join-Path $env:USERPROFILE '.agents\skills\paperflow'
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
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = @($env:Path, $machinePath, $userPath) -join ';'
}

function Get-PaperFlowUserPath {
    return [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Set-PaperFlowUserPath {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    [Environment]::SetEnvironmentVariable('Path', $Value, 'User')
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

function Assert-InstallDestinationPreflight {
    Assert-RegularFileOrMissing -Path $ConfigPath -Name 'PaperFlow config'
    Assert-RegularFileOrMissing -Path $WrapperPath -Name 'PaperFlow wrapper'
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
Write-Host 'PaperFlow installation preview'
Show-InstallationState -State $state

if ($CheckOnly) {
    Write-Host 'CheckOnly: no files, packages, configuration, Skill, wrapper, or PATH were changed.'
    exit 0
}

if ($VaultPath -and -not $state.Vault) {
    throw 'VaultPath must be an existing directory.'
}

Assert-ValidSkillDirectory -Path $SkillSource
Assert-SafeSkillTarget
Assert-InstallDestinationPreflight

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

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    if ($PSCmdlet.ShouldProcess($VenvDir, 'Create PaperFlow virtual environment')) {
        Invoke-SelectedPython -Arguments @('-m', 'venv', $VenvDir)
    }
}

if ((Test-Path -LiteralPath $VenvPython -PathType Leaf) -and $PSCmdlet.ShouldProcess($ProjectRoot, 'Install PaperFlow into the virtual environment')) {
    & $VenvPython -m pip install --requirement $RequirementsLock
    if ($LASTEXITCODE -ne 0) {
        throw 'Locked runtime dependency installation failed.'
    }
    & $VenvPython -m pip install --no-deps --no-build-isolation $ProjectRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'PaperFlow package installation failed.'
    }
}

if (Test-Path -LiteralPath $VenvPaperFlowCmd -PathType Leaf) {
    $VenvPaperFlowDoctor = $VenvPaperFlowCmd
}
else {
    $VenvPaperFlowDoctor = $VenvPaperFlowExe
}

if ($PSCmdlet.ShouldProcess($SkillTarget, 'Copy PaperFlow Skill for the current user')) {
    Install-PaperFlowSkill
}

if ($VaultPath) {
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        Write-Host "Local config preserved: $ConfigPath"
    }
    else {
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
}
else {
    Write-Warning 'No VaultPath was provided; local config.toml was not written.'
}

if ($PSCmdlet.ShouldProcess($WrapperPath, 'Create or update PaperFlow command wrapper')) {
    [System.IO.Directory]::CreateDirectory($BinDir) | Out-Null
    $wrapper = "@echo off`r`n`"$VenvPaperFlow`" %*`r`n"
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

$userPath = Get-PaperFlowUserPath
$pathUpdate = Add-PaperFlowPathEntry -CurrentPath ([string]$userPath) -BinDir $BinDir
if ($pathUpdate.Changed) {
    $answer = Read-Host "Add '$BinDir' to your user PATH? [y/N]"
    if ($answer -match '^(?i:y|yes)$') {
        if ($PSCmdlet.ShouldProcess('User PATH', "Add $BinDir")) {
            Set-PaperFlowUserPath -Value $pathUpdate.Value
            Write-Host 'User PATH updated. Open a new terminal before running paperflow.'
        }
    }
    else {
        Write-Host "PATH unchanged. Run '$WrapperPath' directly or add the bin directory later."
    }
}
else {
    Write-Host 'PaperFlow bin directory is already present in the user PATH.'
}

if (-not $WhatIfPreference -and
    (Test-Path -LiteralPath $WrapperPath -PathType Leaf) -and
    (Test-Path -LiteralPath $VenvPaperFlowDoctor -PathType Leaf)) {
    & $VenvPaperFlowDoctor --json doctor
    $doctorExitCode = $LASTEXITCODE
    if ($doctorExitCode -ne 0) {
        Write-Warning "PaperFlow doctor exited with code $doctorExitCode. Review the doctor JSON output and resolve any required checks; optional Zotero, Obsidian, or missing Vault checks do not roll back the completed installation."
    }
}

Write-Host 'PaperFlow installation steps completed.'
