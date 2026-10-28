const digest = $('Prepare five stories and comments').first().json;
return digest.stories.map(story => ({json: {story, retrievalBody: {profile: 'news', operation: 'rank',
  query: queryText(story.title + ' ' + story.submission), limit: 20,
  records: story.commentCandidates.map(comment => ({id: String(comment.id), text: plain(comment.text),
    metadata: {title: story.title, url: 'https://news.ycombinator.com/item?id=' + comment.id, kind: 'comment'}}))}}}));
