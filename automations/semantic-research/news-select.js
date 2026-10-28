const source = $('Prepare news semantic requests').item.json;
const story = source.story;
const allowed = new Map(story.commentCandidates.map(comment => [String(comment.id), comment]));
const seen = new Set();
const ranked = validResults($json).filter(row => allowed.has(row.id) && !seen.has(row.id) && seen.add(row.id));
const selected = [];
function words(text) { return new Set(plain(text).toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? []); }
function overlap(first, second) {
  const a = words(first), b = words(second);
  return a.size && b.size ? [...a].filter(word => b.has(word)).length / Math.min(a.size, b.size) : 0;
}
if (ranked.length) {
  const candidates = ranked.map(row => allowed.get(row.id));
  while (selected.length < 8 && candidates.length) {
    let best = 0, bestScore = -Infinity;
    for (let index = 0; index < candidates.length; index++) {
      const novelty = selected.length ? 1 - Math.max(...selected.map(chosen => overlap(chosen.text, candidates[index].text))) : 1;
      const relevance = 1 - story.commentCandidates.findIndex(comment => comment.id === candidates[index].id) / 100;
      const score = (1 - index / 30) * 0.65 + novelty * 0.35 + relevance * 0.01;
      if (score > bestScore) { best = index; bestScore = score; }
    }
    selected.push(candidates.splice(best, 1)[0]);
  }
} else selected.push(...story.comments);
const {commentCandidates, ...safeStory} = story;
return {json: {story: {...safeStory, comments: selected.slice(0, 8)}, retrievalBody: {profile: 'news', operation: 'search',
  query: queryText(story.title + ' ' + story.submission), limit: 3}}};
