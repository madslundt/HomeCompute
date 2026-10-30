import { n as schema, r as capabilities, i as describe }
  from '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/browser-tool.schema-CSTr80M_.js';
const caps=capabilities({evaluateEnabled:false});
const value=schema(caps);
console.log(JSON.stringify({schema_chars:JSON.stringify(value).length,
  properties:Object.keys(value.properties),actions:value.properties.action.enum,
  description_chars:describe({capabilities:caps}).length}));
