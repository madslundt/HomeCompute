import { requestTask, submitSchema, taskIdSchema, statusSchema } from './transport.mjs';

// OpenClaw 2026.9.9 supports the public OpenClawPluginDefinition register API.
// Plain ESM entry + JSON Schema keeps this audited bridge dependency-free.
export default {
  id: 'homecompute-broker',
  name: 'HomeCompute task broker',
  description: 'Queue investigations and read/cancel their bounded task metadata.',
  register(api) {
    for (const [name, description, parameters, operation] of [
      ['homecompute_task_submit', 'Queue one deduplicated investigation. This does not approve code writes or PR publication.', submitSchema, 'submit'],
      ['homecompute_task_status', 'Read one task by task_id, or omit task_id to list up to 100 recent tasks. Returns safe state and approved result links.', statusSchema, 'status'],
      ['homecompute_task_cancel', 'Request cancellation of a queued or running investigation.', taskIdSchema, 'cancel'],
    ]) {
      api.registerTool({
        name,
        description,
        parameters,
        async execute(_callId, params, signal) {
          const task = await requestTask(operation, params, { signal });
          const result = Array.isArray(task) ? { tasks: task } : { task };
          return { content: [{ type: 'text', text: JSON.stringify(result) }], details: result };
        },
      }, { optional: true });
    }
  },
};
