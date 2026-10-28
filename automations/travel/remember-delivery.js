const receipt=$input.first().json;
if (typeof receipt.id!=='string' || !/^[a-z0-9]{26}$/.test(receipt.id) || receipt.channel_id!=='wxrgwf839tddxyrunhbo8ernqr') throw new Error('Travel delivery was not confirmed');
const digest=$('Select new bargains').first().json;
const state=$getWorkflowStaticData('global');
state.sent=state.sent||{};
for (const offer of digest.deals) state.sent[offer.key]={price:offer.total,allInclusiveTotalDKK:offer.allInclusive?.totalDKK??null,
  returnDay:offer.returnDay,sentAt:digest.checkedAt};
const today=digest.checkedAt.slice(0,10);
for (const [key,value] of Object.entries(state.sent)) if (value.returnDay<today) delete state.sent[key];
return [{json:{delivered:true,channel:'Travel',postId:receipt.id,dealCount:digest.deals.length}}];
