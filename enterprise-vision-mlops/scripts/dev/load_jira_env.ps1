param(
    [string]$Path = "$env:USERPROFILE\.evm\jira.local.env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Path)) {
    throw "Jira local env file not found: $Path"
}

$allowedNames = @(
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "JIRA_BOARD_ID",
    "JIRA_EPIC_ISSUE_TYPE",
    "JIRA_TASK_ISSUE_TYPE",
    "JIRA_SPRINT_PREFIX",
    "JIRA_STATUS_TRANSITION_MAP"
)

Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }

    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) {
        throw "Invalid env line in ${Path}: $line"
    }

    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    if ($allowedNames -notcontains $name) {
        throw "Unsupported Jira env key in ${Path}: $name"
    }

    Set-Item -Path "Env:$name" -Value $value
}

$required = @("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY")
$missing = @()
foreach ($name in $required) {
    if (-not (Get-Item -Path "Env:$name" -ErrorAction SilentlyContinue)) {
        $missing += $name
    }
}

if ($missing.Count -gt 0) {
    throw "Missing required Jira env keys: $($missing -join ', ')"
}

Write-Host "Loaded Jira env from $Path"
