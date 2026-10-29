function renderWeeklyMattermost(report, reportType) {
  if (typeof report !== 'string' || !report.trim()) throw new Error('Missing validated weekly report');
  const source = report.replace(/\r\n?/g, '\n').replace(/@(?!\u200b)/g, '@\u200b').trim();
  const plain = value => value.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]*>/g, '').replace(/^[\s#>*•+-]+/, '').replace(/[\\`*_~]/g, '').replace(/\s+/g, ' ').trim();
  const short = (value, limit) => {
    const chars = Array.from(value);
    return chars.length <= limit ? value : chars.slice(0, limit - 1).join('').replace(/\s+\S*$/, '') + '…';
  };
  const lines = source.split('\n').map(line => line.trim()).filter(Boolean);
  const bullet = lines.find(line => /^(?:[-*•+]\s|\d+[.)]\s)/.test(line));
  const firstFact = bullet || lines.find(line => !/^(?:#{1,6}\s|\*\*[^*]+\*\*$)/.test(line) && !/^(?:battery|homecompute|weekly|system).*review/i.test(plain(line))) || lines[0];
  const evidence = plain(source)
    .replace(/\bno\s+(?:missing|unknown|unavailable|incomplete|insufficient)(?:\s+(?:data|metrics|sources|telemetry|coverage|evidence))?(?:\s+(?:were|was|are|is))?(?:\s+(?:found|reported|detected))?/gi, '')
    .replace(/\bnot\s+(?:missing|unknown|unavailable|incomplete|insufficient)\b/gi, '');
  const limited = /\b(?:unavailable|unknown|missing|incomplete|insufficient|cannot|could not|not available)\b|\bno (?:reliable |current |historical )?(?:data|telemetry|coverage|evidence|metrics)\b/i.test(evidence);
  const label = reportType === 'system_weekly' ? 'Systems review' : 'Battery review';
  const summary = `${limited ? 'Limited evidence · ' : ''}${label}: ${short(plain(firstFact), 130)}\n\n`;
  const links = [];
  const protect = value => value.replace(/\[[^\]\n]+\]\([^\s)]+\)/g, match => `LINKTOKEN${links.push(match) - 1}END`);
  const restore = value => value.replace(/LINKTOKEN(\d+)END/g, (_, index) => links[Number(index)]);
  const reflow = line => {
    if (line.length <= 280 || /^\s*(?:#|```)/.test(line)) return line;
    const list = line.match(/^(\s*(?:[-*•+]|\d+[.)])\s+)([\s\S]*)$/);
    const content = protect(list ? list[2] : line);
    const sentences = content.split(/(?<=[.!?;])\s+/u).filter(Boolean);
    const bounded = sentences.flatMap(sentence => {
      const chunks = [];
      let current = '';
      for (const word of sentence.split(/\s+/)) {
        if (current && current.length + word.length + 1 > 260) { chunks.push(current); current = ''; }
        current += (current ? ' ' : '') + word;
      }
      if (current) chunks.push(current);
      return chunks;
    }).map(restore);
    return list ? list[1] + bounded.join('  \n  ') : bounded.map(sentence => '- ' + sentence).join('\n');
  };
  const body = source.split(/\n{2,}/).map(block => {
    if (/^\s*```/.test(block)) return block;
    return block.split('\n').map(reflow).join('\n');
  }).join('\n\n');
  return summary + body;
}
