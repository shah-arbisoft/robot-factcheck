// ---- deployment config ----
// After deploying the Google Apps Script web app (see README.md),
// paste its URL here, e.g. "https://script.google.com/macros/s/AKfy.../exec".
// While empty, the site runs in TEST MODE: answers stay in this browser only.
window.CONFIG = {
  BACKEND_URL: "https://script.google.com/macros/s/AKfycbzi3O5jNeKeAcvO-JjdjWiEZcCLSpqK5Ag-_ojsmB4HLUo96otHAlEABkaBTH7QzPoo/exec",
  BATCH_SIZE: 15,           // items per round
  TARGET_VOTES_PER_ITEM: 1, // community goal: judgments per claim (1 = one full pass, ~2000 answers)
  MIN_ANSWER_MS: 350        // buttons locked briefly after each photo (anti double-tap)
};
