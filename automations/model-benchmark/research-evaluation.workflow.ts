import {
  expr,
  fromAi,
  languageModel,
  newCredential,
  node,
  nodeJson,
  outputParser,
  tool,
  trigger,
  workflow,
} from '@n8n/workflow-sdk';

const inbound = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 2.1,
  config: {
    name: 'Synthetic research benchmark webhook',
    parameters: {
      httpMethod: 'POST',
      path: 'homecompute-research-model-benchmark',
      authentication: 'none',
      responseMode: 'lastNode',
      responseData: 'firstEntryJson',
      options: {},
    },
  },
});

const validate = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Validate synthetic research request',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `const request = $input.first().json.body ?? $input.first().json;
if (request.safety_mode !== 'synthetic-inputs-no-side-effects') {
  throw new Error('Refusing request without benchmark safety marker');
}
if (typeof request.model !== 'string' || !Array.isArray(request.messages)) {
  throw new Error('Expected model and messages');
}
const systemPrompt = request.messages
  .filter((message) => message.role === 'system')
  .map((message) => String(message.content))
  .join('\\n\\n');
const userPrompt = request.messages
  .filter((message) => message.role !== 'system')
  .map((message) => String(message.content))
  .join('\\n\\n');
return [{ json: {
  case_id: String(request.case_id ?? ''),
  model: request.model,
  systemPrompt,
  userPrompt,
} }];`,
    },
  },
});

const model = languageModel({
  type: '@n8n/n8n-nodes-langchain.lmChatOpenRouter',
  version: 1,
  config: {
    name: 'Research benchmark OpenRouter model',
    parameters: {
      model: nodeJson(validate, 'model'),
      options: { temperature: 0, maxRetries: 0, timeout: 360000, responseFormat: 'json_object' },
    },
    credentials: { openRouterApi: newCredential('OpenRouter account') },
  },
});

const tavily = tool({
  type: '@tavily/n8n-nodes-tavily.tavilyTool',
  version: 1,
  config: {
    name: 'Search in Tavily',
    parameters: {
      resource: 'search',
      operation: 'query',
      query: fromAi('Query', 'A public web search query needed to complete the task'),
      options: { search_depth: 'basic', max_results: 5, include_answer: 'basic' },
    },
    credentials: { tavilyApi: newCredential('Tavily account') },
  },
});

const parser = outputParser({
  type: '@n8n/n8n-nodes-langchain.outputParserStructured',
  version: 1.3,
  config: {
    name: 'Research benchmark structured output',
    parameters: {
      schemaType: 'fromJson',
      jsonSchemaExample: '{"updated_context":"## Current state\\n\\n- **Completed:** …","change_summary":"- Added …\\n- Updated …","new_status":"Ready"}',
      autoFix: false,
    },
  },
});

const agent = node({
  type: '@n8n/n8n-nodes-langchain.agent',
  version: 3.1,
  config: {
    name: 'Research benchmark agent',
    parameters: {
      promptType: 'define',
      text: expr('{{ $json.userPrompt }}'),
      hasOutputParser: true,
      options: {
        systemMessage: expr('{{ $json.systemPrompt }}'),
        maxIterations: 20,
        returnIntermediateSteps: true,
      },
    },
    subnodes: { model, tools: [tavily], outputParser: parser },
  },
});

const response = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Capture research result without Notion writes',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `const result = $input.first().json;
const output = result.output ?? result.text ?? '';
return [{ json: {
  text: typeof output === 'string' ? output : JSON.stringify(output),
  model: $('Validate synthetic research request').first().json.model,
  provider: 'n8n/OpenRouter',
  workflow: 'notion-research-with-tavily',
  tool_trace: result.intermediateSteps ?? [],
  notion_writes: 0,
  notifications_sent: false
} }];`,
    },
  },
});

export default workflow('homecompute-research-model-evaluation', 'HomeCompute TEST ONLY - Tavily research model evaluation')
  .add(inbound)
  .to(validate)
  .to(agent)
  .to(response);
