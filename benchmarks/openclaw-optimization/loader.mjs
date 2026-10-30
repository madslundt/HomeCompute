// Private-process-only overlay for qualification. No installed runtime file is
// changed, and the production gateway does not load this module.
import { registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
const target='file:///usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/browser-tool.schema-CSTr80M_.js';
const source=readFileSync(new URL('./browser-candidate.js',import.meta.url),'utf8');
registerHooks({load(url,context,nextLoad){
  if(url===target)return {format:'module',source,shortCircuit:true};
  return nextLoad(url,context);
}});
