import { Ban, CheckCheck, ShieldCheck, TerminalSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  cancelCommandIntent,
  commandActionFor,
  commandStatusTone,
  confirmCommandIntent,
  createCommandIntent
} from "../api/controlPanelClient";
import type { CommandIntent, CommandIntentRequest, RuntimeResource } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface CommandDrawerProps {
  resources: RuntimeResource[];
  commands: CommandIntent[];
  onCommandsChange: (commands: CommandIntent[]) => void;
}

export function CommandDrawer({ resources, commands, onCommandsChange }: CommandDrawerProps) {
  const defaultResource = useMemo(
    () => resources.find((resource) => resource.name === "evm-api") || resources[0] || null,
    [resources]
  );
  const [resourceId, setResourceId] = useState(defaultResource?.resource_id || "");
  const selectedResource = resources.find((resource) => resource.resource_id === resourceId) || defaultResource;
  const [actor, setActor] = useState("ai-infra-sre");
  const [reason, setReason] = useState("W7 guarded operation review");
  const [scale, setScale] = useState("1");
  const [submitting, setSubmitting] = useState(false);
  const [focusedCommandId, setFocusedCommandId] = useState("");

  useEffect(() => {
    if (!resourceId && defaultResource) setResourceId(defaultResource.resource_id);
  }, [defaultResource, resourceId]);

  async function submitCommand(dryRun: boolean) {
    if (!selectedResource) return;
    setSubmitting(true);
    try {
      const request: CommandIntentRequest = {
        action: commandActionFor(selectedResource),
        target: {
          namespace: selectedResource.namespace,
          kind: selectedResource.kind,
          name: selectedResource.name
        },
        actor,
        reason,
        dry_run: dryRun,
        parameters: {
          requested_replicas: Number.parseInt(scale, 10) || 1,
          node_pool: selectedResource.node_pool,
          pressure: selectedResource.pressure || selectedResource.status
        }
      };
      const command = await createCommandIntent(request);
      setFocusedCommandId(command.command_id);
      onCommandsChange([command, ...commands.filter((item) => item.command_id !== command.command_id)]);
    } finally {
      setSubmitting(false);
    }
  }

  async function updateCommand(commandId: string, action: "confirm" | "cancel") {
    setSubmitting(true);
    try {
      const command = action === "confirm" ? await confirmCommandIntent(commandId) : await cancelCommandIntent(commandId);
      onCommandsChange(commands.map((item) => (item.command_id === command.command_id ? command : item)));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Command Intent</h2>
          <p>{selectedResource ? `${selectedResource.namespace}/${selectedResource.name}` : "no target"}</p>
        </div>
        <TerminalSquare />
      </div>

      <div className="ops-form">
        <label>
          <span>Target</span>
          <select value={resourceId} onChange={(event) => setResourceId(event.target.value)}>
            {resources.map((resource) => (
              <option key={resource.resource_id} value={resource.resource_id}>
                {resource.namespace} / {resource.kind} / {resource.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Actor</span>
          <input value={actor} onChange={(event) => setActor(event.target.value)} />
        </label>
        <label>
          <span>Reason</span>
          <input value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        <label>
          <span>Replicas</span>
          <input inputMode="numeric" value={scale} onChange={(event) => setScale(event.target.value)} />
        </label>
      </div>

      <div className="ops-button-row">
        <button type="button" className="primary-action" disabled={submitting || !selectedResource} onClick={() => void submitCommand(true)} title="Evaluate and audit the command without mutating the target resource.">
          <ShieldCheck size={16} />
          Preview Command
        </button>
        <button type="button" className="secondary-action" disabled={submitting || !selectedResource} onClick={() => void submitCommand(false)}>
          <CheckCheck size={16} />
          Confirm Queue
        </button>
      </div>

      <div className="ledger-list">
        {commands.length ? (
          commands.slice(0, 5).map((command) => (
            <article
              key={command.command_id}
              className={`ledger-item ledger-${commandStatusTone(command.status)}${focusedCommandId === command.command_id ? " focused" : ""}`}
              data-focused-command={focusedCommandId === command.command_id ? "true" : "false"}
            >
              <header>
                <div>
                  <strong>{command.action}</strong>
                  <span>{command.target.namespace} / {command.target.name}</span>
                </div>
                <StatusBadge status={command.status} compact />
              </header>
              <p>{command.reason}</p>
              <div className="ledger-actions">
                <button
                  type="button"
                  disabled={submitting || command.status === "cancelled"}
                  onClick={() => void updateCommand(command.command_id, "confirm")}
                >
                  <CheckCheck size={14} />
                  Confirm
                </button>
                <button
                  type="button"
                  disabled={submitting || command.status === "cancelled"}
                  onClick={() => void updateCommand(command.command_id, "cancel")}
                >
                  <Ban size={14} />
                  Cancel
                </button>
              </div>
              <small>{command.audit.at(-1)?.event || "audit pending"}</small>
            </article>
          ))
        ) : (
          <div className="empty-ledger">No command intents</div>
        )}
      </div>
    </div>
  );
}
