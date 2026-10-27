const original = $('Prepare five stories and comments').first().json;
const byId = new Map($input.all().map(item => [Number(item.json.story?.id), item.json.story]));
const stories = original.stories.map(story => {
  const enriched = byId.get(story.id);
  if (!enriched || enriched.rank !== story.rank || enriched.articleUrl !== story.articleUrl || enriched.discussionUrl !== story.discussionUrl)
    throw new Error('News retrieval changed official story identity or order');
  return enriched;
});
return [{json: {...original, stories}}];
