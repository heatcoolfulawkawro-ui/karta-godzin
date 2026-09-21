// Karta godzin — backend (Apps Script Web App).
//
// Dwa tryby działania:
//  * LEGACY_OPEN = true  — stary, otwarty magazyn klucz-wartość (GET ?key=, POST {key,value})
//    działa jak dotąd, żeby stare wersje appki na telefonach nie przestały działać w
//    trakcie wdrażania logowania. Tryb przejściowy.
//  * konta: POST {action, token, ...} — logowanie loginem (skrót) + 6-cyfrowym PIN-em,
//    sesja z tokenem, dane osobne per użytkownik, role user/admin, widoczność historii.
//    Gdy LEGACY_OPEN = false, stary tryb jest wyłączony.
//
// PIN-y NIGDY nie są zapisywane — tylko HMAC(PIN; pepper+sól). Pepper leży we właściwościach
// skryptu (poza Arkuszem i repo). Tokeny sesji są zapisywane tylko jako SHA-256.

const LEGACY_OPEN = false; // stary otwarty tryb wyłączony 19.09.2026 (etap 3)
const LEGACY_OWNER = 'PF'; // dane tego użytkownika leżą pod starymi, nieprefiksowanymi kluczami
// SHA-256 jednorazowego klucza konfiguracji (sam klucz nie jest w repo). Akcja bootstrap
// działa tylko przy pustej tabeli Users i tylko z kluczem pasującym do tego skrótu.
const SETUP_KEY_SHA256 = 'eb51f1764184704daa527ded287062475859b31e55ca80607501c0fd9a7da773';

// SHA-256 klucza kopii zapasowej (sam klucz leży tylko na komputerze Szefa, zaszyfrowany
// kontem Windows). Pozwala WYŁĄCZNIE pobrać pełny zrzut danych (akcja backup, tylko odczyt).
const BACKUP_KEY_SHA256 = '405126d49a5b99b58eb12240ce34b3659ca96e12c3bfb3f454beceaa738b3adc';

const USERS_SHEET = 'Users';
const SESSIONS_SHEET = 'Sessions';
const USERS_HEADERS = ['id', 'name', 'role', 'salt', 'hash', 'fails', 'lockUntil', 'visibleMonths', 'active', 'lockCount', 'createdAt', 'canExport', 'canImport', 'startPin'];
const AUDIT_SHEET = 'Audit';
const AUDIT_HEADERS = ['time', 'actor', 'action', 'target', 'detail'];
const SESSIONS_HEADERS = ['tokenHash', 'userId', 'expires', 'createdAt'];
const SESSION_TTL_MS = 30 * 24 * 3600 * 1000;
const MAX_FAILS = 5;
const LOCK_BASE_MS = 5 * 60 * 1000;
const LOCK_MAX_MS = 24 * 3600 * 1000;
const MAX_VALUE_CHARS = 200000;
const TZ = 'Europe/Warsaw';

// ---------- wejścia ----------

function doGet(e) {
  if (!LEGACY_OPEN) return ContentService.createTextOutput('').setMimeType(ContentService.MimeType.JSON);
  const key = e.parameter.key;
  const sheet = getDataSheet();
  const rows = sheet.getDataRange().getValues();
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] === key) {
      return ContentService.createTextOutput(rows[i][1])
        .setMimeType(ContentService.MimeType.JSON);
    }
  }
  return ContentService.createTextOutput('').setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonOut_({ ok: false, error: 'bad' });
  }
  if (body && body.action) return handleAction_(body);
  if (!LEGACY_OPEN) return jsonOut_({ ok: false, error: 'auth' });
  const key = body.key;
  const value = body.value;
  const sheet = getDataSheet();
  const rows = sheet.getDataRange().getValues();
  let found = false;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] === key) {
      sheet.getRange(i + 1, 2).setValue(value);
      found = true;
      break;
    }
  }
  if (!found) {
    sheet.appendRow([key, value]);
  }
  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ---------- akcje kont ----------

function handleAction_(b) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    return jsonOut_(dispatch_(b));
  } catch (err) {
    console.error(err && err.stack || err);
    return jsonOut_({ ok: false, error: 'server' });
  } finally {
    lock.releaseLock();
  }
}

function fail_(code, extra) {
  return Object.assign({ ok: false, error: code }, extra || {});
}

