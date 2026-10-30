// No network or generation. Exercise the installed provider payload builder.
import assert from 'node:assert/strict';
import { buildOpenAICompletionsParams } from '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/@openclaw/ai/dist/transports.mjs';
const base={id:'automation-moe',name:'synthetic',provider:'inference',api:'openai-completions',
  baseUrl:'https://inference.local/v1',reasoning:false,input:['text'],contextWindow:32768,maxTokens:1024,
  cost:{input:0,output:0,cacheRead:0,cacheWrite:0},compat:{supportsStore:false}};
const qualified={...base,reasoning:true,thinkingLevelMap:{off:'none'},
  compat:{...base.compat,supportsReasoningEffort:true,supportedReasoningEfforts:['none','low','medium','high','xhigh']}};
const flagOnly={...qualified,compat:{...base.compat,supportsReasoningEffort:true}};
const context={messages:[{role:'user',content:'Synthetic payload fixture',timestamp:0}],
  tools:[{name:'read',description:'Read supplied fixture',parameters:{type:'object',properties:{path:{type:'string'}},required:['path']}}]};
for(const [variant,model,reasoning] of [
  ['current-off',base,'none'],['flag-only-off',flagOnly,'none'],
  ['qualified-off',qualified,'none'],['qualified-high',qualified,'high'],
]){
  const payload=buildOpenAICompletionsParams(model,context,{reasoning,maxTokens:1024});
  console.log(JSON.stringify({variant,reasoning_effort:payload.reasoning_effort??null,
    tools:payload.tools.map(t=>t.function?.name),stream:payload.stream}));
  assert.equal(payload.reasoning_effort,variant.endsWith('off')&&!variant.startsWith('qualified')?undefined:reasoning);
  assert.equal(payload.tools[0].function.name,'read');
}
const toolResult='{"rice_dkk":30,"lentils_dkk":20}';
const roundTrip={...context,messages:[...context.messages,
  {role:'assistant',api:base.api,provider:base.provider,model:base.id,timestamp:1,
    content:[{type:'toolCall',id:'fixture-read',name:'read',arguments:{path:'fixture.json'}}],
    stopReason:'toolUse',usage:{input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,
      cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}},
  {role:'toolResult',toolCallId:'fixture-read',toolName:'read',timestamp:2,isError:false,
    content:[{type:'text',text:toolResult}]}]};
for(const [variant,model] of [['current',base],['qualified',qualified]]){
  const payload=buildOpenAICompletionsParams(model,roundTrip,{reasoning:'none',maxTokens:1024});
  assert.ok(payload.messages.some(m=>m.role==='assistant'&&m.tool_calls?.[0]?.function?.name==='read'));
  assert.ok(payload.messages.some(m=>m.role==='tool'&&m.content===toolResult));
  console.log(JSON.stringify({variant,tool_round_trip_preserved:true}));
}
