# Fact-check the Robot — crowdsourced validation

A tiny web game that crowd-validates the automatic annotator's labels. The
pool is a stratified sample of ~2000 claims (286 per predicate) drawn from the
tool's predictions on object pairs the human annotators never labelled — the
part with no ground truth. Each visitor judges a batch of 15 claims
(~3 minutes): photo + claim → **TRUE** / **WRONG or can't tell**.
Batches are assigned least-covered-first, so coverage evens out.

The 150 author-verdicted audit items are kept as a subset, so the crowd result
also gives an author-bias (kappa) check.

Anonymous by design: a random id per browser, no names/emails/IPs. Faces of
people in the photos are pixelated before publication.

## Rebuilding the item set

    D:\uni_project\.venv\Scripts\python.exe tools/build_validation_set.py --n 2000

writes `docs/items.json` + `docs/img/NNNN.jpg` (public, no verdicts) and
`analysis/items_key.csv` (private key with predicate + author verdicts,
gitignored). Uses the dissertation repo's `outputs/pairs.csv` + geometry cache.

```
docs/            the static site (publish this folder)
  index.html     intro + game + end screens
  app.js         batch assignment, vote queue, zoom, keyboard
  config.js      <-- paste your backend URL here
  items.json     the ~2000 claims (no verdicts, no key)
  img/           the ~2000 rendered photos (faces pixelated)
tools/
  build_validation_set.py  samples + renders the claim set
  render_clean_images.py   face-blur + render helpers (imported above)
apps_script/
  Code.gs        Google Apps Script backend (votes -> your Google Sheet)
analysis/
  score_votes.py crowd precision + CIs, kappa vs author, alpha, spam filters
  items_key.csv  private key (gitignored): predicate + author verdict per item
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
share button. `TARGET_VOTES_PER_ITEM` in `config.js` sets the goal shown on
screen; it is display only and never caps collection. It is currently 1 —
one pass over the pool, ~2000 answers, ~134 people at 15 each — which
already gives a tight overall and per-predicate precision estimate.

Batches are always served least-covered-first, so answers beyond the goal
automatically become second and third judgments on claims already seen.
Raise `TARGET_VOTES_PER_ITEM` to 2 or 3 once a pass is complete if the
volunteers keep coming: majority verdicts and Krippendorff's alpha need at
least two judgments per claim.

## Collecting results

1. In the Google Sheet: File → Download → CSV (the `votes` sheet).
2. `python analysis/score_votes.py votes.csv`
   - joins `analysis/items_key.csv` (predicate + author verdict per item)
   - prints CROWD PRECISION overall and per predicate with 95% Wilson
     intervals (the headline), the author-bias check (agreement + Cohen's
     kappa, crowd vs author on the verdicted subset), Krippendorff's alpha
     (crowd-internal), coverage, and a per-rater spam table
   - writes `majority_verdicts.csv` + `report.json`
   - useful flags: `--min-ms 800` (drop reflex-speed answers),
     `--exclude-rater <id>` (drop a spammer found in the rater table)

Ties (e.g. 1 y vs 1 n) resolve to `n`, matching the protocol's
"unclear = n" rule; they are counted and flagged in the output.

## Notes

- `analysis/items_key.csv` and all author verdicts stay OUT of this repo
  and off the site (gitignored) — the site only ever sees claim text and
  images, never a verdict.
- Faces are pixelated at render time; where blurring a face would cover the
  object a claim is about, that claim is dropped rather than shown.
- Local test mode: while `BACKEND_URL` is empty the site banner says
  "test mode" and answers stay in the browser's localStorage.
- GitHub Pages caches assets for 10 minutes, so returning visitors can run
  old code. `index.html` loads `style.css` / `config.js` / `app.js` with a
  `?v=N` query and `app.js` reuses its own `N` for `items.json` — **bump
  `?v=` in `index.html` whenever the claim set is rebuilt**, otherwise a
  cached script could submit votes against renumbered items.
- Image credit: *A Spatial Relationship Aware Dataset for Robotics*
  (Wang et al., 2025), CC-BY 4.0. The page is `noindex`.
