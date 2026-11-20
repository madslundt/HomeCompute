import { expr, ifElse, newCredential, node, trigger, workflow } from '@n8n/workflow-sdk';

const clock = trigger({
  type: 'n8n-nodes-base.scheduleTrigger',
  version: 1.4,
  config: {
    name: 'Check every minute',
    parameters: { rule: { interval: [{ field: 'minutes', minutesInterval: 1 }] } },
  },
});

const checkQueue = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.5,
  config: {
    name: 'Check manual run queue',
    parameters: {
      method: 'GET',
      url: 'http://ai-automation-manager:8080/api/workflows/manual-queue',
      authentication: 'genericCredentialType',
      genericAuthType: 'httpTemplatedCustomAuth',
      options: { timeout: 15000 },
    },
    credentials: { httpTemplatedCustomAuth: newCredential('AI automation manager workflow bearer token') },
  },
});

const hasWork = ifElse({
  version: 2.2,
  config: {
    name: 'Manual work waiting?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'strict' },
        conditions: [{
          leftValue: expr('{{ $json.has_pending }}'),
          operator: { type: 'boolean', operation: 'true', singleValue: true },
        }],
        combinator: 'and',
      },
    },
  },
});

const startWorker = node({
  type: 'n8n-nodes-base.executeWorkflow',
  version: 1.4,
  config: {
    name: 'Start automation worker',
    parameters: {
      source: 'database',
      workflowId: { __rl: true, mode: 'id', value: 'SOIDzmG7YFzvOUlP' },
      mode: 'once',
      options: { waitForSubWorkflow: false },
    },
  },
});

export default workflow('homecompute-ai-automation-manual-poller', 'HomeCompute AI automation manual run poller', { saveDataSuccessExecution: 'none' })
  .add(clock)
  .to(checkQueue)
  .to(hasWork.onTrue(startWorker));
