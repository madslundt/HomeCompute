import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const root = new URL('../', import.meta.url);
const renderer = (path, name) => vm.runInNewContext(readFileSync(new URL(path, root), 'utf8') + '\n' + name, {Intl});
const news = renderer('automations/semantic-research/news-mattermost.js', 'renderNewsMattermost');
const weekly = renderer('automations/battery-and-system-review/mattermost-report.js', 'renderWeeklyMattermost');
const stories = Array.from({length: 5}, (_, i) => `<b>${i + 1}. Story ${i + 1}</b>\nSourced summary ${i + 1}.\n<i>Comments:</i> Attributed opinion ${i + 1}.\n<a href="https://example.com/story-${i + 1}">Read more</a> · <a href="https://news.ycombinator.com/item?id=${i + 1}">HN discussion</a>`);
const digest = rows => '<b>Hacker News · Top 5 · 29 Oct 2026</b>\n\n' + rows.join('\n\n') + '\n\n<i>Based on HN headlines and sampled comments; opinions attributed to commenters.</i>';
test('news opens with a useful plain preview and preserves all five ordered stories and evidence', () => {
 const text = news(digest(stories));
 assert.equal(text.split('\n')[0], 'Hacker News: Story 1 · 4 more stories.');
 assert.equal((text.match(/^### \d\./gm) || []).length, 5);
 assert.ok(text.includes('- **Story:** Sourced summary 1.\n- **Discussion:** Attributed opinion 1.'));
 assert.ok(text.includes('[Read article](https://example.com/story-5)'));
 assert.ok(text.includes('Based on HN headlines and sampled comments; opinions attributed to commenters.'));
});
test('news treats source markup and mentions as literal text and retains intended links', () => {
 const rows = [...stories];
 rows[0] = rows[0].replace('Story 1', '**Headline** @channel &lt;img&gt;').replace('Sourced summary 1.', '[fake](https://bad.invalid) &amp; _text_.');
 const text = news(digest(rows));
 assert.ok(text.includes('\\*\\*Headline\\*\\* @\u200bchannel \\<img\\>'));
 assert.ok(text.includes('\\[fake\\](https://bad.invalid) & \\_text\\_.'));
 assert.ok(text.split('\n')[0].length <= 145);
 assert.ok(!text.split('\n')[0].includes('**'));
 assert.ok(!text.includes('@channel'));
 assert.ok(news(digest(stories.map((story, index) => index ? story : story.replace('Story 1', 'C# is supported')))).startsWith('Hacker News: C# is supported'));
});
test('news rejects incomplete stories, changed ordering and unsafe source links', () => {
 assert.throws(() => news(digest(stories.slice(1))), /story|five/i);
 assert.throws(() => news(digest([...stories].reverse())), /story/i);
 assert.throws(() => news(digest(stories).replace('https://example.com/story-1', 'javascript:alert(1)')), /source link/i);
});
test('weekly preview leads with a sourced result and flags limited evidence without losing qualifications', () => {
 const report = '**Battery plan weekly review — 2026-10-19 to 2026-10-25**\n\n- Estimated savings: 42 DKK across 4/7 covered days.\n- Grid import/export impact is unknown without a CT meter.';
 const text = weekly(report, 'weekly');
 assert.equal(text.split('\n')[0], 'Limited evidence · Battery review: Estimated savings: 42 DKK across 4/7 covered days.');
 assert.ok(text.endsWith(report));
 assert.ok(!text.includes('0 DKK'));
 assert.match(weekly('### Weekly systems review\n\n- Home Assistant checks passed.\n- Host telemetry unavailable.', 'system_weekly'), /^Limited evidence · Systems review: Home Assistant checks passed\./);
 for (const result of ['No missing metrics were found. All checked services have fresh telemetry.', 'No unavailable sources.', 'Telemetry is not missing.']) {
   assert.ok(!weekly('### Weekly systems review\n\n- ' + result, 'system_weekly').startsWith('Limited evidence'));
 }
});
test('weekly long prose becomes sentence bullets without breaking citations or activating mentions', () => {
 const prose = 'Home Assistant has current telemetry for the checked entities and the observed values remain available. '.repeat(4) + 'The model suggestion needs local benchmark evidence; see [upstream](https://github.com/example/project).';
 const text = weekly('### Systems review\n\n' + prose + '\n\n@channel is source text.', 'system_weekly');
 assert.ok(text.includes('\n- Home Assistant has current telemetry'));
 assert.ok(text.includes('[upstream](https://github.com/example/project)'));
 assert.ok(text.includes('@\u200bchannel'));
 const longBullet = weekly('### Systems review\n\n- Findings: ' + prose, 'system_weekly');
 assert.ok(longBullet.includes('  \n  '));
 assert.throws(() => weekly('', 'weekly'), /Missing validated/);
});
