# Local Linux product regression

This image is separate from the validated API smoke image because product tests
need the project's pinned test dependencies and Python 3.11.11. The smoke image
and its historical results are unchanged. This is local Linux evidence only;
it does not impersonate the required externally attested Ubuntu 24.04 CI lane.

The only dependency authority is the repository `uv.lock`. Regenerate the export:

```powershell
uv export --locked --extra test --no-group container-test --no-default-groups --no-emit-project --format requirements.txt --output-file tools/container-tests/requirements-regression.txt --no-progress
docker build --platform linux/amd64 --pull=false -f tools/container-tests/Dockerfile.regression -t evm-product-regression:0.1.0 tools/container-tests
```

Resolve the built image ID before execution; use that immutable ID, not the tag.
No project source is baked into this dependency image. Mount a clean exact
checkout of the whole repository at `/workspace:ro`, record its commit/tree
and working-byte hashes, and use a unique external `/results` directory.
The whole root is required because tests read the parent `.github/workflows`.

Use one fresh container per run with `--init --network none --read-only
--cap-drop ALL --security-opt no-new-privileges --cpus 2 --memory 2g
--pids-limit 256 --tmpfs /tmp:rw,nosuid,noexec,size=536870912`.
The init process reaps children in the exact-parent-death contract test.
Do not grant a Docker socket, host PID namespace, privileged mode or GPU access.
Set the run label and `PYTHONPATH=/workspace/enterprise-vision-mlops/src`.

Collect and execute the exact three files in
`ci/pre-r8-r7s5-test-lanes.json:file_inventory.lanes.portable`:

```text
python -I -B -m pytest --collect-only -q -p no:cacheprovider <exact files>
python -I -B -m pytest -q -rs -p no:cacheprovider --basetemp=/tmp/pytest --junitxml=/results/junit.xml <exact files>
```

Bind the actual node inventory, image/package origins, inputs, command,
timeout, exit code and raw stdout/stderr/JUnit in the existing task receipt.
Zero unexpected skip, deselection, missing node or failure is required.
Export logs and final state before removing only the exact run container.
No service recreation, host/Docker/WSL restart, fault injection or V4 experiment
belongs to this local lane. Reusing an earlier immutable image ID is the rollback.