function dispatch_(b) {
  const action = String(b.action);
  if (action === 'login') return login_(b);
  if (action === 'bootstrap') return bootstrap_(b);
  if (action === 'backup') return backup_(b);
  // Przejściowo: eksport bez tokenu dla appki w wersji sprzed logowania.
  if (action === 'export' && LEGACY_OPEN && !b.token) return exportXlsx_(b, null);

  const auth = authenticate_(b.token);
  if (!auth) return fail_('auth');
  const user = auth.user;

  switch (action) {
    case 'me': return { ok: true, user: pub_(user) };
    case 'logout': revokeSessions_(user.id, auth.tokenHash); return { ok: true };
    case 'get': return getData_(user, b);
    case 'set': return setData_(user, b);
    case 'export':
      if (user.role !== 'admin' && !user.canExport) return fail_('forbidden');
      return exportXlsx_(b, user);
    case 'urlopYear': return urlopYear_(user, b);
    case 'search': return search_(user, b);
    case 'changePin': return changePin_(user, b);
  }
  if (action.indexOf('admin.') !== 0) return fail_('bad');
  if (user.role !== 'admin') return fail_('forbidden');
  const res = adminAction_(user, action, b);
  if (res.ok && action !== 'admin.list' && action !== 'admin.get') audit_(user.id, action, b);
  return res;
}

function adminAction_(user, action, b) {
  switch (action) {
    case 'admin.list': return { ok: true, users: readUsers_().map(adminView_) };
    case 'admin.createUser': return adminCreateUser_(b);
    case 'admin.setPin': return adminSetPin_(b);
    case 'admin.setVisibility': return adminSetVisibility_(b);
    case 'admin.setPerms': return adminSetPerms_(b);
    case 'admin.unlock': return adminUnlock_(b);
    case 'admin.setName': return adminSetName_(b);
    case 'admin.setActive': return adminSetActive_(user, b);
    case 'admin.get': return adminGet_(b);
    case 'admin.set': return adminSet_(b);
    case 'admin.backup': return buildDump_();
    case 'admin.backupDrive': return backupToDrive_();
  }
  return fail_('bad');
}

function bootstrap_(b) {
  if (sha256Hex_(String(b.setupKey || '')) !== SETUP_KEY_SHA256) return fail_('auth');
  if (readUsers_().length) return fail_('exists');
  const id = validId_(b.id);
  const pin = validPin_(b.pin);
  if (!id || !pin) return fail_('bad');
  createUser_(id, String(b.name || id), 'admin', pin, 0);
  return { ok: true };
}

function login_(b) {
  const id = validId_(b.user);
  const pin = validPin_(b.pin);
  if (!id || !pin) return fail_('bad');
  const u = findUser_(id);
  if (!u || !u.active) return fail_('bad');
  const now = Date.now();
  if (u.lockUntil > now) return fail_('locked', { retryMs: u.lockUntil - now });
  if (!safeEqual_(hashPin_(pin, u.salt), u.hash)) {
    u.fails += 1;
    if (u.fails >= MAX_FAILS) {
      u.lockCount += 1;
      u.lockUntil = now + Math.min(LOCK_BASE_MS * Math.pow(2, u.lockCount - 1), LOCK_MAX_MS);
      u.fails = 0;
    }
    saveUser_(u);
    return u.lockUntil > now ? fail_('locked', { retryMs: u.lockUntil - now }) : fail_('bad');
  }
  u.fails = 0;
  u.lockCount = 0;
  u.lockUntil = 0;
  saveUser_(u);
  purgeExpiredSessions_();
  const token = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
  const tokenHash = sha256Hex_(token);
  const expires = now + SESSION_TTL_MS;
  getSheet_(SESSIONS_SHEET, SESSIONS_HEADERS).appendRow([tokenHash, u.id, expires, now]);
  return { ok: true, token: token, expires: expires, user: pub_(u) };
}

function changePin_(user, b) {
  const oldPin = validPin_(b.oldPin);
  const newPin = validPin_(b.newPin);
  if (!oldPin || !newPin) return fail_('bad');
  const u = findUser_(user.id);
  if (!safeEqual_(hashPin_(oldPin, u.salt), u.hash)) return fail_('bad');
  setPin_(u, newPin, false);
  return { ok: true };
}

