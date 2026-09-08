# Container test image

This directory is the complete Docker build context for the bounded container-test client. The
base is the official Linux/amd64 Locust 2.46.5 image pinned by digest. Its upstream Dockerfile
provides `/opt/venv`, the `locust` runtime user, and `/home/locust`; this image temporarily uses
root only while installing the exported dependency closure, then returns to `USER locust`.

`requirements.txt` is generated from the repository lock with `uv export --frozen
--only-group container-test`. Do not hand-edit it or maintain a second dependency lock here. The
image installs that complete export with `--require-hashes --no-deps` and runs `pip check` during
the build. Build a new version only when the pinned base digest, Dockerfile, dependency lock, or
exported requirements change; do not rebuild the image for each test run. The build context must
remain this directory only:

```text
docker build --platform linux/amd64 --pull --no-cache --tag evm-container-tests:0.1.0 tools/container-tests
docker image inspect evm-container-tests:0.1.0 --format "{{.Id}}"
docker image save --output <absolute-artifact-path>/evm-container-tests-0.1.0.tar evm-container-tests:0.1.0
```

Record the returned immutable image ID and the SHA-256 and byte length of the image archive. Keep
each version's archive and record immutable; do not overwrite a previous version. To roll back,
verify the selected previous archive's recorded SHA-256 before `docker image load --input
<exact-previous-archive>`, verify that the loaded image ID equals its recorded previous image ID,
and continue to run by that `sha256:...` ID. Runtime commands must never rely on a mutable tag. The
default command only reports the installed Locust version; CT-M04 will supply a separately
hash-bound runner from a read-only code mount.

Every test container must be fresh and use all of these constraints (replace angle-bracketed
values with absolute paths or immutable identifiers):

```text
docker run --name evm-ct-<run-id> --label evm.ct.run=<run-id> --network evm-local --cpus 1 --memory 512m --pids-limit 128 --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 1000:1000 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m --mount type=bind,src=<absolute-code>,dst=/work/code,readonly --mount type=bind,src=<absolute-input>,dst=/work/input,readonly --mount type=bind,src=<absolute-output>,dst=/work/output --workdir /work/code sha256:<image-id> -I -B /work/code/<runner.py> --profile /work/input/<profile.json> --output /work/output/<result.json>
```

Do not remove the container automatically. After it exits, capture its stdout, stderr, inspected
exit state, configuration, and output-file hashes. Confirm that its exact name is
`evm-ct-<run-id>` and its `evm.ct.run` label exactly equals `<run-id>`. Only after those artifacts
are safely stored may the operator remove that one owned container with `docker container rm
evm-ct-<run-id>`. Never use a name prefix, wildcard, bulk removal, or `docker volume prune` in this
workflow.

Before execution, independently verify that the pinned image reports Linux/amd64 and that the
upstream `locust` account is UID/GID 1000. If either differs, stop rather than changing `--user`.
Do not add a Docker socket, GPU device, host PID namespace, `--privileged`, extra capabilities, or
writable code/input mounts. Only `/work/output` is writable. The API target is the existing
`evm-api` service on `evm-local`; the test container must not create lifecycle, training,
deployment, or fault-injection work.

`profiles/smoke.example.json` is deliberately `NOT_RUN`. It is not executable or credit-bearing
until CT-M04 binds a freshly captured actual capacity-probe catalog and request corpus by hash.
The Windows ETW, Windows/WSL dual-collector, and high-integrity-token gates remain deferred and
must never inherit PASS from this Linux container lane.

Upstream image layout reference: <https://github.com/locustio/locust/blob/2.46.5/Dockerfile>
