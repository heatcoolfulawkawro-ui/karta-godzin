// Serwer testowy v1.3: podaje appkę z GAS_URL -> /gas, a /gas obsługuje PRAWDZIWY kod Kod.gs
// uruchomiony na atrapie Arkusza w pamięci (dane testowe, nic nie dotyka prawdziwego Arkusza).
// Użycie: node sandbox-auth.js index.html Kod.gs [port]
const http = require('http'), fs = require('fs'), vm = require('vm'), crypto = require('crypto');
const [htmlFile, kodFile, portArg] = process.argv.slice(2);
const port = Number(portArg || 4180);
const SETUP = 'sandbox-setup-key';

function mkSheet(headers) {
  const rows = headers ? [headers.slice()] : [];
  return {
    rows,
    appendRow: r => { rows.push(r.slice()); },
    getDataRange: () => ({ getValues: () => rows.map(r => r.slice()) }),
    getRange: (row, col, nr = 1, nc = 1) => ({
      setValue: v => { rows[row - 1][col - 1] = v; },
      setValues: vs => { for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) rows[row - 1 + i][col - 1 + j] = vs[i][j]; }
    }),
    deleteRow: n => { rows.splice(n - 1, 1); }
  };
}
const sheets = { Data: mkSheet(['key', 'value']) };
const props = {};
const files = [];
const code = fs.readFileSync(kodFile, 'utf8')
  .replace(/const SETUP_KEY_SHA256 = '[0-9a-f]+';/, () => `const SETUP_KEY_SHA256 = '${crypto.createHash('sha256').update(SETUP).digest('hex')}';`);
const sb = {
  console,
  SpreadsheetApp: { getActiveSpreadsheet: () => ({ getSheetByName: n => sheets[n] || null, insertSheet: n => (sheets[n] = mkSheet(null)) }) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] || null, setProperty: (k, v) => { props[k] = v; } }) },
  LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
  ContentService: { MimeType: { JSON: 'json' }, createTextOutput: t => ({ text: t, setMimeType() { return this; } }) },
  Utilities: {
    getUuid: () => crypto.randomUUID(),
    computeHmacSha256Signature: (msg, key) => Array.from(crypto.createHmac('sha256', key).update(msg).digest()).map(b => (b > 127 ? b - 256 : b)),
    base64Encode: bytes => Buffer.from(bytes.map(b => b & 255)).toString('base64'),
    base64Decode: s => Array.from(Buffer.from(s, 'base64')).map(b => (b > 127 ? b - 256 : b)),
    newBlob: (bytes, mime, name) => ({ bytes, mime, name }),
    computeDigest: (alg, s) => Array.from(crypto.createHash('sha256').update(s, 'utf8').digest()).map(b => (b > 127 ? b - 256 : b)),
    DigestAlgorithm: { SHA_256: 1 }, Charset: { UTF_8: 1 },
    formatDate: (d, tz, fmt) => {
      const p = new Intl.DateTimeFormat('en-GB', { timeZone: tz, year: 'numeric', month: 'numeric', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d).reduce((o, x) => (o[x.type] = x.value, o), {});
      if (fmt === 'yyyy') return p.year; if (fmt === 'M') return String(Number(p.month));
      return `${p.year}-${String(p.month).padStart(2, '0')}-${p.day}_${p.hour}${p.minute}`;
    }
  },
  DriveApp: {
    Access: { ANYONE_WITH_LINK: 1 }, Permission: { VIEW: 1 },
    getFoldersByName: () => ({ hasNext: () => false }),
    createFolder: () => ({
      getFiles: () => ({ hasNext: () => false }),
      createFile: (a, b, c) => { const f = { a: typeof a === 'string' ? a : a.name }; files.push(f); return { setSharing() {}, getId: () => 'FILE' + files.length, getUrl: () => 'about:blank#drive-' + f.a }; }
    })
  }
};
vm.createContext(sb);
vm.runInContext(code, sb);
const post = obj => JSON.parse(sb.doPost({ postData: { contents: JSON.stringify(obj) } }).text);

// ---- dane testowe ----
const day = (dayType, blocks, extra) => Object.assign({ dayType, urlopKomentarz: '', nocna: false, blocks }, extra || {});
const blk = (start, end, kind, komentarz) => ({ start, end, kind, komentarz: komentarz || '', gapAfter: 'dojazd' });
function month(y, m, fill) {
  const n = new Date(y, m, 0).getDate(), arr = [];
  for (let d = 1; d <= n; d++) arr.push(fill(d) || day('praca', [blk('', '', 'obiekt')]));
  return JSON.stringify(arr);
}
post({ action: 'bootstrap', setupKey: SETUP, id: 'PF', name: 'Paweł Fuławka', pin: '000111' });
const A = post({ action: 'login', user: 'PF', pin: '000111' }).token;
post({ action: 'admin.createUser', token: A, id: 'PS', name: 'Piotr S', pin: '000222' });
const wk = (y, m, d) => { const w = new Date(y, m - 1, d).getDay(); return w === 0 || w === 6; };
function ordinary(y, m) {
  return month(y, m, d => wk(y, m, d) ? null : (d % 9 === 0 ? day('urlop', [blk('', '', 'obiekt')], { urlopKomentarz: 'urlop testowy' })
    : day('praca', [blk('07:00', '11:00', 'biuro', 'biuro'), blk('11:30', '16:00', 'obiekt', 'montaż')])));
}
for (const m of [5, 6, 7, 8, 9]) post({ action: 'admin.set', token: A, id: 'PF', key: `karta_godzin_v3_2026_${m}`, value: ordinary(2026, m) });
// PF (admin) zapisuje swoje dane bez prefiksu — przez zwykły set
for (const m of [5, 6, 7, 8, 9]) post({ action: 'set', token: A, key: `karta_godzin_v3_2026_${m}`, value: ordinary(2026, m) });
const ps = post({ action: 'login', user: 'PS', pin: '000222' }).token;
for (const m of [6, 7, 8, 9]) post({ action: 'set', token: ps, key: `karta_godzin_v3_2026_${m}`, value: ordinary(2026, m) });
console.log('dane testowe gotowe: PF (V-IX), PS (VI-IX)');

http.createServer((req, res) => {
  if (req.url.startsWith('/gas')) {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        if (req.method === 'POST') {
          const out = sb.doPost({ postData: { contents: body } }).text;
          res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(out);
        } else {
          const u = new URL(req.url, 'http://x');
          res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(sb.doGet({ parameter: { key: u.searchParams.get('key') } }).text);
        }
      } catch (e) { res.writeHead(500); res.end(String(e)); }
    });
    return;
  }
  if (req.url === '/__sheet') { res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end(JSON.stringify({ users: sheets.Users.rows.map(r => r.slice(0, 3).concat([r[7], r[11], r[12], r[13]])), audit: sheets.Audit ? sheets.Audit.rows : [] })); }
  let html = fs.readFileSync(htmlFile, 'utf8');
  const n = (html.match(/const GAS_URL = '[^']*';/g) || []).length;
  if (n !== 1) { res.writeHead(500); return res.end('GAS_URL x' + n); }
  html = html.replace(/const GAS_URL = '[^']*';/, "const GAS_URL = '/gas';");
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(html);
}).listen(port, () => console.log(`sandbox v1.3: http://localhost:${port}/  (backend = Kod.gs w pamięci)`));
