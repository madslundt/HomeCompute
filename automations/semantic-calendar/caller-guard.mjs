// Keep review-only calendar receipts from acknowledging the original source.
export const CALENDAR_RECEIPT_GUARD = `const receipt = $input.item.json;
if (item.routeIndex < 5 && (receipt.ok !== true || receipt.review_required === true || receipt.action === 'review')) {
  throw new Error('Calendar intake needs review; source remains unprocessed');
}
`;

export function guardedCalendarAcknowledgment(source) {
  if (typeof source !== 'string') throw new Error('Existing calendar acknowledgment source required');
  if (source.includes(CALENDAR_RECEIPT_GUARD)) return source;
  const marker = "const item = $('Validate and classify event').item.json;\n";
  if (source.split(marker).length !== 2 || !source.includes("const state = $getWorkflowStaticData('global');")) {
    throw new Error('Calendar acknowledgment format changed; review before patching');
  }
  return source.replace(marker, marker + CALENDAR_RECEIPT_GUARD);
}
