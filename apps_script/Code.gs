/**
 * Fact-check the Robot — vote collector.
 *
 * Container-bound script: create a Google Sheet, open Extensions > Apps Script,
 * paste this file, then Deploy > New deployment > Web app with
 *   Execute as: Me
 *   Who has access: Anyone
 * and paste the /exec URL into docs/config.js (BACKEND_URL).
 *
 * Endpoints:
 *   GET  ?fn=counts[&callback=cb]  -> {counts: {itemId: n}, total: n}  (JSONP if callback given)
 *   POST body = JSON vote or array of votes
 *        {rater, item, verdict: 'y'|'n', ms, note, batch}
 */

var SHEET_NAME = 'votes';

function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(['timestamp', 'rater', 'item', 'verdict', 'ms', 'note', 'batch']);
  }
  return sh;
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var votes = Array.isArray(data) ? data : [data];
    if (votes.length > 200) votes = votes.slice(0, 200); // sanity cap

    var rows = votes.map(function (v) {
      return [
        new Date(),
        String(v.rater || '').slice(0, 64),
        Number(v.item) || 0,
        v.verdict === 'y' ? 'y' : 'n',
        Number(v.ms) || 0,
        String(v.note || '').slice(0, 500),
        Number(v.batch) || 0
      ];
    }).filter(function (r) { return r[2] >= 1 && r[2] <= 150; });

    if (rows.length) {
      var lock = LockService.getScriptLock();
      lock.waitLock(10000);
      try {
        var sh = sheet_();
        sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
        CacheService.getScriptCache().remove('counts');
      } finally {
        lock.releaseLock();
      }
    }
    return json_({ ok: true, saved: rows.length });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function doGet(e) {
  var body = JSON.stringify(counts_());
  var cb = e && e.parameter && e.parameter.callback;
  if (cb && /^[\w$.]+$/.test(cb)) {
    return ContentService.createTextOutput(cb + '(' + body + ')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(body)
    .setMimeType(ContentService.MimeType.JSON);
}

function counts_() {
  var cache = CacheService.getScriptCache();
  var hit = cache.get('counts');
  if (hit) return JSON.parse(hit);

  var sh = sheet_();
  var last = sh.getLastRow();
  var counts = {};
  var total = 0;
  if (last > 1) {
    var vals = sh.getRange(2, 3, last - 1, 1).getValues(); // item column
    for (var i = 0; i < vals.length; i++) {
      var id = vals[i][0];
      if (id) {
        counts[id] = (counts[id] || 0) + 1;
        total++;
      }
    }
  }
  var res = { counts: counts, total: total };
  cache.put('counts', JSON.stringify(res), 30); // 30 s cache
  return res;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
