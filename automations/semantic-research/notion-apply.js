const prepared = $('Prepare page research retrieval').item.json;
const seen = new Set();
const results = validResults($json).filter(row => row.source_id === prepared.source_id && !seen.has(row.id) && seen.add(row.id)).slice(0, 6);
const history = results.map(row => ({text: plain(row.text), title: plain(row.metadata?.title, 160),
  url: /^https:\/\/(?:www\.)?notion\.so\//.test(row.metadata?.url ?? '') ? row.metadata.url : '',
  observed_at: row.metadata?.observed_at ?? ''}));
return {json: {context: prepared.context, retrieved_history: history, retrieval_available: $json.available === true}};
