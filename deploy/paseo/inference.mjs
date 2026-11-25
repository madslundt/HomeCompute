import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { pathToFileURL } from "node:url";

const fixedRoutes = new Map([
  ["/v1/models", "GET"],
  ["/v1/chat/completions", "POST"],
  ["/v1/responses", "POST"],
  ["/v1/audio/transcriptions", "POST"],
  ["/v1/audio/speech", "POST"],
  ["/v1/embeddings", "POST"],
  ["/v1/videos", "POST"],
]);

function allowedRoute(path, method) {
  return fixedRoutes.get(path) === method ||
    (method === "GET" && /^\/v1\/videos\/[\w-]+(?:\/content)?$/.test(path));
}

async function boundedBody(body, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of body) {
    size += chunk.length;
    if (size > limit) throw new RangeError("Body exceeds limit");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, size);
}

export function waveToPcm(wave) {
  if (wave.length < 12 || wave.toString("ascii", 0, 4) !== "RIFF" ||
      wave.toString("ascii", 8, 12) !== "WAVE" ||
      wave.readUInt32LE(4) !== wave.length - 8) {
    throw new Error("Invalid WAV");
  }
  let format;
  let audio;
  for (let offset = 12; offset + 8 <= wave.length;) {
    const id = wave.toString("ascii", offset, offset + 4);
    const length = wave.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (start + length > wave.length) throw new Error("Truncated WAV");
    if (id === "fmt ") format = wave.subarray(start, start + length);
    if (id === "data") audio = wave.subarray(start, start + length);
    offset = start + length + (length % 2);
  }
  if (!format || format.length < 16 || !audio?.length || audio.length % 2 ||
      format.readUInt16LE(0) !== 1 || format.readUInt16LE(2) !== 1 ||
      format.readUInt16LE(14) !== 16) {
    throw new Error("Unsupported WAV sample format");
  }
  const sourceRate = format.readUInt32LE(4);
  if (sourceRate === 24000) return audio;
  if (sourceRate < 8000 || sourceRate > 48000) throw new Error("Unsupported WAV sample rate");
  const sampleCount = audio.length / 2;
  const output = Buffer.alloc(Math.floor(sampleCount * 24000 / sourceRate) * 2);
  for (let index = 0; index < output.length / 2; index++) {
    const source = index * sourceRate / 24000;
    const first = Math.floor(source);
    const fraction = source - first;
    const left = audio.readInt16LE(first * 2);
    const right = audio.readInt16LE(Math.min(first + 1, sampleCount - 1) * 2);
    output.writeInt16LE(Math.round(left + (right - left) * fraction), index * 2);
  }
  return output;
}

export function createInferenceServer({ upstream, key }) {
  return createServer(async (request, response) => {
    const path = request.url;
    const method = request.method;
    if (method === "GET" && path === "/health") {
      response.writeHead(204).end();
      return;
    }
    if (!allowedRoute(path, method)) {
      response.writeHead(404).end();
      return;
    }

    const headers = { authorization: `Bearer ${key}` };
    if (request.headers["content-type"]) headers["content-type"] = request.headers["content-type"];
    if (request.headers.accept) headers.accept = request.headers.accept;
    let body;
    let pcm = false;
    try {
      if (method === "POST") {
        const isSpeech = path === "/v1/audio/speech";
        body = await boundedBody(request, isSpeech ? 32 * 1024 : 32 * 1024 * 1024);
        if (isSpeech) {
          const speech = JSON.parse(body);
          pcm = speech.response_format === "pcm";
          if (pcm) body = JSON.stringify({ ...speech, response_format: "wav" });
        }
      }
    } catch {
      response.writeHead(400).end();
      return;
    }

    const controller = new AbortController();
    response.once("close", () => controller.abort());
    try {
      const result = await fetch(upstream + path, {
        method, headers, body, redirect: "error",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(620_000)]),
      });
      if (pcm && result.ok) {
        const audio = waveToPcm(await boundedBody(result.body, 32 * 1024 * 1024));
        response.writeHead(200, { "content-type": "audio/pcm", "content-length": audio.length });
        response.end(audio);
        return;
      }
      const returned = {};
      for (const header of ["content-type", "cache-control", "retry-after", "x-litellm-model-id"]) {
        const value = result.headers.get(header);
        if (value) returned[header] = value;
      }
      response.writeHead(result.status, returned);
      if (result.body) await pipeline(Readable.fromWeb(result.body), response);
      else response.end();
    } catch {
      if (response.headersSent) response.destroy();
      else response.writeHead(502).end();
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const key = readFileSync("/run/homecompute/inference-key", "utf8").trim();
  if (!/^sk-[A-Za-z0-9_-]+$/.test(key)) throw new Error("Missing Paseo inference key");
  createInferenceServer({ upstream: "https://llm.home.arpa:8443", key }).listen(18080, "0.0.0.0");
}
