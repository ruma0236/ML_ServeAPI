# Domain Packs

Domain packs keep the core MLOps platform portable.

The core platform owns orchestration, storage, tracking, registry, serving,
monitoring, rollback, and audit mechanics. A domain pack owns the policy surface
that changes by use case:

- dataset candidates and access policy,
- manifest schema,
- validation rules,
- model adapter contract,
- evaluation schema,
- promotion gates,
- benchmark scenarios,
- audit and RCA keys.

The first reference workload is manufacturing visual inspection with a
VLM-first inference and evaluation path.
