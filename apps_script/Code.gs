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
var HEADERS = ['timestamp', 'rater', 'item', 'verdict', 'ms', 'note', 'batch', 'vid'];
var VID_COL = 8; // 1-indexed column holding the vote id

function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(HEADERS);
    return sh;
  }
  // Add the vid header in place on an existing sheet, so deploying this needs
  // no migration and the rows already collected stay exactly as they are.
  if (sh.getLastColumn() < VID_COL) sh.getRange(1, VID_COL).setValue('vid');
  return sh;
}

/**
 * Vote ids already written.
 *
 * A client whose flush response is lost -- tab hidden mid-send, or the network
 * dropping after the request was delivered -- keeps its queue and re-sends
 * votes this sheet already holds. It cannot tell that case from a real
 * failure, so the duplicate has to be rejected here. The 11 August export held
 * 12 such rows, identical down to the millisecond.
 */
function seenVids_(sh) {
  var last = sh.getLastRow();
  var seen = {};
  if (last < 2) return seen;
  var vals = sh.getRange(2, VID_COL, last - 1, 1).getValues();
  for (var i = 0; i < vals.length; i++) {
    if (vals[i][0]) seen[String(vals[i][0])] = true;
  }
  return seen;
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
        Number(v.batch) || 0,
        String(v.vid || '').slice(0, 64)
      ];
    }).filter(function (r) { return r[2] >= 1 && r[2] <= 5000; });

    var saved = 0, skipped = 0;
    if (rows.length) {
      var lock = LockService.getScriptLock();
      lock.waitLock(10000);
      try {
        var sh = sheet_();
        // Deduplicate inside the lock, against the sheet and against this
        // payload, so two concurrent posts of the same queue cannot both win.
        var seen = seenVids_(sh);
        var fresh = [];
        for (var i = 0; i < rows.length; i++) {
          var vid = rows[i][VID_COL - 1];
          if (vid && seen[vid]) { skipped++; continue; }
          if (vid) seen[vid] = true;
          fresh.push(rows[i]);
        }
        if (fresh.length) {
          sh.getRange(sh.getLastRow() + 1, 1, fresh.length, fresh[0].length)
            .setValues(fresh);
          CacheService.getScriptCache().remove('counts');
        }
        saved = fresh.length;
      } finally {
        lock.releaseLock();
      }
    }
    // A retry of an already-written batch reports ok, so the client clears its
    // queue instead of retrying for ever.
    return json_({ ok: true, saved: saved, skipped: skipped });
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
