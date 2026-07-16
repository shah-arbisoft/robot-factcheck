# Fact-check the Robot — crowdsourced blind audit

A tiny web game that distributes the 150-item blind re-audit
(`outputs/audit_blind/`) across many casual volunteers instead of one
exhausted classmate. Each visitor judges a batch of 15 claims
(~3 minutes): photo + claim → **TRUE** / **WRONG or can't tell**.
Batches are assigned least-covered-first, so coverage evens out to the
target of 3 independent judgments per item.

Anonymous by design: a random id per browser, no names/emails/IPs.

```
docs/            the static site (publish this folder)
  index.html     intro + game + end screens
  app.js         batch assignment, vote queue, zoom, keyboard
  config.js      <-- paste your backend URL here
  items.json     the 150 claims (no verdicts, no key)
  img/           the 150 rendered photos
apps_script/
  Code.gs        Google Apps Script backend (votes -> your Google Sheet)
analysis/
  score_votes.py majority verdicts, kappa vs author, alpha, spam filters
```

## Setup (one-time, ~15 minutes)

### 1. Backend (Google Sheet + Apps Script)

1. Create a new Google Sheet (any name, e.g. `audit-votes`).
2. Extensions → Apps Script, delete the stub, paste `apps_script/Code.gs`, save.
3. Deploy → New deployment → type **Web app**:
   - Execute as: **Me**
   - Who has access: **Anyone**
4. Authorize when prompted, copy the **Web app URL** (ends in `/exec`).
5. Paste it into `docs/config.js` as `BACKEND_URL`.

Test: open the URL with `?fn=counts` appended in a browser — you should
see `{"counts":{},"total":0}` and a `votes` sheet appears in the Sheet.

### 2. Hosting (GitHub Pages)

1. Create a **new public repo** (e.g. `robot-factcheck`) — do NOT reuse the
   dissertation repo.
2. Push this folder; in repo Settings → Pages, set source to the `main`
   branch **`/docs` folder**.
3. The site appears at `https://<user>.github.io/robot-factcheck/`.

(Alternative: drag the `docs/` folder onto https://app.netlify.com/drop.)

### 3. Share

Send the link to friends/family/course group chats. The end screen has a
share button. 30 people × 15 judgments ≈ 450 = full 3× coverage.
The intro and end screens show live progress toward the goal.

## Collecting results

1. In the Google Sheet: File → Download → CSV (the `votes` sheet).
2. `python analysis/score_votes.py votes.csv`
   - joins the blind key + the three source audit sheets from the
     dissertation repo (override with `--audit-root`)
   - prints agreement + Cohen's kappa (crowd majority vs author verdicts),
     Krippendorff's alpha (crowd-internal), per-predicate / per-source
     breakdowns, and a per-rater spam table
   - writes `majority_verdicts.csv` + `report.json`
   - useful flags: `--min-ms 800` (drop reflex-speed answers),
     `--exclude-rater <id>` (drop a spammer found in the rater table)

Ties (e.g. 1 y vs 1 n) resolve to `n`, matching the protocol's
"unclear = n" rule; they are counted and flagged in the output.

## Notes

- `_key_do_not_share.csv` and all author verdicts stay OUT of this repo
  and off the site — the site only ever sees claim text and images.
- The images carry the source-audit item number in their caption; raters
  never see the source sheets, so blinding holds.
- Local test mode: while `BACKEND_URL` is empty the site banner says
  "test mode" and answers stay in the browser's localStorage.
- Image credit: *A Spatial Relationship Aware Dataset for Robotics*
  (Wang et al., 2025), CC-BY 4.0. The page is `noindex`.
