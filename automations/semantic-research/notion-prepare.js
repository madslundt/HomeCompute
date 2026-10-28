const page = $('Filter').item.json;
const source_id = pageSource(page.id);
const context = String($json.context ?? $json.markdown ?? '');
return {json: {context, source_id, retrievalBody: {profile: 'research', operation: 'search', source_id,
  query: queryText([page.property_ai_prompt, page.property_success_criteria].filter(Boolean).join('\n')) || 'Previous confirmed research and preferences', limit: 6}}};
