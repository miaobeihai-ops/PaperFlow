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