// ---------- dane ----------

function validKey_(k) {
  return typeof k === 'string' && /^[A-Za-z0-9_]{1,64}$/.test(k) ? k : null;
}

function storageKey_(user, key) {
  return user.id === LEGACY_OWNER ? key : user.id + '::' + key;
}

// Ile miesięcy temu jest klucz miesięczny (0 = bieżący, <0 = przyszłość); null gdy to nie klucz miesiąca.
function monthsAgo_(key) {
  const m = /^karta_godzin_v3_(\d{4})_(\d{1,2})$/.exec(key);
  if (!m) return null;
  const now = new Date();
  const cy = Number(Utilities.formatDate(now, TZ, 'yyyy'));
  const cm = Number(Utilities.formatDate(now, TZ, 'M'));
  return (cy * 12 + cm) - (Number(m[1]) * 12 + Number(m[2]));
}

function isHidden_(user, key) {
  if (user.role === 'admin' || !user.visibleMonths) return false;
  const ago = monthsAgo_(key);
  return ago !== null && ago >= user.visibleMonths;
}

function getData_(user, b) {
  const key = validKey_(b.key);
  if (!key) return fail_('bad');
  if (isHidden_(user, key)) return fail_('hidden');
  return { ok: true, value: readRaw_(storageKey_(user, key)) };
}

function setData_(user, b) {
  const key = validKey_(b.key);
  const value = b.value;
  if (!key || typeof value !== 'string' || value.length > MAX_VALUE_CHARS) return fail_('bad');
  if (isHidden_(user, key)) return fail_('hidden');
  writeRaw_(storageKey_(user, key), value);
  return { ok: true };
}

function adminGet_(b) {
  const target = findUser_(validId_(b.id));
  const key = validKey_(b.key);
  if (!target || !key) return fail_('bad');
  return { ok: true, value: readRaw_(storageKey_(target, key)) };
}

function readRaw_(sk) {
  const rows = getDataSheet().getDataRange().getValues();
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] === sk) return String(rows[i][1]);
  }
  return '';
}

function writeRaw_(sk, value) {
  const sheet = getDataSheet();
  const rows = sheet.getDataRange().getValues();
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] === sk) {
      sheet.getRange(i + 1, 2).setValue(value);
      return;
    }
  }
  sheet.appendRow([sk, value]);
}

// ---------- panel administratora ----------

function adminCreateUser_(b) {
  const id = validId_(b.id);
  const pin = validPin_(b.pin);
  const name = String(b.name || '').trim().slice(0, 60);
  const months = validMonths_(b.visibleMonths === undefined ? 0 : b.visibleMonths);
  if (!id || !pin || !name || months === null) return fail_('bad');
  if (findUser_(id)) return fail_('exists');
  createUser_(id, name, 'user', pin, months, true);
  return { ok: true };
}

function adminSetPin_(b) {
  const u = findUser_(validId_(b.id));
  const pin = validPin_(b.pin);
  if (!u || !pin) return fail_('bad');
  setPin_(u, pin, true);
  return { ok: true };
}

function adminSetVisibility_(b) {
  const u = findUser_(validId_(b.id));
  const months = validMonths_(b.months);
  if (!u || months === null) return fail_('bad');
  u.visibleMonths = months;
  saveUser_(u);
  return { ok: true };
}

// Pełny zrzut danych do kopii zapasowej: wszystkie wiersze zakładki Data (dane miesięcy
// wszystkich kont), konta bez hashy i soli (PIN-y w razie odtwarzania resetuje się), dziennik zmian.
// Bez sesji i bez pepperu — kopia nie zawiera niczego, czym da się zalogować.
function backup_(b) {
  if (sha256Hex_(String(b.backupKey || '')) !== BACKUP_KEY_SHA256) return fail_('auth');
  return buildDump_();
}

function buildDump_() {
  const data = getDataSheet().getDataRange().getValues()
    .filter(function (r, i) { return !(i === 0 && r[0] === 'key'); })
    .map(function (r) { return [String(r[0]), String(r[1])]; });
  const users = readUsers_().map(function (u) {
    return {
      id: u.id, name: u.name, role: u.role, visibleMonths: u.visibleMonths, active: u.active,
      canExport: u.canExport, canImport: u.canImport, createdAt: u.createdAt
    };
  });
  const audit = getSheet_(AUDIT_SHEET, AUDIT_HEADERS).getDataRange().getValues().slice(1);
  return {
    ok: true, format: 1,
    generated: Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd'T'HH:mm:ssXXX"),
    counts: { data: data.length, users: users.length, audit: audit.length },
    data: data, users: users, audit: audit
  };
}

