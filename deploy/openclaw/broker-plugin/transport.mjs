const BROKER = 'http://broker:8080';
const MAX_RESPONSE_BYTES = 262144;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ISSUE = /^[A-Za-z0-9_.:-]{1,128}$/;
const PR_URL = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/;
const string = (minLength, maxLength, pattern) => ({ type: 'string', minLength, maxLength, ...(pattern ? { pattern: pattern.source } : {}) });
export const submitSchema = {
  type: 'object', additionalProperties: false,
  properties: { project: string(1, 64, ID), issue_key: string(1, 128, ISSUE), summary: string(1, 1000), context: string(0, 16000) },
  required: ['project', 'issue_key', 'summary', 'context'],
};
export const taskIdSchema = {
  type: 'object', additionalProperties: false,
  properties: { task_id: string(36, 36, UUID) }, required: ['task_id'],
};
export const statusSchema = { ...taskIdSchema, required: [] };
export const actionSchema = {
  type: 'object', additionalProperties: false,
  properties: Object.fromEntries(['action_id', 'target', 'incident_key', 'evidence_id'].map(key => [key, string(1, 128, ISSUE)])),
  required: ['action_id', 'target', 'incident_key', 'evidence_id'],
};

function validate(operation, params) {
  const schema = operation === 'submit' ? submitSchema : operation === 'propose' ? actionSchema : operation === 'status' ? statusSchema : taskIdSchema;
  if (!params || typeof params !== 'object' || Array.isArray(params) || Object.keys(params).some(key => !Object.hasOwn(schema.properties, key))) {
    throw new Error('Invalid task arguments');
  }
  if (schema.required.some(key => !Object.hasOwn(params, key))) throw new Error('Invalid task arguments');
  for (const key of Object.keys(params)) {
    const value = params[key];
    const rule = schema.properties[key];
    if (typeof value !== 'string' || value.length < rule.minLength || value.length > rule.maxLength || (rule.pattern && !new RegExp(rule.pattern).test(value))) {
      throw new Error('Invalid task arguments');
    }
  }
}

function actionMetadata(row) {
  if (!row || typeof row !== 'object' || Array.isArray(row) || !UUID.test(row.id ?? '')) throw new Error('Invalid action broker response');
  return Object.fromEntries(['id', 'action_id', 'target', 'incident_key', 'evidence_id', 'state', 'created', 'updated', 'attempts', 'error', 'policy_sha256']
    .filter(key => row[key] === null || ['string', 'boolean'].includes(typeof row[key]) || typeof row[key] === 'number' && Number.isFinite(row[key]))
    .map(key => [key, typeof row[key] === 'string' ? row[key].slice(0, 2048) : row[key]]));
}

// Never expose the broker's request context, prompts, worker logs or credentials.
function metadata(response) {
  const row = response.task ?? response;
  if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('Invalid task broker response');
  const out = {};
  for (const key of ['id', 'task_id', 'project', 'issue_key', 'state', 'status', 'phase', 'progress', 'approval_required', 'approval_kind', 'created', 'updated', 'session_id', 'commit', 'pr_url', 'error_code', 'error']) {
    const value = row[key];
    if (typeof value === 'string') out[key] = value.slice(0, 2048);
    else if (typeof value === 'number' && Number.isFinite(value)) out[key] = value;
    else if (typeof value === 'boolean') out[key] = value;
    else if (value === null) out[key] = null;
  }
  if (row.result && typeof row.result === 'object') {
    out.result = {};
    for (const key of ['session_id', 'commit', 'pr_url', 'ok', 'tests_passed', 'exit_code', 'changed_files']) {
      const value = row.result[key];
      if (typeof value === 'string') out.result[key] = value.slice(0, 2048);
      else if (typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))) out.result[key] = value;
    }
    if (Object.hasOwn(row.result, 'proposal')) {
      const proposal = row.result.proposal;
      const fields = ['problem', 'change', 'validation', 'risk'];
      if (row.result.ok !== true || !proposal || typeof proposal !== 'object' || Array.isArray(proposal)
          || Object.keys(proposal).length !== fields.length || fields.some(key => {
            const text = proposal[key];
            return typeof text !== 'string' || !text.trim() || Array.from(text).length > 400
              || /[\x00-\x09\x0b-\x1f]/.test(text) || /sk-[A-Za-z0-9_-]{16,}|Bearer\s+\S{16,}/.test(text);
          })) throw new Error('Invalid bounded proposal');
      out.result.proposal = Object.fromEntries(fields.map(key => [key, proposal[key]]));
    }
  }
  if (Array.isArray(row.artifacts)) {
    out.artifacts = row.artifacts.slice(0, 10).filter(item => item?.kind === 'pull_request' && typeof item.url === 'string' && PR_URL.test(item.url))
      .map(item => ({ kind: 'pull_request', url: item.url }));
  }
  if (!UUID.test(out.id ?? out.task_id ?? '')) throw new Error('Invalid task broker response');
  return out;
}

/** Fixed-origin, bounded HTTP transport; dependency injection is for isolated tests only. */
async function requestResource(resource, operation, params, { signal, fetchImpl = globalThis.fetch, token = process.env.OPENCLAW_BROKER_TOKEN } = {}) {
  if (!(resource === 'tasks' ? ['submit', 'status', 'cancel'] : ['propose', 'status', 'cancel']).includes(operation)) throw new Error('Invalid task operation');
  validate(operation, params);
  if (!token || /[\r\n]/.test(token)) throw new Error('Task broker authentication is unavailable');
  signal?.throwIfAborted();
  const writesBody = operation === 'submit' || operation === 'propose';
  const path = writesBody || (operation === 'status' && !params.task_id) ? `/${resource}` : `/${resource}/${params.task_id}${operation === 'cancel' ? '/cancel' : ''}`;
  let response;
  try {
    response = await fetchImpl(BROKER + path, {
      method: operation === 'status' ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${token}`, ...(writesBody ? { 'Content-Type': 'application/json' } : {}) },
      ...(writesBody ? { body: JSON.stringify(params) } : {}),
      redirect: 'error',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000),
    });
  } catch {
    throw new Error('Task broker request failed or was interrupted');
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`Task broker rejected request (HTTP ${response.status})`);
  }
  const chunks = [];
  let bytes = 0;
  try {
    for await (const chunk of response.body) {
      bytes += chunk.byteLength;
      if (bytes > MAX_RESPONSE_BYTES) throw new Error('Response limit exceeded');
      chunks.push(chunk);
    }
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const projectMetadata = resource === 'tasks' ? metadata : actionMetadata;
    if (operation === 'status' && !params.task_id) {
      const rows = Array.isArray(body) ? body : body[resource];
      if (!Array.isArray(rows) || rows.length > 100) throw new Error('Invalid task list');
      return rows.map(projectMetadata);
    }
    return projectMetadata(body);
  } catch {
    throw new Error('Invalid or oversized task broker response');
  }
}

export const requestTask = (operation, params, options) => requestResource('tasks', operation, params, options);
export const requestAction = (operation, params, options) => requestResource('actions', operation, params, options);
