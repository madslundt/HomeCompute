const configured = false;
const source = __MAIL_SOURCE__;
const body = $json.body;
if (!configured || !body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).sort().join() !== 'arguments,operation') throw new Error('Mail request refused');
const {operation, arguments: args} = body;
if (!['search','thread'].includes(operation) || !args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Mail operation refused');
const keys = operation === 'search' ? ['account','query'] : ['account','thread_id'];
if (Object.keys(args).sort().join() !== keys.sort().join() || args.account !== source.alias) throw new Error('Mail account refused');
const text = (value, limit) => typeof value === 'string' && value.length >= 1 && value.length <= limit && value === value.trim() && !/[\x00-\x1f]/.test(value);
if (operation === 'search' && !text(args.query, 200)) throw new Error('Mail search refused');
if (operation === 'thread' && (!text(args.thread_id, 1024) || !/^[A-Za-z0-9_+=/-]+$/.test(args.thread_id))) throw new Error('Mail thread refused');
const enc = encodeURIComponent;
let url;
const headers = {};
if (source.provider === 'gmail') {
  const base = 'https://gmail.googleapis.com/gmail/v1/users/me/threads';
  if (operation === 'search') url = base + '?maxResults=10&includeSpamTrash=false&q=' + enc(args.query) + '&fields=threads(id,snippet),nextPageToken';
  else {
    if (!/^[a-fA-F0-9]{1,64}$/.test(args.thread_id)) throw new Error('Gmail thread refused');
    const fields = 'id,messages(id,threadId,snippet,internalDate,payload(mimeType,headers,body(data,size),parts(mimeType,body(data,size),parts(mimeType,body(data,size)))))';
    url = base + '/' + enc(args.thread_id) + '?format=full&fields=' + enc(fields);
  }
} else {
  const base = 'https://graph.microsoft.com/v1.0/me/messages';
  const fields = 'id,conversationId,subject,from,receivedDateTime,bodyPreview' + (operation === 'thread' ? ',body' : '');
  headers.Prefer = 'outlook.body-content-type="text"';
  if (operation === 'search') url = base + '?$top=10&$select=' + fields + '&$search=' + enc('"' + args.query.replace(/\\/g,'\\\\').replace(/"/g,'\\"') + '"');
  else url = base + '?$top=10&$select=' + fields + '&$filter=' + enc("conversationId eq '" + args.thread_id.replace(/'/g,"''") + "'");
}
return [{json:{operation, account:args.account, provider:source.provider, thread_id:args.thread_id || null, request:{url, headers}}}];
