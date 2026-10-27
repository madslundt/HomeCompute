if (['manual', 'test'].includes($execution.mode) || $json.dry_run) return null;
const selected = $('Prepare Review Snapshot').item.json;
if (!['weekly', 'system_weekly'].includes(selected.reportType) || typeof selected.period !== 'string' || !selected.period.trim()) return null;
if (typeof $json.report !== 'string' || !$json.report.trim()) return null;
const period = selected.period.replace(/[^a-zA-Z0-9_-]+/g, '-').slice(0, 100);
return {json: {retrievalBody: {profile: 'battery', operation: 'remember', source_id: 'weekly:' + selected.reportType + ':' + period,
  records: [{id: 'formatted-review', text: plain(selected.period + '\n' + $json.report, 3000), metadata: {
    kind: 'formatted-advisory-review', title: 'Weekly systems and battery review', captured_at: currentTimestamp(), source: selected.reportType}}]}}};
