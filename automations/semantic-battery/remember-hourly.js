const validated = $json;
const selected = $('Select LLM Review').item.json;
if (validated.dry_run || selected.dry_run || validated.valid !== true || !validated.review || validated.failure) return null;
if (typeof validated.event_id !== 'string' || !/^[A-Za-z0-9_:.-]{8,200}$/.test(validated.event_id)) return null;
if (!Number.isFinite(Date.parse(selected.captured_at))) return null;
const text = plain(JSON.stringify(validated.review), 3000);
return {json: {retrievalBody: {profile: 'battery', operation: 'remember', source_id: 'hourly:' + validated.event_id,
  records: [{id: 'validated-review', text, metadata: {kind: 'validated-advisory-review', title: 'Battery historical review',
    captured_at: selected.captured_at, source: 'hourly'}}]}}};
