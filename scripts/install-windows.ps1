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
$VenvPaperFlow = Join-Path $VenvDir 'Scripts\paperflow.exe'
$PaperFlowHome = Join-Path $env:LOCALAPPDATA 'PaperFlow'
$BinDir = Join-Path $PaperFlowHome 'bin'
$WrapperPath = Join-Path $BinDir 'paperflow.cmd'
$ConfigDir = Join-Path $env:APPDATA 'PaperFlow'
$ConfigPath = Join-Path $ConfigDir 'config.toml'
$SkillSource = Join-Path $ProjectRoot '.agents\skills\paperflow'
$SkillTarget = Join-Path $env:USERPROFILE '.agents\skills\paperflow'
$script:PythonCommand = $null
$script:PythonPrefixArguments = @()

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
    & $VenvPython -m pip install $ProjectRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'PaperFlow package installation failed.'
    }
}

if ($PSCmdlet.ShouldProcess($WrapperPath, 'Create or update PaperFlow command wrapper')) {
    [System.IO.Directory]::CreateDirectory($BinDir) | Out-Null
    $wrapper = "@echo off`r`n`"$VenvPaperFlow`" %*`r`n"
    [System.IO.File]::WriteAllText($WrapperPath, $wrapper, (New-Object System.Text.UTF8Encoding($false)))
}

if (-not (Test-Path -LiteralPath $SkillSource -PathType Container)) {
    throw "PaperFlow Skill source was not found: $SkillSource"
}
if ($PSCmdlet.ShouldProcess($SkillTarget, 'Copy PaperFlow Skill for the current user')) {
    [System.IO.Directory]::CreateDirectory($SkillTarget) | Out-Null
    Copy-Item -Path (Join-Path $SkillSource '*') -Destination $SkillTarget -Recurse -Force
}

if ($VaultPath) {
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
    if ($PSCmdlet.ShouldProcess($ConfigPath, 'Create or update local PaperFlow configuration')) {
        [System.IO.Directory]::CreateDirectory($ConfigDir) | Out-Null
        [System.IO.File]::WriteAllText($ConfigPath, $config, (New-Object System.Text.UTF8Encoding($false)))
    }
}
else {
    Write-Warning 'No VaultPath was provided; local config.toml was not written.'
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathEntries = @($userPath -split ';' | Where-Object { $_ })
$binAlreadyPresent = $false
foreach ($entry in $pathEntries) {
    if ($entry.TrimEnd('\') -ieq $BinDir.TrimEnd('\')) {
        $binAlreadyPresent = $true
        break
    }
}
if (-not $binAlreadyPresent) {
    $answer = Read-Host "Add '$BinDir' to your user PATH? [y/N]"
    if ($answer -match '^(?i:y|yes)$') {
        if ($PSCmdlet.ShouldProcess('User PATH', "Add $BinDir")) {
            $newUserPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $BinDir } else { "$userPath;$BinDir" }
            [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
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

Write-Host 'PaperFlow installation steps completed.'