// Zapisuje pełny zrzut jako PRYWATNY plik na Dysku właściciela (bez linku publicznego) i zwraca
// link do otwarcia w aplikacji Dysk na telefonie. Dodatkowa kopia po stronie Google.
function backupToDrive_() {
  const dump = buildDump_();
  const folders = DriveApp.getFoldersByName('KG-kopie');
  const folder = folders.hasNext() ? folders.next() : DriveApp.createFolder('KG-kopie');
  const stamp = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd_HHmm');
  const name = 'karta-godzin_' + stamp + '.json';
  const file = folder.createFile(name, JSON.stringify(dump), 'application/json');
  return { ok: true, name: name, url: file.getUrl(), counts: dump.counts };
}

function adminSetPerms_(b) {
  const u = findUser_(validId_(b.id));
  if (!u || typeof b.canExport !== 'boolean' || typeof b.canImport !== 'boolean') return fail_('bad');
  u.canExport = b.canExport;
  u.canImport = b.canImport;
  saveUser_(u);
  return { ok: true };
}

// Admin zapisuje dane miesiąca na koncie serwisanta (np. import z .xlsx w jego imieniu).
function adminSet_(b) {
  const target = findUser_(validId_(b.id));
  const key = validKey_(b.key);
  const value = b.value;
  if (!target || !key || typeof value !== 'string' || value.length > MAX_VALUE_CHARS) return fail_('bad');
  writeRaw_(storageKey_(target, key), value);
  return { ok: true };
}

// Dziennik zmian wykonanych przez admina (bez PIN-ów i bez treści danych).
function audit_(actor, action, b) {
  const detail = {};
  ['key', 'months', 'active', 'canExport', 'canImport', 'role'].forEach(function (f) { if (b[f] !== undefined) detail[f] = b[f]; });
  if (b.name !== undefined && action === 'admin.createUser') detail.name = String(b.name).slice(0, 60);
  getSheet_(AUDIT_SHEET, AUDIT_HEADERS).appendRow([Date.now(), actor, action, String(b.id || ''), JSON.stringify(detail)]);
}

// Wyszukiwarka haseł w opisach wpisów i komentarzach urlopu. Działa po stronie serwera na
// danych TEGO konta i pomija miesiące ukryte przez admina (nic z nich nie wycieka we fragmentach).
// Zakres: months = 0 (cała dostępna historia) albo N = ostatnie N miesięcy z bieżącym. Wszystkie słowa
// zapytania muszą wystąpić w tym samym opisie; wielkość liter i polskie znaki są pomijane.
const SEARCH_MAX_HITS = 300;

