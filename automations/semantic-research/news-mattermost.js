function renderNewsMattermost(html) {
  const decode = value => String(value).replace(/&(amp|lt|gt|quot|apos|#39);/g,
    (whole, key) => ({amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", '#39': "'"}[key]));
  const neutral = value => decode(value).replace(/@(?!\u200b)/g, '@\u200b');
  const markdown = value => neutral(value).replace(/([\\`*_[\]~<>#|])/g, '\\$1');
  const short = (value, limit) => {
    const characters = Array.from(value.replace(/\s+/g, ' ').trim());
    return characters.length <= limit ? characters.join('') : characters.slice(0, limit - 1).join('').replace(/\s+\S*$/, '') + '…';
  };
  const chunks = String(html ?? '').trim().split(/\n\n+/);
  const title = chunks.shift()?.match(/^<b>([^\n]+)<\/b>$/)?.[1];
  if (!title || !title.startsWith('Hacker News · Top 5 · ')) throw new Error('Unrecognized validated news heading');
  const note = chunks.pop();
  if (!note || !/^<i>[^<]+<\/i>$/.test(note)) throw new Error('Missing news evidence qualification');
  const stories = chunks.map((chunk, index) => {
    const match = chunk.match(/^<b>(\d+)\. ([\s\S]*?)<\/b>\n([\s\S]*?)\n<i>Comments:<\/i> ([\s\S]*?)\n<a href="([^"]+)">Read more<\/a> · <a href="([^"]+)">HN discussion<\/a>$/);
    if (!match || Number(match[1]) !== index + 1) throw new Error('Unrecognized validated news story');
    for (const url of match.slice(5)) {
      if (!/^https?:\/\//i.test(decode(url)) || /[\s<>\\]/.test(decode(url))) throw new Error('Unsafe news source link');
    }
    const link = url => decode(url).replace(/\(/g, '%28').replace(/\)/g, '%29');
    return {headline: match[2], body: `### ${index + 1}. ${markdown(match[2])}\n\n` +
      `- **Story:** ${markdown(match[3])}\n- **Discussion:** ${markdown(match[4])}\n\n` +
      `[Read article](${link(match[5])}) · [HN discussion](${link(match[6])})`};
  });
  if (stories.length !== 5) throw new Error('Mattermost news requires exactly five validated stories');
  const headline = neutral(stories[0].headline).replace(/[\\`*_~<>\[\]|]/g, '');
  const preview = `Hacker News: ${short(headline, 100)} · 4 more stories.`;
  return preview + '\n\n## ' + markdown(title) + '\n\n' + stories.map(story => story.body).join('\n\n') +
    '\n\n*' + markdown(note.slice(3, -4)) + '*';
}
