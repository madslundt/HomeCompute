const original = $json;
const purpose = typeof original.reportType === 'string' ? original.reportType : 'hourly';
const findingText = (original.deterministic_findings ?? []).map(row => row.key + ': ' + row.message).join(' ');
const topics = purpose === 'system_weekly'
  ? 'HomeCompute systems automation reliability missing telemetry performance model software updates previous explanations'
  : purpose === 'weekly' ? 'Battery weekly savings charge discharge cycles dated plan execution coverage modeled cost previous explanations'
  : 'Battery plan adherence optimizer execution faults stale telemetry charge discharge economics previous explanations';
const query = queryText([findingText, topics, purpose, original.period].filter(Boolean).join(' '));
return {json: {...original, retrievalBody: {profile: 'battery', operation: 'search', query, limit: 3}}};
