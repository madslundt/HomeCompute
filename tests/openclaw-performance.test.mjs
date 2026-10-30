import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { projectProtectedBrowserSchema, BROWSER_ACTION_FIELDS, PROTECTED_BROWSER_DESCRIPTION }
  from '../deploy/openclaw/safety-plugin/browser-schema.mjs';
import { browserPolicy } from '../deploy/openclaw/safety-plugin/index.mjs';

const fields = ['action','profile','target',...Object.values(BROWSER_ACTION_FIELDS).flat(),
  'text','submit','request','node','paths','accept','timeoutMs'];
const native = { type:'object', properties:Object.fromEntries(fields.map(f=>[f,{type:'string'}])),
  required:['action'] };
native.properties.action.enum = [...Object.keys(BROWSER_ACTION_FIELDS),'act','upload','dialog'];

test('projection omits forbidden actions and unused interactive schema fields',()=>{
  const projected=projectProtectedBrowserSchema(native);
  assert.deepEqual(projected.properties.action.enum,Object.keys(BROWSER_ACTION_FIELDS));
  for(const field of ['text','submit','request','node','paths','accept','timeoutMs'])
    assert.equal(projected.properties[field],undefined);
  assert.equal(projected.additionalProperties,false);
  assert.equal(native.properties.text.type,'string');
});
test('every currently permitted guard field survives projection',()=>{
  const projected=projectProtectedBrowserSchema(native);
  for(const [action,allowed] of Object.entries(BROWSER_ACTION_FIELDS)) {
    assert.ok(projected.properties.action.enum.includes(action));
    for(const field of allowed)assert.ok(projected.properties[field],`${action}:${field}`);
  }
});
test('model-facing projection does not change independent vetoes',()=>{
  assert.equal(browserPolicy({toolName:'browser',params:{action:'act',kind:'click',ref:'x'}}).block,true);
  assert.equal(browserPolicy({toolName:'browser',params:{action:'navigate',targetUrl:'http://127.0.0.1/'}}).block,true);
  assert.equal(browserPolicy({toolName:'browser',params:{action:'navigate',targetUrl:'https://example.com/'}}).block,undefined);
  assert.match(PROTECTED_BROWSER_DESCRIPTION,/untrusted/);
});
test('unknown native schema fails rather than silently hiding capabilities',()=>{
  assert.throws(()=>projectProtectedBrowserSchema({type:'object',properties:{}}));
});
test('compatibility aliases absent in the native model schema are not invented',()=>{
  const source=structuredClone(native);
  delete source.properties.tabId;
  delete source.properties.suggestedTargetId;
  assert.equal(projectProtectedBrowserSchema(source).properties.tabId,undefined);
});
test('projection field inventory matches the independent guard declaration',()=>{
  const source=readFileSync(new URL('../deploy/openclaw/safety-plugin/index.mjs',import.meta.url),'utf8');
  const declarations=source.slice(source.indexOf('const COMMON ='),source.indexOf('const DENIED ='));
  const actual=runInNewContext(declarations+'; JSON.stringify(ACTIONS)');
  assert.deepEqual(JSON.parse(actual),BROWSER_ACTION_FIELDS);
});
