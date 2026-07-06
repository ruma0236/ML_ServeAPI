# VLM-First Manufacturing Direction Sync

Date: 2026-07-05
Branch: `codex/mac-mini-worker`
Scope: Git, Jira, Notion, and Obsidian synchronization for the newly shared
July 31 direction document.

## Summary

The project direction is now anchored on a sharper July MVP:

Manufacturing Visual Inspection VLM-first AI Infra / MLOps / AIOps.

W0-W3 remain the control-plane foundation. W4/W5 are no longer generic
observability and CI hardening first; they now prioritize a real industrial
image dataset, manufacturing manifest and validation flow, VLM adapter contract,
batch inference/evaluation, regression gates, rollback/failure scenarios,
benchmarking, RCA/audit linkage, and final portfolio evidence.

## Priority Reset

P0 for the July 31 cut:

- real industrial anomaly dataset handling, with VisA recommended as primary
  and MVTec AD as fallback or secondary;
- dataset import, manifest, validation, sharding, sampling, retry/resume, audit
  and lineage hooks;
- VLM request/response adapter interface with mock-first implementation;
- manifest-based batch inference and structured VLM output validation;
- prompt/model version tracking, regression gate, rollback simulation, failure
  scenario suite, benchmark report, RCA/audit evidence, and final demo script.

P1 if time allows:

- Grafana dashboard hardening, MLflow prompt/model/eval artifact tracking,
  traffic spike benchmark, GPU/resource metrics, Docker Compose polish, drift
  metrics, audit log hardening, and distributed Windows/Mac mini docs.

P2 after August:

- LLM Agent, LangGraph AgentOps, human approval workflow, tool-call safety,
  Kueue/GPU scheduling, OpenLineage/Marquez, Ray Serve, KServe, production
  vLLM, multi-GPU simulation, autoscaling, synthetic defect generation, RAG
  over defect history, and full MLflow Registry governance.

## Git-Tracked Planning Updates

Updated:

- `README.md`
- `docs/issues/issue-register.md`
- `docs/agenda/enterprise-mlops-roadmap.md`
- `docs/agenda/enterprise-mlops-implementation-agenda.md`
- `docs/agenda/enterprise-multimodal-mlops-target-roadmap.md`

New backlog IDs:

- `EVM-EPIC-13`: Manufacturing VLM P0 Foundation
- `EVM-EPIC-14`: VLM Reliability Evaluation And Portfolio Cut
- `EVM-130` to `EVM-144`: W4 dataset, manifest, validation, adapter, router,
  batch inference, and schema validation foundation
- `EVM-151` to `EVM-181`: W5 prompt/model registry, regression gate, RCA,
  failure scenarios, benchmark, and final demo evidence

## Jira Synchronization State

Repository source data is now present in `docs/issues/issue-register.md`.

Target live sync command:

```powershell
python scripts\dev\jira_sync.py --project-root . --project-key SCRUM --source-id EVM-EPIC-13,EVM-EPIC-14,EVM-130,EVM-131,EVM-132,EVM-133,EVM-134,EVM-135,EVM-141,EVM-142,EVM-143,EVM-144,EVM-151,EVM-152,EVM-161,EVM-162,EVM-171,EVM-181 --labels vlm-first,manufacturing-visual-inspection --transition-statuses
```

The active process environment did not expose `JIRA_*` values during the first
sync attempt, so Jira live write requires credentials to be present in the
executing shell or a secure one-shot run.

## Notion And Obsidian Sync

Notion was updated with a decision/planning entry under the Enterprise Vision
MLOps knowledge base describing the VLM-first manufacturing reset, new P0/P1/P2
priority split, W4/W5 backlog IDs, and open decisions.

Notion detail page:

```text
https://app.notion.com/p/39410ad2dcad81f7bdbafb8942042030
```

Updated Notion pages:

- `01 Phase Review Index`
- `03 Evidence And Run Ledger`
- `04 Decision Log`

Obsidian was updated with a work log and current context pack update so future
Codex sessions can recover:

- the VLM-first manufacturing target;
- the W4/W5 issue ranges;
- the recommended dataset and model strategy;
- the deferment of LLM Agent/LangGraph/KServe/Ray/vLLM production work.

Updated Obsidian files:

- `08_Codex_Memory/01_Work_Logs/2026-07-05 VLM First Manufacturing Direction Sync.md`
- `08_Codex_Memory/02_Context_Packs/Current Context Pack.md`
- `08_Codex_Memory/05_Retrieval_Index/Read First - Codex Retrieval Index.md`

## Open Decisions

- Confirm VisA as the primary P0 dataset, with MVTec AD as fallback or
  secondary comparison.
- Confirm mock-first VLM adapter, then real Qwen2.5-VL 3B/7B quantized endpoint
  on the Windows RTX node.
- Confirm whether Mac mini becomes the active control-plane during July or
  remains a remote/control-plane candidate while Windows keeps the current repo
  and Docker stack.
