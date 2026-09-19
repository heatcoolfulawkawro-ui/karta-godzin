function doGet(e) {
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
  const body = JSON.parse(e.postData.contents);
  if (body.action === 'export') return exportXlsx_(body);
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

// Eksport .xlsx: appka wysyła gotowy plik (base64), tu zapisujemy go na Dysku
// pod właściwą nazwą i zwracamy zwykły link do pobrania (Content-Disposition z
// nazwą i typem pliku — działa w Chrome i Safari na iOS, w przeciwieństwie do
// blob:/data:). Pliki są tymczasowe: każdy eksport sprząta te starsze niż 15 min.
const EXPORT_FOLDER = 'KG-eksport-tmp';
const EXPORT_MAX_AGE_MS = 15 * 60 * 1000;
const EXPORT_MAX_B64 = 1500000;
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function exportXlsx_(body) {
  const name = String(body.name || '');
  const b64 = String(body.b64 || '');
  if (!/^\d{1,2}_\d{2}_[A-Za-z]{1,6}\.xlsx$/.test(name)) return jsonOut_({ ok: false, error: 'bad name' });
  if (!b64 || b64.length > EXPORT_MAX_B64 || b64.indexOf('UEsDB') !== 0) return jsonOut_({ ok: false, error: 'bad file' });
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
  return jsonOut_({ ok: true, url: 'https://drive.google.com/uc?export=download&id=' + file.getId() });
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