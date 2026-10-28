const digest = $('Assemble ranked news context').first().json;
const timestamp = currentTimestamp();
return digest.stories.map(story => ({json: {retrievalBody: {profile: 'news', operation: 'remember', source_id: 'hn:' + story.id,
  records: [{id: 'story', text: plain(story.title + '\n' + story.submission, 1400),
    metadata: {title: story.title, url: story.discussionUrl, observed_at: timestamp, kind: 'news-coverage'}}]}}}));
