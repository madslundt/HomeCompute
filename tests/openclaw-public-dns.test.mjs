import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { RESEARCH_HOSTS, decodeQuestion, createDnsEngine, publicAddress, createWorkerLookup } from '../deploy/openclaw/safety-plugin/public-dns.mjs';

function query(host = 'example.com', type = 1, qclass = 1) {
  const head = Buffer.alloc(12); head.writeUInt16BE(0x1234); head.writeUInt16BE(0x100, 2); head.writeUInt16BE(1, 4);
  const tail = Buffer.alloc(5); tail.writeUInt16BE(type, 1); tail.writeUInt16BE(qclass, 3);
  return Buffer.concat([head, ...host.split('.').flatMap(label => [Buffer.from([label.length]), Buffer.from(label)]), tail]);
}
const rcode = packet => packet.readUInt16BE(2) & 15;
const dto = hostname => ({ hostname, ttl: 60, addresses: [{ family: 4, address: '93.184.216.34' }, { family: 6, address: '2606:4700::1111' }] });

test('DNS wire accepts only reviewed A/AAAA IN names and never resolves CNAME or other questions', async () => {
  const looked = [];
  const engine = createDnsEngine(async hostname => { looked.push(hostname); return dto(hostname); }, () => 1000);
  for (const type of [1, 28]) {
    const packet = await engine.handle(query('EXAMPLE.COM', type));
    assert.equal(rcode(packet), 0); assert.equal(packet.readUInt16BE(6), 1); assert.equal(packet.readUInt16BE(0), 0x1234);
    const record = 12 + decodeQuestion(query()).bytes.length;
    assert.equal(packet.readUInt16BE(record + 2), type); assert.equal(packet.readUInt32BE(record + 6), 60);
  }
  assert.deepEqual(looked, ['example.com']);
  for (const [host, type, qclass] of [['home-core',1,1],['10.77.20.1',1,1],['example.com.evil',1,1],['example.com',5,1],['example.com',255,1],['example.com',1,3]]) {
    assert.equal(rcode(await engine.handle(query(host,type,qclass))), 3);
  }
  assert.deepEqual(looked, ['example.com']);
  assert.equal(RESEARCH_HOSTS.length, 14);
});

test('DNS wire rejects malformed and compressed questions and limits packet length', async () => {
  const engine = createDnsEngine(() => { throw new Error('must not resolve'); });
  const compressed = Buffer.concat([query().subarray(0,12), Buffer.from([0xc0,0x0c,0,1,0,1])]);
  const multiple = query(); multiple.writeUInt16BE(2,4);
  const response = query(); response.writeUInt16BE(0x8100,2);
  const additional = query(); additional.writeUInt16BE(1,10);
  for (const packet of [Buffer.alloc(0),Buffer.alloc(11),Buffer.alloc(4097),compressed,multiple,response,additional,query().subarray(0,-1),Buffer.concat([query(),Buffer.from([1])])]) {
    assert.equal(rcode(await engine.handle(packet)),1);
  }
});

test('mixed, private, mapped IPv6, malformed and oversized authenticated DNS answers fail closed', async () => {
  for (const address of ['127.0.0.1','10.77.20.1','169.254.169.254','192.0.2.1','224.0.0.1','::1','fd00::1','2001:db8::1','::ffff:10.77.20.1','::ffff:93.184.216.34']) {
    const family = address.includes(':') ? 6 : 4;
    assert.equal(publicAddress(address,family),false,address);
    const engine = createDnsEngine(hostname => ({...dto(hostname),addresses:[...dto(hostname).addresses,{address,family}]}));
    assert.equal(rcode(await engine.handle(query())),2,address);
  }
  for (const value of [{...dto('evil.example')},{...dto('example.com'),ttl:3600},{...dto('example.com'),addresses:[]},{...dto('example.com'),addresses:Array(17).fill({family:4,address:'93.184.216.34'})}]) {
    assert.equal(rcode(await createDnsEngine(() => value).handle(query())),2);
  }
});

test('cache TTL and concurrent DNS budget remain finite even when worker lookup is stalled', async () => {
  let now = 1000; let calls = 0;
  const engine = createDnsEngine(hostname => { calls++;return dto(hostname); }, () => now);
  await engine.handle(query());now += 5000;
  const cached = await engine.handle(query());
  const offset = 12 + decodeQuestion(query()).bytes.length;
  assert.equal(cached.readUInt32BE(offset+6),55); assert.equal(calls,1);
  now += 60000; await engine.handle(query());assert.equal(calls,2);
  const resolvers=[];
  const bounded=createDnsEngine(hostname=>new Promise(resolve=>resolvers.push(()=>resolve(dto(hostname)))));
  const pending=RESEARCH_HOSTS.slice(0,4).map(host=>bounded.handle(query(host)));
  const duplicate=bounded.handle(query(RESEARCH_HOSTS[0],28));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(resolvers.length,4);
  assert.equal(rcode(await bounded.handle(query(RESEARCH_HOSTS[4]))),2);
  resolvers.forEach(resolve=>resolve());await Promise.all([...pending,duplicate]);
});

test('worker lookup uses only the authenticated fixed loopback endpoint and bounds responses', async () => {
  let requestOptions;let calls=0;
  const transport={get(options, callback) {
    calls++; requestOptions=options;
    const request=new EventEmitter();request.destroy=error=>request.emit('error',error);
    queueMicrotask(()=>{const response=new EventEmitter();response.statusCode=200;callback(response);response.emit('data',JSON.stringify(dto('example.com')));response.emit('end');});
    return request;
  }};
  const lookup=createWorkerLookup('q'.repeat(48),transport);
  assert.deepEqual(await lookup('example.com'),dto('example.com'));
  assert.equal(requestOptions.hostname,'127.0.0.1');assert.equal(requestOptions.port,18800);assert.equal(requestOptions.timeout,3000);
  assert.equal(new URL(requestOptions.path,'http://fixed').pathname,'/json/public-dns');
  assert.equal(new URL(requestOptions.path,'http://fixed').searchParams.get('hostname'),'example.com');
  await assert.rejects(lookup('home-core'));assert.equal(calls,1);
  assert.throws(()=>createWorkerLookup('short'));
});
