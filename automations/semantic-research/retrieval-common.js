function plain(value, limit = 1400) {
  return String(value ?? '').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, limit);
}
function validResults(response) {
  return response?.available === true && Array.isArray(response.results)
    ? response.results.filter(row => row && typeof row.id === 'string' && typeof row.text === 'string') : [];
}
function currentTimestamp() { return $now.toISO(); }
function queryText(value) {
  let output = '', bytes = 0;
  for (const character of plain(value, 1800)) {
    const point = character.codePointAt(0);
    const size = point < 128 ? 1 : point < 2048 ? 2 : point < 65536 ? 3 : 4;
    if (bytes + size > 1100) break;
    output += character; bytes += size;
  }
  return output;
}
function pageSource(id) {
  const canonical = String(id ?? '').replace(/-/g, '').toLowerCase();
  if (!/^[a-f0-9]{32}$/.test(canonical)) throw new Error('Missing exact Notion page identity');
  return 'notion:' + canonical;
}
function passageRecords(text, metadata) {
  const source = plain(text, 24000);
  const records = [];
  for (let offset = 0; offset < source.length && records.length < 20; offset += 1200)
    records.push({id: 'passage-' + records.length, text: source.slice(offset, offset + 1400), metadata});
  return records;
}
