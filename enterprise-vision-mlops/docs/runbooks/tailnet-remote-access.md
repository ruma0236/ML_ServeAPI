# Tailnet Remote Access

## Security Boundary

Docker-published ports bind to `127.0.0.1` by default. Remote operator access
is provided through Tailscale Serve, so the services remain limited to devices
and users authorized by the tailnet. Do not forward the Docker backend ports
directly from the public router.

The Control Panel Vite server accepts the tailnet DNS name through the explicit
`VITE_CONTROL_PANEL_ALLOWED_HOSTS` allowlist. Add exact hostnames as a
comma-separated list; do not use a wildcard.

The Tailscale URLs use HTTP at the application layer, but the tailnet transport
is encrypted. Public internet access requires a separate HTTPS gateway with
SSO or another strong authentication boundary.

## Enable

Start the Docker stack and the Control Panel, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev/configure_tailscale_remote_access.ps1 -IncludeDataPlane
```

Disable the routes with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev/configure_tailscale_remote_access.ps1 -IncludeDataPlane -Disable
```

## Published Tailnet Ports

| Service | Tailnet port | Local target | Purpose |
|---|---:|---:|---|
| Control Panel | 4174 | 4173 | Unified lifecycle UI and proxied Control Panel API |
| Grafana | 3001 | 3000 | Dashboards and operational metrics |
| MLflow | 5001 | 5000 | Experiments, runs, artifacts, and model registry |
| Airflow | 8081 | 8080 | DAG inspection and orchestration operations |
| Control API | 8001 | 8000 | API clients and OpenAPI inspection |
| MinIO Console | 9002 | 9001 | Object-storage administration |
| MinIO S3 API | 9003 | 9000 | Authenticated remote S3 clients |
| Prometheus | 9091 | 9090 | Query and target inspection |

PostgreSQL is intentionally not published through Tailscale Serve.

## Public Router Policy

Do not port-forward `3000`, `4173`, `5000`, `5433`, `8000`, `8080`, `9000`,
`9001`, or `9090`. These services include unauthenticated or high-privilege
management surfaces in the current local stack.

For future public access, expose only a hardened gateway on external TCP 443.
The gateway must provide trusted TLS, SSO/MFA, role-based authorization, rate
limits, request and mutation audit logs, and explicit routes to approved
services. PostgreSQL, Prometheus, the MinIO S3 API, and raw MLflow endpoints
must remain private unless a separate service-level authorization layer is
implemented.