function norm_(s) {
  return String(s || '').toLowerCase().replace(/ł/g, 'l').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

function search_(user, b) {
  const words = norm_(String(b.q || '').trim()).split(/\s+/).filter(Boolean).slice(0, 5);
  if (!words.length || words.join(' ').length < 2 || String(b.q).length > 60) return fail_('bad');
  let months = Number(b.months);
  if (!Number.isInteger(months) || months < 0 || months > 240) months = 0;
  const prefix = user.id === LEGACY_OWNER ? '' : user.id + '::';
  const re = /^karta_godzin_v3_(\d{4})_(\d{1,2})$/;
  const rows = getDataSheet().getDataRange().getValues();
  const hits = [];
  let scanned = 0;
  for (let r = 0; r < rows.length; r++) {
    const k = String(rows[r][0]);
    if (prefix ? k.indexOf(prefix) !== 0 : k.indexOf('::') >= 0) continue;
    const m = re.exec(prefix ? k.slice(prefix.length) : k);
    if (!m) continue;
    const key = 'karta_godzin_v3_' + m[1] + '_' + m[2];
    if (isHidden_(user, key)) continue;
    if (months > 0 && monthsAgo_(key) > months - 1) continue;
    let arr;
    try { arr = JSON.parse(rows[r][1]); } catch (e) { continue; }
    if (!Array.isArray(arr)) continue;
    scanned++;
    arr.forEach(function (d, i) {
      if (!d) return;
      const items = d.dayType === 'urlop'
        ? [{ text: d.urlopKomentarz, kind: 'urlop', start: '', end: '' }]
        : (d.blocks || []).map(function (bl) { return { text: bl.komentarz, kind: bl.kind || '', start: bl.start || '', end: bl.end || '' }; });
      items.forEach(function (it) {
        const text = String(it.text || '');
        const n = norm_(text);
        if (!text || !words.every(function (w) { return n.indexOf(w) >= 0; })) return;
        const at = n.indexOf(words[0]);
        const from = Math.max(0, at - 40);
        const snip = (from > 0 ? '…' : '') + text.slice(from, from + 140) + (from + 140 < text.length ? '…' : '');
        const off = from > 0 ? 1 : 0;
        const sn = norm_(snip);
        const marks = [];
        words.forEach(function (w) { let p = sn.indexOf(w); while (p >= 0) { marks.push([p, w.length]); p = sn.indexOf(w, p + w.length); } });
        hits.push({ y: Number(m[1]), m: Number(m[2]), d: i + 1, kind: it.kind, start: it.start, end: it.end, text: snip, marks: marks });
      });
    });
  }
  hits.sort(function (a, b2) { return (b2.y * 10000 + b2.m * 100 + b2.d) - (a.y * 10000 + a.m * 100 + a.d); });
  const truncated = hits.length > SEARCH_MAX_HITS;
  return { ok: true, hits: hits.slice(0, SEARCH_MAX_HITS), total: hits.length, truncated: truncated, scanned: scanned, limited: user.role !== 'admin' && user.visibleMonths > 0 };
}

// Urlopy z całego roku, niezależnie od widoczności miesięcy: liczniki (counts) zawsze
// z pełnych danych, a lista dni (items) tylko z miesięcy widocznych dla użytkownika.
function urlopYear_(user, b) {
  const year = Number(b.year);
  if (!Number.isInteger(year) || year < 2000 || year > 2100) return fail_('bad');
  const rows = getDataSheet().getDataRange().getValues();
  const byKey = {};
  for (let i = 0; i < rows.length; i++) byKey[rows[i][0]] = rows[i][1];
  const counts = [];
  const items = [];
  for (let m = 1; m <= 12; m++) {
    const key = 'karta_godzin_v3_' + year + '_' + m;
    const raw = byKey[storageKey_(user, key)];
    let n = 0;
    if (raw) {
      try {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) {
          const visible = !isHidden_(user, key);
          arr.forEach(function (d, i) {
            if (d && d.dayType === 'urlop') {
              n++;
              if (visible) items.push({ month: m, day: i + 1, komentarz: String(d.urlopKomentarz || '') });
            }
          });
        }
      } catch (e) { /* uszkodzony miesiąc pomijamy */ }
    }
    counts.push(n);
  }
  return { ok: true, counts: counts, items: items };
}

function adminSetName_(b) {
  const u = findUser_(validId_(b.id));
  const name = String(b.name || '').trim().slice(0, 60);
  if (!u || !name) return fail_('bad');
  u.name = name;
  saveUser_(u);
  return { ok: true };
}

function adminUnlock_(b) {
  const u = findUser_(validId_(b.id));
  if (!u) return fail_('bad');
  u.fails = 0;
  u.lockCount = 0;
  u.lockUntil = 0;
  saveUser_(u);
  return { ok: true };
}

function adminSetActive_(admin, b) {
  const u = findUser_(validId_(b.id));
  if (!u) return fail_('bad');
  if (u.id === admin.id) return fail_('self');
  u.active = b.active === true;
  saveUser_(u);
  if (!u.active) revokeSessions_(u.id, null);
  return { ok: true };
}

function validMonths_(m) {
  const n = Number(m);
  return Number.isInteger(n) && n >= 0 && n <= 120 ? n : null;
}

function adminView_(u) {
  return {
    id: u.id, name: u.name, role: u.role, active: u.active,
    visibleMonths: u.visibleMonths, locked: u.lockUntil > Date.now(),
    lockUntil: u.lockUntil, fails: u.fails,
    startPin: decStartPin_(u), pinSetBy: u.startPin ? 'admin' : 'user',
    canExport: u.role === 'admin' || u.canExport, canImport: u.role === 'admin' || u.canImport
  };
}

