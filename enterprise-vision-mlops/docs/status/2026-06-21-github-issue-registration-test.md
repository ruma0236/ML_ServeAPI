# 2026-06-21 GitHub Issue Registration Test

## Summary

Created the first Codex-managed GitHub Issue using the repository automation
script.

Issue:

- ID: `EVM-BUG-001`
- GitHub Issue: https://github.com/ruma0236/ML_ServeAPI/issues/1
- Title: `[EVM-BUG-001] sample edit breaks data validation dimensions`
- Status: Open

## Scenario

The test scenario assumes that a sample code edit accidentally changed image
dimension handling and caused the data validation pipeline to receive zero
dimensions.

Reproduction command recorded in the issue:

```powershell
python scripts/run_pipeline.py data-validate --config configs/local.toml
```

Observed behavior recorded in the issue:

```text
Validation fails after a sample code edit changed image width/height handling to emit zero dimensions.
```

Expected behavior recorded in the issue:

```text
Data validation should reject invalid dimensions with a clear failure reason and the pipeline should keep valid records reproducible.
```

## Creation Method

The current shell did not have `GITHUB_TOKEN` or `GH_TOKEN` set. The issue was
created by reading the local Git Credential Manager token inside the PowerShell
process and passing it to `scripts/dev/github_issue.py` without printing the
secret.

No token value was written to the repository or displayed in command output.

## Result

The GitHub API returned:

```json
{
  "number": 1,
  "url": "https://github.com/ruma0236/ML_ServeAPI/issues/1"
}
```

## Next Step

When this flow is used for a real bug, Codex should:

1. create or reuse the issue,
2. fix the bug,
3. run validation commands,
4. post a resolution comment,
5. close the issue after verification.
