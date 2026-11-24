param([switch]$DryRun, [switch]$Scheduled)

$ErrorActionPreference = 'Stop'
$repository = 'oyamada-shinya/FX_auto'
$workflow = 'fx.yml'
$dispatch = @('workflow', 'run', $workflow, '--repo', $repository,
              '--ref', 'main', '--raw-field', 'reference_only=true')
if ($Scheduled) {
    $dispatch += @('--raw-field', 'external_hourly=true')
}

if ($DryRun) {
    Write-Output ('gh ' + ($dispatch -join ' '))
    return
}

$githubCli = (Get-Command gh.exe -ErrorAction Stop).Source
& $githubCli auth status --hostname github.com *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'GitHub CLI authentication is unavailable for this Windows account.'
}

$runUrl = & $githubCli @dispatch
if ($LASTEXITCODE -ne 0) {
    throw "GitHub workflow dispatch failed with exit code $LASTEXITCODE."
}
Write-Output "$([DateTimeOffset]::UtcNow.ToString('o')) GitHub dispatch accepted. $runUrl"
