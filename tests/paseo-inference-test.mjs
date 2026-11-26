import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { test } from "node:test";
import { createInferenceServer, waveToPcm } from "../deploy/paseo/inference.mjs";

function wave(samples, rate = 24000) {
  const result = Buffer.alloc(44 + samples.length * 2);
  result.write("RIFF", 0);
  result.writeUInt32LE(result.length - 8, 4);
  result.write("WAVEfmt ", 8);
  result.writeUInt32LE(16, 16);
  result.writeUInt16LE(1, 20);
  result.writeUInt16LE(1, 22);
  result.writeUInt32LE(rate, 24);
  result.writeUInt32LE(rate * 2, 28);
  result.writeUInt16LE(2, 32);
  result.writeUInt16LE(16, 34);
  result.write("data", 36);
  result.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((sample, index) => result.writeInt16LE(sample, 44 + index * 2));
  return result;
}

async function listen(testContext, server) {
  server.listen(0, "127.0.0.1");
  testContext.after(() => server.close());
  await once(server, "listening");
  return `http://127.0.0.1:${server.address().port}`;
}

test("converts mono WAV to 24 kHz PCM and rejects truncated input", () => {
  const samples = wave([1000, -1000]);
  assert.deepEqual(waveToPcm(samples), samples.subarray(44));
  const resampled = waveToPcm(wave([0, 3000], 16000));
  assert.deepEqual([0, 2, 4].map(offset => resampled.readInt16LE(offset)), [0, 2000, 3000]);
  assert.throws(() => waveToPcm(samples.subarray(0, 45)), /Invalid WAV/);
});

test("forwards SSE and multipart, converts voice audio and blocks management paths", async t => {
  const incoming = [];
  const upstream = await listen(t, createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    incoming.push({ path: request.url, headers: request.headers, body: Buffer.concat(chunks) });
    if (request.url === "/v1/audio/speech") response.writeHead(200).end(wave([42]));
    else if (request.url === "/v1/audio/transcriptions") response.writeHead(200).end('{"text":"test"}');
    else response.writeHead(200, { "content-type": "text/event-stream" }).end("data: [DONE]\n\n");
  }));
  const relay = await listen(t, createInferenceServer({ upstream, key: "scoped-key" }));
  const rejected = await fetch(relay + "/key/info");
  assert.equal(rejected.status, 404);
  assert.equal(incoming.length, 0);

  const stream = await fetch(relay + "/v1/responses", {
    method: "POST", body: "{}", headers: { authorization: "Bearer caller" },
  });
  assert.equal(stream.headers.get("content-type"), "text/event-stream");
  assert.equal(await stream.text(), "data: [DONE]\n\n");
  assert.equal(incoming[0].headers.authorization, "Bearer scoped-key");

  const form = new FormData();
  form.set("model", "stt-danish");
  form.set("file", new Blob([wave([42])]), "test.wav");
  assert.deepEqual(await fetch(relay + "/v1/audio/transcriptions", { method: "POST", body: form }).then(r => r.json()),
                   { text: "test" });
  assert.match(incoming[1].headers["content-type"], /^multipart\/form-data; boundary=/);
  assert.ok(incoming[1].body.includes(Buffer.from('name="model"')));

  const audio = await fetch(relay + "/v1/audio/speech", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: "omnivoice", response_format: "pcm" }),
  });
  assert.equal(audio.headers.get("content-type"), "audio/pcm");
  assert.equal(Buffer.from(await audio.arrayBuffer()).readInt16LE(), 42);
  assert.equal(JSON.parse(incoming[2].body).response_format, "wav");
});