function pub_(u) {
  return {
    id: u.id, name: u.name, role: u.role, visibleMonths: u.visibleMonths,
    canExport: u.role === 'admin' || u.canExport, canImport: u.role === 'admin' || u.canImport
  };
}

// ---------- użytkownicy i sesje ----------

function validId_(v) {
  const s = String(v || '').trim().toUpperCase();
  return /^[A-Z]{1,6}$/.test(s) ? s : null;
}

function validPin_(v) {
  const s = String(v || '');
  return /^\d{6}$/.test(s) ? s : null;
}

function getPepper_() {
  const props = PropertiesService.getScriptProperties();
  let p = props.getProperty('PEPPER');
  if (!p) {
    p = Utilities.getUuid() + Utilities.getUuid();
    props.setProperty('PEPPER', p);
  }
  return p;
}

// PIN startowy/zresetowany przez admina jest przechowywany odwracalnie (szyfr cyfrowy z kluczem z
// pepperu), żeby admin mógł go przekazać serwisantowi. Kasowany, gdy serwisant ustawi własny PIN.
function pinPad_(id, salt) {
  return Utilities.computeHmacSha256Signature('startpin:' + id + ':' + salt, getPepper_());
}

function encStartPin_(pin, id, salt) {
  const pad = pinPad_(id, salt);
  let out = '';
  for (let i = 0; i < pin.length; i++) out += String((Number(pin[i]) + (pad[i] & 255)) % 10);
  return out;
}

function decStartPin_(u) {
  if (!u.startPin) return '';
  const pad = pinPad_(u.id, u.salt);
  let out = '';
  for (let i = 0; i < u.startPin.length; i++) out += String(((Number(u.startPin[i]) - (pad[i] & 255)) % 10 + 10) % 10);
  return out;
}

function hashPin_(pin, salt) {
  const sig = Utilities.computeHmacSha256Signature(pin, getPepper_() + ':' + salt);
  return Utilities.base64Encode(sig);
}

function sha256Hex_(s) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8)
    .map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

function safeEqual_(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function getSheet_(name, headers) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.appendRow(headers);
  }
  return sheet;
}

function readUsers_() {
  const sheet = getSheet_(USERS_SHEET, USERS_HEADERS);
  const rows = sheet.getDataRange().getValues();
  if (rows[0].length < USERS_HEADERS.length) sheet.getRange(1, 1, 1, USERS_HEADERS.length).setValues([USERS_HEADERS]);
  const out = [];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    if (!r[0]) continue;
    out.push({
      row: i + 1, id: String(r[0]), name: String(r[1]), role: String(r[2]),
      salt: String(r[3]), hash: String(r[4]), fails: Number(r[5]) || 0,
      lockUntil: Number(r[6]) || 0, visibleMonths: Number(r[7]) || 0,
      active: r[8] === true || r[8] === 'TRUE', lockCount: Number(r[9]) || 0,
      createdAt: r[10],
      canExport: r[11] === true || r[11] === 'TRUE',
      canImport: r[12] === true || r[12] === 'TRUE',
      startPin: String(r[13] || '')
    });
  }
  return out;
}

