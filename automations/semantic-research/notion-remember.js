const page = $('Filter').item.json;
const output = $('AI Agent').item.json.output;
if (typeof output?.updated_context !== 'string' || !output.updated_context.trim()) return null;
const source_id = pageSource(page.id);
const timestamp = currentTimestamp();
const title = 'Confirmed Notion research';
const metadata = {title, url: 'https://www.notion.so/' + source_id.slice(7), observed_at: timestamp, kind: 'confirmed-research'};
return {json: {retrievalBody: {profile: 'research', operation: 'remember', source_id,
  records: passageRecords(output.updated_context, metadata)}}};
