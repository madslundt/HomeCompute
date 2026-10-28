import { requestTask, requestAction, submitSchema, taskIdSchema, statusSchema, actionSchema } from './transport.mjs';

// OpenClaw 2026.9.9 supports the public OpenClawPluginDefinition register API.
// Plain ESM entry + JSON Schema keeps this audited bridge dependency-free.
export default {
  id: 'homecompute-broker',
  name: 'HomeCompute task broker',
  description: 'Queue investigations and read/cancel their bounded task metadata.',
  register(api) {
    for (const [name, description, parameters, operation] of [
      ['homecompute_task_submit', 'Request isolated coding preparation under reviewed project policy. This never authorizes publication, deployment or host changes.', submitSchema, 'submit'],
      ['homecompute_task_status', 'Read one task by task_id, or omit task_id to list up to 100 recent tasks. Returns safe state and approved result links.', statusSchema, 'status'],
      ['homecompute_task_cancel', 'Request cancellation of a queued or running investigation.', taskIdSchema, 'cancel'],
      ['homecompute_action_propose', 'Propose one operator-defined action using trusted evidence. Separate human approval is required for execution.', actionSchema, 'propose'],
      ['homecompute_action_status', 'Read safe action metadata. This cannot ingest evidence, review, approve or execute.', statusSchema, 'status'],
      ['homecompute_action_cancel', 'Cancel an action proposal before execution. This cannot retry or undo an action.', taskIdSchema, 'cancel'],
    ]) {
      api.registerTool({
        name,
        description,
        parameters,
        async execute(_callId, params, signal) {
          const isAction = name.startsWith('homecompute_action_');
          const task = await (isAction ? requestAction : requestTask)(operation, params, { signal });
          const result = isAction ? { actions: Array.isArray(task) ? task : [task] } : Array.isArray(task) ? { tasks: task } : { task };
          return { content: [{ type: 'text', text: JSON.stringify(result) }], details: result };
        },
      }, { optional: true });
    }
  },
};