function findUser_(id) {
  if (!id) return null;
  const list = readUsers_();
  for (let i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
  return null;
}

function userRow_(u) {
  return [u.id, u.name, u.role, u.salt, u.hash, u.fails, u.lockUntil, u.visibleMonths, u.active, u.lockCount, u.createdAt, u.canExport === true, u.canImport === true, u.startPin || ''];
}

function saveUser_(u) {
  getSheet_(USERS_SHEET, USERS_HEADERS).getRange(u.row, 1, 1, USERS_HEADERS.length).setValues([userRow_(u)]);
}

function createUser_(id, name, role, pin, visibleMonths, startPinVisible) {
  const salt = Utilities.getUuid();
  const u = {
    id: id, name: name, role: role, salt: salt, hash: hashPin_(pin, salt), fails: 0,
    lockUntil: 0, visibleMonths: visibleMonths, active: true, lockCount: 0, createdAt: Date.now(),
    canExport: false, canImport: false, startPin: ''
  };
  if (startPinVisible) u.startPin = encStartPin_(pin, id, salt);
  getSheet_(USERS_SHEET, USERS_HEADERS).appendRow(userRow_(u));
}

function setPin_(u, pin, fromAdmin) {
  u.salt = Utilities.getUuid();
  u.hash = hashPin_(pin, u.salt);
  u.startPin = fromAdmin ? encStartPin_(pin, u.id, u.salt) : '';
  u.fails = 0;
  u.lockCount = 0;
  u.lockUntil = 0;
  saveUser_(u);
  revokeSessions_(u.id, null);
}

function authenticate_(token) {
  if (typeof token !== 'string' || token.length < 32 || token.length > 128) return null;
  const tokenHash = sha256Hex_(token);
  const rows = getSheet_(SESSIONS_SHEET, SESSIONS_HEADERS).getDataRange().getValues();
  const now = Date.now();
  for (let i = 1; i < rows.length; i++) {
    if (rows[i][0] === tokenHash) {
      if (Number(rows[i][2]) < now) return null;
      const user = findUser_(String(rows[i][1]));
      if (!user || !user.active) return null;
      return { user: user, tokenHash: tokenHash };
    }
  }
  return null;
}

// Usuwa sesje: wszystkie sesje użytkownika (onlyHash = null) albo tylko jedną (wylogowanie).
function revokeSessions_(userId, onlyHash) {
  const sheet = getSheet_(SESSIONS_SHEET, SESSIONS_HEADERS);
  const rows = sheet.getDataRange().getValues();
  for (let i = rows.length - 1; i >= 1; i--) {
    const match = onlyHash ? rows[i][0] === onlyHash : String(rows[i][1]) === userId;
    if (match) sheet.deleteRow(i + 1);
  }
}

function purgeExpiredSessions_() {
  const sheet = getSheet_(SESSIONS_SHEET, SESSIONS_HEADERS);
  const rows = sheet.getDataRange().getValues();
  const now = Date.now();
  for (let i = rows.length - 1; i >= 1; i--) {
    if (Number(rows[i][2]) < now) sheet.deleteRow(i + 1);
  }
}

// ---------- eksport .xlsx ----------
// Appka wysyła gotowy plik (base64), tu zapisujemy go na Dysku pod właściwą nazwą i
// zwracamy zwykły link do pobrania (Content-Disposition z nazwą i typem pliku — działa
// w Chrome i Safari na iOS, w przeciwieństwie do blob:/data:). Pliki są tymczasowe:
// każdy eksport sprząta te starsze niż 15 min.
const EXPORT_FOLDER = 'KG-eksport-tmp';
const EXPORT_MAX_AGE_MS = 15 * 60 * 1000;
const EXPORT_MAX_B64 = 1500000;
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function exportXlsx_(body, user) {
  const name = String(body.name || '');
  const b64 = String(body.b64 || '');
  if (!/^\d{1,2}_\d{2}_[A-Za-z]{1,6}\.xlsx$/.test(name)) return fail_('bad name');
  const owner = user && user.role === 'admin' && body.forId ? validId_(body.forId) : (user && user.id);
  if (user && (!owner || name.slice(-(owner.length + 6)).toUpperCase() !== '_' + owner + '.XLSX')) return fail_('bad name');
  if (!b64 || b64.length > EXPORT_MAX_B64 || b64.indexOf('UEsDB') !== 0) return fail_('bad file');
  const folders = DriveApp.getFoldersByName(EXPORT_FOLDER);
  const folder = folders.hasNext() ? folders.next() : DriveApp.createFolder(EXPORT_FOLDER);
  const now = Date.now();
  const old = folder.getFiles();
  while (old.hasNext()) {
    const f = old.next();
    if (now - f.getDateCreated().getTime() > EXPORT_MAX_AGE_MS) f.setTrashed(true);
  }
  const file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(b64), XLSX_MIME, name));
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  return { ok: true, url: 'https://drive.google.com/uc?export=download&id=' + file.getId() };
}

function jsonOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// Uruchamiana jednorazowo ręcznie w edytorze, żeby właściciel przyznał uprawnienie do Dysku.
function authorizeDrive() {
  DriveApp.getRootFolder();
}

function getDataSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName('Data');
  if (!sheet) {
    sheet = ss.insertSheet('Data');
    sheet.appendRow(['key', 'value']);
  }
  return sheet;
}
