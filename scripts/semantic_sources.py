"""Bounded source projections for separate school and public-evidence indexes."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from typing import Any


def identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.hidden += 1
        if tag in {'p', 'div', 'li', 'br'}: self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}: self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'div', 'li'}: self.parts.append('\n')

    def handle_data(self, value):
        if not self.hidden: self.parts.append(value)


TEXT_KEYS = ('title', 'subject', 'headline', 'text', 'content', 'body', 'description',
             'summary', 'message', 'plain', 'html')
DATE_KEYS = ('publishedAt', 'publishedDate', 'published_date', 'publishAt', 'createdAt',
             'created_at', 'sendDateTime', 'date', 'updatedAt', 'updated_at')


def source_text(value: Any) -> str:
    """Only extract displayed text; raw credentials/attachments never enter here."""
    if isinstance(value, str):
        parser = PlainText(); parser.feed(value); parser.close()
        return '\n'.join(line for raw in ''.join(parser.parts).splitlines()
                         if (line := ' '.join(raw.split())))
    if not isinstance(value, dict): return ''
    keys = ('plain',) if isinstance(value.get('plain'), str) else TEXT_KEYS
    parts = [source_text(value[key]) for key in keys if key in value]
    return '\n'.join(dict.fromkeys(part for part in parts if part))


def school_snapshots(operation: str, values: dict, payload: Any, captured_at: str) -> list[tuple[str, list[dict]]]:
    """Partial pages refresh identified records without deleting missing history."""
    scope=dict(values)
    if operation=='school_weekly_letter': scope.setdefault('offset','0')
    for key in ('profile_ids','child_ids','institution_codes'):
        if key in scope: scope[key]=','.join(sorted(scope[key].split(',')))
    source_id = operation + ':' + identity(scope)
    if operation == 'school_posts':
        rows = payload if isinstance(payload, list) else payload.get('posts', payload.get('items', [])) if isinstance(payload, dict) else []
    elif operation == 'school_thread':
        rows = payload.get('messages', []) if isinstance(payload, dict) else []
        if not rows and isinstance(payload, dict) and source_text(payload): rows = [payload]
    else:
        rows = payload.get('letters', []) if isinstance(payload, dict) else []
    snapshots: dict[str, list[dict]] = {source_id: []}
    for index, row in enumerate(rows[:20]):
        if not isinstance(row, dict): continue
        upstream_id = next((str(row[key]) for key in ('messageId', 'postId', 'id')
                            if type(row.get(key)) in {str, int} and str(row[key])), None)
        identified = operation in {'school_posts', 'school_thread'} and upstream_id is not None
        record_source = source_id + ':record:' + identity(upstream_id) if identified else source_id
        text = source_text(row)
        if not text.strip(): continue
        subject = payload.get('subject') if operation == 'school_thread' and isinstance(payload, dict) else None
        if subject and isinstance(subject, str): text = source_text(subject)[:300] + '\n' + text
        records = snapshots.setdefault(record_source, [])
        stamp = next((row[key] for key in DATE_KEYS if isinstance(row.get(key), (str, int))), None)
        metadata = {'principal_scope': 'owner', 'data_domain': 'personal', 'source': 'aula',
                    'source_tool': 'homecompute_' + operation, 'source_scope': dict(values),
                    'captured_at': captured_at, 'source_timestamp': str(stamp)[:100] if stamp is not None else None}
        if operation == 'school_weekly_letter':
            metadata.update({key: row[key] for key in ('childName', 'subject', 'week_number', 'chunk_index', 'chunk_count') if key in row})
        metadata['source_record_id'] = upstream_id if upstream_id is not None else str(index)
        for chunk, start in enumerate(range(0, min(len(text), 12000), 1800)):
            records.append({'id': identity([record_source, upstream_id if identified else index, chunk]),
                            'text': text[start:start+1800],
                            'metadata': {**metadata, 'passage_index': chunk}})
    return list(snapshots.items())


def public_record(row: dict, captured_at: str) -> dict:
    return {'id': identity(row['url']), 'text': row['title'] + '\n' + row['snippet'],
            'metadata': {'source': 'tavily', 'url': row['url'], 'title': row['title'],
                         'published_date': row['published_date'], 'captured_at': captured_at,
                         'data_domain': 'public'}}


def rerank_public(ranker: Any, query: str, rows: list[dict]) -> list[dict]:
    ranked=ranker.rank(query,rows,task='research',text_key='snippet',limit=5)
    # A reranker outage or no lexical overlap cannot erase valid live evidence.
    returned={row['url'] for row in ranked}
    return (ranked + [{**row,'retrieval_score':None,'lexical_score':None,'semantic_score':None}
                      for row in rows if row['url'] not in returned])[:5]


def bounded_hits(rows: list[dict], budget: int = 19000) -> list[dict]:
    """Keep provenance and scores while fitting the facade's whole-response cap."""
    results = []
    for row in rows[:10]:
        item = {key: row[key] for key in ('id', 'text', 'metadata', 'retrieval_score',
                                         'lexical_score', 'semantic_score') if key in row}
        if not isinstance(item.get('text'), str): continue
        item['text'] = item['text'][:1800]
        size = len(json.dumps(item, allow_nan=False).encode()) + 2
        while size > budget and len(item['text']) > 100:
            item['text'] = item['text'][:len(item['text']) // 2]
            size = len(json.dumps(item, allow_nan=False).encode()) + 2
        if size > budget: break
        results.append(item); budget -= size
    return results
