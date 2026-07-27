/* Fact-check the Robot — app logic (vanilla JS, no dependencies) */
(function () {
  "use strict";

  var CFG = window.CONFIG || {};
  var BACKEND = (CFG.BACKEND_URL || "").trim();
  var BATCH = CFG.BATCH_SIZE || 15;
  var TARGET = CFG.TARGET_VOTES_PER_ITEM || 3;
  var PRI_TARGET = CFG.PRIORITY_VOTES_PER_ITEM || TARGET;
  var LOCK_MS = CFG.MIN_ANSWER_MS || 350;
  var TEST_MODE = BACKEND === "";
  // this script's own ?v= from index.html, reused for items.json so a cached
  // app.js can never be paired with a freshly rebuilt (renumbered) claim set
  var VERSION = (document.currentScript && document.currentScript.src.split("?v=")[1]) || "";

  // ---------- state ----------
  var items = [];          // all items from items.json
  var counts = {};         // itemId -> vote count (from backend)
  var totalVotes = 0;
  var batch = [];          // this round's items
  var idx = 0;             // position within batch
  var shownAt = 0;         // timestamp the current photo appeared
  var sessionAnswered = 0; // across all rounds this visit
  var locked = false;
  var backendDown = false; // backend configured but unreachable

  // ---------- tiny helpers ----------
  function $(id) { return document.getElementById(id); }
  function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function load(k, fallback) {
    try {
      var v = localStorage.getItem(k);
      return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
  }
  function toast(msg, ms) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.classList.add("hidden"); }, ms || 2600);
  }
  function show(screenId) {
    ["screen-intro", "screen-game", "screen-done"].forEach(function (s) {
      $(s).classList.toggle("active", s === screenId);
    });
    window.scrollTo(0, 0);
  }

  // anonymous rater id, persistent per browser
  var rater = load("fc_rater", null);
  if (!rater) {
    rater = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
      : "r" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);
    store("fc_rater", rater);
  }
  var answered = load("fc_answered", []); // item ids this browser already judged

  // ---------- display names ----------
  function displayName(raw) {
    var base = raw.replace(/\d+$/, "");
    return base === "human" ? "person" : base;
  }
  var CAMERA_PREDS = { "to the left of": 1, "to the right of": 1, "in front of": 1, "behind": 1 };
  function hintFor(p) {
    if (p === "in front of") return "camera’s view: the red-boxed thing is closer to the camera, the blue one further away";
    if (p === "behind") return "camera’s view: the red-boxed thing is further from the camera than the blue one";
    if (CAMERA_PREDS[p]) return "from the camera’s point of view";
    if (p === "on" || p === "under") return "physically resting — a held object doesn’t count";
    if (p === "near") return "next to each other, close relative to their size";
    return "";
  }

  // ---------- backend ----------
  function jsonp(url, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var cb = "__fc_cb" + Math.floor(Math.random() * 1e9);
      var script = document.createElement("script");
      // generous: a cold Apps Script can take several seconds, and this only
      // fetches the progress counter — the quiz itself is usable meanwhile
      var timer = setTimeout(function () { cleanup(); reject(new Error("timeout")); }, timeoutMs || 12000);
      function cleanup() {
        clearTimeout(timer);
        delete window[cb];
        if (script.parentNode) script.parentNode.removeChild(script);
      }
      window[cb] = function (data) { cleanup(); resolve(data); };
      script.onerror = function () { cleanup(); reject(new Error("script error")); };
      script.src = url + (url.indexOf("?") >= 0 ? "&" : "?") + "callback=" + cb;
      document.head.appendChild(script);
    });
  }

  function fetchCounts() {
    if (TEST_MODE) {
      var local = load("fc_local_votes", []);
      counts = {};
      totalVotes = local.length;
      local.forEach(function (v) { counts[v.item] = (counts[v.item] || 0) + 1; });
      return Promise.resolve();
    }
    return jsonp(BACKEND + "?fn=counts").then(function (data) {
      counts = (data && data.counts) || {};
      totalVotes = (data && data.total) || 0;
      backendDown = false;
    }).catch(function () { counts = {}; totalVotes = 0; backendDown = true; });
  }

  function sendVote(vote) {
    if (TEST_MODE) {
      var local = load("fc_local_votes", []);
      local.push(vote);
      store("fc_local_votes", local);
      return;
    }
    var queue = load("fc_queue", []);
    queue.push(vote);
    store("fc_queue", queue);
    flushQueue();
  }

  var flushing = false;
  function flushQueue() {
    if (flushing || TEST_MODE) return;
    var queue = load("fc_queue", []);
    if (!queue.length) return;
    flushing = true;
    var sent = queue.length; // only drop what was actually posted; new votes may arrive mid-flight
    fetch(BACKEND, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" }, // simple request: no CORS preflight
      body: JSON.stringify(queue),
      keepalive: true
    }).then(function (r) {
      if (r.ok) store("fc_queue", load("fc_queue", []).slice(sent));
    }).catch(function () { /* keep queue, retry on next answer */ })
      .finally(function () { flushing = false; });
  }

  // ---------- batch selection: furthest below its own target first ----------
  function targetFor(it) { return it.pri ? PRI_TARGET : TARGET; }

  function pickBatch() {
    var done = {};
    answered.forEach(function (id) { done[id] = 1; });
    var pool = items.filter(function (it) { return !done[it.id]; });
    // shuffle for random tie-breaking, then stable-sort by how far each item
    // is below the coverage it needs (priority items need more, so they lead)
    for (var i = pool.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp;
    }
    // Three keys, in order: items still short of their target come first;
    // among those the priority (audit-overlap) items lead, because a partial
    // sample of ordinary claims still gives an unbiased precision estimate
    // whereas a partial priority set makes the crowd-vs-author comparison
    // uncomputable; then least-voted first.
    function rank(it) {
      var c = counts[it.id] || 0;
      return [c >= targetFor(it) ? 1 : 0, it.pri ? 0 : 1, c];
    }
    pool.sort(function (a, b) {
      var ra = rank(a), rb = rank(b);
      return (ra[0] - rb[0]) || (ra[1] - rb[1]) || (ra[2] - rb[2]);
    });
    return pool.slice(0, BATCH);
  }

  // ---------- rendering ----------
  function preload(i) {
    if (i < batch.length) { var im = new Image(); im.src = "img/" + batch[i].img; }
  }

  // ---------- focus view: frame the two objects, toggle to whole scene ----------
  var view = "focus"; // or "full"

  function setBox(el, b) {
    if (!b) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.style.left = (b[0] * 100) + "%";
    el.style.top = (b[1] * 100) + "%";
    el.style.width = ((b[2] - b[0]) * 100) + "%";
    el.style.height = ((b[3] - b[1]) * 100) + "%";
  }

  function focusRect(it) {
    if (!it.sb || !it.ob) return null;
    var x1 = Math.min(it.sb[0], it.ob[0]), y1 = Math.min(it.sb[1], it.ob[1]);
    var x2 = Math.max(it.sb[2], it.ob[2]), y2 = Math.max(it.sb[3], it.ob[3]);
    var side = Math.max(x2 - x1, y2 - y1) + 0.14; // 7% padding each side
    side = Math.min(Math.max(side, 0.32), 1);     // zoom capped at ~3x
    var x = Math.min(Math.max((x1 + x2) / 2 - side / 2, 0), 1 - side);
    var y = Math.min(Math.max((y1 + y2) / 2 - side / 2, 0), 1 - side);
    return { x: x, y: y, side: side };
  }

  function applyView(it) {
    var layer = $("zoom-layer");
    var f = focusRect(it);
    var canFocus = f && f.side < 0.85;
    if (view === "focus" && canFocus) {
      var s = 1 / f.side;
      layer.style.transform = "scale(" + s + ") translate(" + (-f.x * 100) + "%," + (-f.y * 100) + "%)";
      layer.style.setProperty("--bw", (2 / s).toFixed(2) + "px"); // keep outlines crisp when zoomed
      $("zoom-hint").textContent = "tap for whole scene";
      $("img-wrap").classList.remove("full-view");
    } else {
      layer.style.transform = "none";
      layer.style.setProperty("--bw", "2px");
      $("zoom-hint").textContent = canFocus ? "tap to zoom in on the objects" : "";
      $("img-wrap").classList.add("full-view");
    }
  }

  function renderItem() {
    var it = batch[idx];
    view = "focus";
    setBox($("box-s"), it.sb);
    setBox($("box-o"), it.ob);
    applyView(it);
    $("photo").src = "img/" + it.img;
    $("claim").innerHTML =
      'The <span class="chip chip-s">' + displayName(it.s) + "</span> " +
      'is <span class="pred">' + it.p + "</span> " +
      'the <span class="chip chip-o">' + displayName(it.o) + "</span>.";
    $("claim-hint").textContent = hintFor(it.p);
    $("progress-label").textContent = (idx + 1) + " / " + batch.length;
    $("progress-fill").style.width = (100 * idx / batch.length) + "%";
    $("note-input").value = "";
    $("note-box").classList.add("hidden");
    shownAt = Date.now();
    locked = true;
    setButtons(false);
    setTimeout(function () { locked = false; setButtons(true); }, LOCK_MS);
    preload(idx + 1);
    preload(idx + 2);
  }

  function setButtons(enabled) {
    $("btn-true").disabled = !enabled;
    $("btn-false").disabled = !enabled;
  }

  function answer(verdict) {
    if (locked || !batch.length) return;
    locked = true;
    setButtons(false);
    var it = batch[idx];
    var btn = verdict === "y" ? $("btn-true") : $("btn-false");
    btn.classList.remove("pop"); void btn.offsetWidth; btn.classList.add("pop");

    sendVote({
      rater: rater,
      item: it.id,
      verdict: verdict,
      ms: Date.now() - shownAt,
      note: $("note-input").value.trim(),
      batch: sessionAnswered
    });
    answered.push(it.id);
    store("fc_answered", answered);
    counts[it.id] = (counts[it.id] || 0) + 1;
    totalVotes++;
    sessionAnswered++;

    idx++;
    if (idx < batch.length) {
      setTimeout(renderItem, 160);
    } else {
      $("progress-fill").style.width = "100%";
      setTimeout(showDone, 300);
    }
  }

  // ---------- screens ----------
  function communityStats() {
    var covered = 0, goal = 0;
    items.forEach(function (it) {
      var t = targetFor(it);
      goal += t;
      covered += Math.min(counts[it.id] || 0, t);
    });
    return { covered: covered, goal: goal, pct: goal ? Math.round(100 * covered / goal) : 0 };
  }

  function showDone() {
    var mins = sessionAnswered * 11; // ~11s per judgment, just for flavour
    var medal = sessionAnswered >= 45 ? "🥇" : sessionAnswered >= 30 ? "🥈" : "🎉";
    var title = sessionAnswered >= 45 ? "Robot auditor — gold!" :
                sessionAnswered >= 30 ? "Serious fact-checker!" : "Nice work!";
    $("done-emoji").textContent = medal;
    $("done-title").textContent = title;
    $("done-stats").textContent = "You judged " + sessionAnswered + " claim" +
      (sessionAnswered === 1 ? "" : "s") + " — about " +
      Math.max(1, Math.round(mins / 60)) + " min of solo annotation saved. Thank you!";

    var cs = communityStats();
    $("community-fill").style.width = cs.pct + "%";
    // with a 1-per-claim goal "covered" counts claims checked, not raw answers
    var unit = TARGET > 1 ? "judgments collected" : "claims checked";
    $("community-note").textContent = TEST_MODE
      ? "Test mode — answers stored on this device only."
      : "Together: " + cs.pct + "% of the goal (" + cs.covered + " of " + cs.goal + " " + unit + ").";

    var remaining = items.length - answered.length;
    var more = $("btn-more");
    if (remaining <= 0) {
      more.disabled = true;
      more.textContent = "You’ve judged all " + items.length + " photos 🏆";
    } else {
      more.disabled = false;
      more.textContent = "Judge " + Math.min(BATCH, remaining) + " more ▶";
    }
    show("screen-done");
  }

  function startRound() {
    batch = pickBatch();
    if (!batch.length) { showDone(); return; }
    idx = 0;
    show("screen-game");
    renderItem();
  }

  // ---------- events ----------
  $("btn-start").addEventListener("click", startRound);
  $("btn-more").addEventListener("click", function () {
    fetchCounts().then(startRound); // refresh coverage between rounds
  });
  $("btn-true").addEventListener("click", function () { answer("y"); });
  $("btn-false").addEventListener("click", function () { answer("n"); });

  $("btn-share").addEventListener("click", function () {
    var url = location.href.split("#")[0];
    var text = "Can you fact-check a robot? Takes 3 minutes 🤖";
    if (navigator.share) {
      navigator.share({ title: "Fact-check the Robot", text: text, url: url }).catch(function () {});
    } else if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(function () {
        $("share-feedback").textContent = "Link copied — paste it anywhere!";
      });
    }
  });

  $("btn-note").addEventListener("click", function () {
    var box = $("note-box");
    box.classList.toggle("hidden");
    if (!box.classList.contains("hidden")) $("note-input").focus();
  });

  $("btn-help").addEventListener("click", function () { $("help-overlay").classList.remove("hidden"); });
  $("btn-help-close").addEventListener("click", function () { $("help-overlay").classList.add("hidden"); });

  // tap: toggle between the framed objects and the whole scene
  $("img-wrap").addEventListener("click", function () {
    if (!batch.length || idx >= batch.length) return;
    view = view === "focus" ? "full" : "focus";
    applyView(batch[idx]);
  });

  // keyboard shortcuts (desktop)
  document.addEventListener("keydown", function (e) {
    if (!$("screen-game").classList.contains("active")) return;
    if (e.target && e.target.tagName === "INPUT") return;
    var k = e.key.toLowerCase();
    if (k === "y" || k === "t" || k === "arrowright") answer("y");
    else if (k === "n" || k === "f" || k === "arrowleft") answer("n");
    else if (k === "z") $("img-wrap").click();
  });

  // try to flush any unsent votes when leaving
  window.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flushQueue();
  });

  // ---------- boot ----------
  fetch("items.json" + (VERSION ? "?v=" + VERSION : ""))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      items = data;
      return fetchCounts();
    })
    .then(function () {
      flushQueue();
      var cs = communityStats();
      if (backendDown) {
        $("intro-progress").textContent = "⚠️ Could not reach the answer server — votes will be kept in this browser and retried.";
      } else if (!TEST_MODE && totalVotes > 0) {
        $("intro-progress").textContent = totalVotes + " judgments collected so far — " + cs.pct + "% of the goal.";
      } else if (TEST_MODE) {
        $("intro-progress").textContent = "⚠️ Test mode: no backend configured, answers stay on this device.";
      }
    })
    .catch(function () { toast("Could not load the quiz data. Check your connection and refresh."); });
})();
