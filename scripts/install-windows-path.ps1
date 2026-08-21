function Add-PaperFlowPathEntry {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$CurrentPath,

        [Parameter(Mandatory = $true)]
        [string]$BinDir
    )

    $normalizedBinDir = $BinDir.Trim().TrimEnd('\')
    foreach ($entry in @($CurrentPath -split ';')) {
        if (-not [string]::IsNullOrWhiteSpace($entry) -and
            $entry.Trim().TrimEnd('\') -ieq $normalizedBinDir) {
            return [pscustomobject]@{
                Changed = $false
                Value = $CurrentPath
            }
        }
    }

    $updatedPath = if ([string]::IsNullOrWhiteSpace($CurrentPath)) {
        $BinDir
    }
    else {
        "$CurrentPath;$BinDir"
    }
    return [pscustomobject]@{
        Changed = $true
        Value = $updatedPath
    }
}

function Set-PaperFlowPathEntry {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$CurrentPath,

        [Parameter(Mandatory = $true)]
        [string]$BinDir,

        [AllowEmptyString()]
        [string]$LegacyBinDir = ''
    )

    $normalize = {
        param([string]$Value)
        $Value.Trim().TrimEnd('\')
    }
    $normalizedNew = & $normalize $BinDir
    $normalizedLegacy = & $normalize $LegacyBinDir
    $entries = [System.Collections.Generic.List[string]]::new()
    $newPresent = $false
    $changed = $false

    foreach ($rawEntry in @($CurrentPath -split ';')) {
        if ([string]::IsNullOrWhiteSpace($rawEntry)) {
            continue
        }
        $normalizedEntry = & $normalize $rawEntry
        if ($normalizedLegacy -and
            $normalizedLegacy -ine $normalizedNew -and
            $normalizedEntry -ieq $normalizedLegacy) {
            $changed = $true
            continue
        }
        if ($normalizedEntry -ieq $normalizedNew) {
            $newPresent = $true
        }
        $entries.Add($rawEntry)
    }

    if (-not $newPresent) {
        $entries.Add($BinDir)
        $changed = $true
    }

    return [pscustomobject]@{
        Changed = $changed
        Value = ($entries -join ';')
    }
}
