// ---- deployment config ----
// After deploying the Google Apps Script web app (see README.md),
// paste its URL here, e.g. "https://script.google.com/macros/s/AKfy.../exec".
// While empty, the site runs in TEST MODE: answers stay in this browser only.
window.CONFIG = {
  BACKEND_URL: "https://script.google.com/macros/s/AKfycbzi3O5jNeKeAcvO-JjdjWiEZcCLSpqK5Ag-_ojsmB4HLUo96otHAlEABkaBTH7QzPoo/exec",
  BATCH_SIZE: 15,           // items per round
  // Coverage is stratified. Most claims need one judgment, which is all an
  // aggregate precision estimate requires. The ~150 claims that also carry a
  // verdict from the earlier manual audit need several independent judgments,
  // because those are the ones used to compare crowd against author (Cohen's
  // kappa) and to measure how much raters agree with each other. They are
  // marked "pri" in items.json and served first until they reach their target.
  TARGET_VOTES_PER_ITEM: 1,   // ordinary claims
  PRIORITY_VOTES_PER_ITEM: 3, // audit-overlap claims
  MIN_ANSWER_MS: 350        // buttons locked briefly after each photo (anti double-tap)
};
