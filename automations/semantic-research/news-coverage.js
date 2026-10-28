const source = $('Select relevant diverse comments').item.json;
const now = Date.parse(currentTimestamp());
const earlier_coverage = validResults($json).filter(row => row.source_id !== 'hn:' + source.story.id
  && /^https:\/\/news\.ycombinator\.com\/item\?id=\d+$/.test(row.metadata?.url ?? '')
  && Number.isFinite(Date.parse(row.metadata?.observed_at)) && Date.parse(row.metadata.observed_at) < now)
  .slice(0, 2).map(row => ({text: plain(row.text, 700), title: plain(row.metadata.title, 160),
    url: row.metadata.url, observed_at: row.metadata.observed_at}));
return {json: {story: {...source.story, earlier_coverage}}};
