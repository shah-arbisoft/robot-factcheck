"""Score the crowdsourced blind-audit votes.

Inputs
------
1. votes.csv           exported from the Google Sheet (File > Download > CSV),
                       columns: timestamp, rater, item, verdict, ms, note, batch
2. the blind-audit folder (items + key) and the three source audit sheets,
   found under --audit-root (default: the dissertation repo's outputs/).

Outputs
-------
- majority_verdicts.csv   per item: votes, majority, author verdict, agreement
- report.json             all statistics
- a readable report on stdout: agreement + Cohen's kappa (crowd majority vs
  author), Krippendorff's alpha (crowd-internal), per-predicate and
  per-source-audit breakdowns, per-rater quality table.

Usage
-----
    python score_votes.py votes.csv
    python score_votes.py votes.csv --min-ms 800 --min-votes 3
"""

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_AUDIT_ROOT = r"D:\uni_project\spatial-auto-annotation\outputs"
SOURCE_SHEETS = {  # key prefix -> source audit sheet (relative to audit root)
    "A": "audit/audit_sheet.csv",
    "B": "audit_v2/audit_sheet.csv",
    "C": "audit_plane/audit_sheet.csv",
}
SOURCE_NAMES = {"A": "audit v1", "B": "audit v2 (support)", "C": "plane audit"}


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def verdict_col(row):
    for k in row:
        if k.strip().lower().startswith("verdict"):
            return k
    raise KeyError("no verdict column in %s" % list(row))


def load_author_verdicts(audit_root):
    """blind item id -> (author verdict, source prefix) via the key file."""
    root = Path(audit_root)
    key_rows = read_csv(root / "audit_blind" / "_key_do_not_share.csv")
    sheets = {}
    for prefix, rel in SOURCE_SHEETS.items():
        rows = read_csv(root / rel)
        vcol = verdict_col(rows[0])
        sheets[prefix] = {row["id"]: row[vcol].strip().lower() for row in rows}
    out = {}
    for row in key_rows:
        blind_id = int(row["id"])
        prefix, src_id = row["_key"].split(":")
        v = sheets[prefix].get(src_id, "")
        if v not in ("y", "n"):
            print("WARNING: no author verdict for blind item %d (%s)" % (blind_id, row["_key"]))
            continue
        out[blind_id] = (v, prefix)
    return out


def load_items(audit_root):
    rows = read_csv(Path(audit_root) / "audit_blind" / "audit_sheet_blind.csv")
    return {int(r["id"]): r for r in rows}


def cohen_kappa(pairs):
    """pairs: list of (a, b) labels."""
    n = len(pairs)
    if n == 0:
        return float("nan"), float("nan")
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    if pe == 1.0:
        return po, float("nan")
    return po, (po - pe) / (1 - pe)


def krippendorff_alpha(units):
    """Nominal alpha. units: list of lists of labels (one list per item, >=2 labels)."""
    o = defaultdict(float)  # coincidence matrix
    for vals in units:
        m = len(vals)
        if m < 2:
            continue
        for i, c in enumerate(vals):
            for j, k in enumerate(vals):
                if i != j:
                    o[(c, k)] += 1.0 / (m - 1)
    n_c = defaultdict(float)
    for (c, _k), v in o.items():
        n_c[c] += v
    n = sum(n_c.values())
    if n <= 1:
        return float("nan")
    d_o = sum(v for (c, k), v in o.items() if c != k)
    d_e = sum(n_c[c] * n_c[k] for c in n_c for k in n_c if c != k)
    if d_e == 0:
        return float("nan")
    return 1.0 - (n - 1) * d_o / d_e


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("votes_csv", help="votes.csv exported from the Google Sheet")
    ap.add_argument("--audit-root", default=DEFAULT_AUDIT_ROOT)
    ap.add_argument("--min-ms", type=int, default=0,
                    help="drop votes answered faster than this (spam filter)")
    ap.add_argument("--min-votes", type=int, default=3,
                    help="votes needed for the 'well-covered' subset stats")
    ap.add_argument("--exclude-rater", action="append", default=[],
                    help="rater id(s) to drop entirely (repeatable)")
    ap.add_argument("--out", default=None, help="output directory (default: next to votes.csv)")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else Path(args.votes_csv).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    items = load_items(args.audit_root)
    author = load_author_verdicts(args.audit_root)

    raw = read_csv(args.votes_csv)
    print("raw votes: %d" % len(raw))

    # --- clean: dedup (rater, item) keeping first; filters ---
    seen, votes, dropped_fast = set(), [], 0
    for r in raw:
        try:
            item = int(r["item"])
            ms = int(float(r["ms"] or 0))
        except (ValueError, KeyError):
            continue
        v = (r.get("verdict") or "").strip().lower()
        if v not in ("y", "n") or item not in items:
            continue
        if r.get("rater") in args.exclude_rater:
            continue
        if args.min_ms and 0 < ms < args.min_ms:
            dropped_fast += 1
            continue
        key = (r.get("rater"), item)
        if key in seen:
            continue
        seen.add(key)
        votes.append({"rater": r.get("rater", ""), "item": item, "v": v, "ms": ms})
    print("clean votes: %d  (dropped %d fast, %d dup/invalid)"
          % (len(votes), dropped_fast, len(raw) - len(votes) - dropped_fast))

    by_item = defaultdict(list)
    for v in votes:
        by_item[v["item"]].append(v)

    # --- per-rater quality table ---
    by_rater = defaultdict(list)
    for v in votes:
        by_rater[v["rater"]].append(v)
    rater_rows = []
    for rater, vs in sorted(by_rater.items(), key=lambda kv: -len(kv[1])):
        agree, considered = 0, 0
        for v in vs:
            others = [w["v"] for w in by_item[v["item"]] if w["rater"] != rater]
            if not others:
                continue
            maj = Counter(others).most_common()
            if len(maj) > 1 and maj[0][1] == maj[1][1]:
                continue  # others tied: uninformative
            considered += 1
            agree += v["v"] == maj[0][0]
        rater_rows.append({
            "rater": rater[:12],
            "votes": len(vs),
            "median_ms": int(statistics.median(v["ms"] for v in vs)),
            "fast_frac": round(sum(v["ms"] < 800 for v in vs) / len(vs), 2),
            "agree_with_others": round(agree / considered, 2) if considered else None,
        })

    # --- per-item majority + author join ---
    item_rows, pairs_all, pairs_covered = [], [], []
    per_pred = defaultdict(list)
    per_src = defaultdict(list)
    ties = 0
    for item_id in sorted(items):
        vs = by_item.get(item_id, [])
        n_y = sum(1 for v in vs if v["v"] == "y")
        n_n = len(vs) - n_y
        if not vs:
            majority = ""
        elif n_y == n_n:
            majority = "n"  # protocol: not clearly true -> n
            ties += 1
        else:
            majority = "y" if n_y > n_n else "n"
        av, src = author.get(item_id, ("", ""))
        it = items[item_id]
        item_rows.append({
            "id": item_id, "image": it["image"], "subject": it["subject"],
            "predicate": it["predicate"], "object": it["object"],
            "votes": len(vs), "yes": n_y, "no": n_n, "tie": int(bool(vs) and n_y == n_n),
            "majority": majority, "author": av,
            "agree": "" if not (majority and av) else int(majority == av),
        })
        if majority and av:
            pairs_all.append((majority, av))
            per_pred[it["predicate"]].append((majority, av))
            per_src[src].append((majority, av))
            if len(vs) >= args.min_votes:
                pairs_covered.append((majority, av))

    # --- crowd-internal reliability ---
    units = [[v["v"] for v in vs] for vs in by_item.values() if len(vs) >= 2]
    alpha = krippendorff_alpha(units)

    po_all, kappa_all = cohen_kappa(pairs_all)
    po_cov, kappa_cov = cohen_kappa(pairs_covered)

    # --- write outputs ---
    with open(out_dir / "majority_verdicts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(item_rows[0]))
        w.writeheader()
        w.writerows(item_rows)

    report = {
        "n_raw": len(raw), "n_clean": len(votes),
        "n_raters": len(by_rater), "n_items_voted": len(by_item),
        "coverage": {str(k): sum(1 for vs in by_item.values() if len(vs) >= k)
                     for k in (1, 2, 3, 5)},
        "ties_resolved_to_n": ties,
        "majority_vs_author": {
            "all_items": {"n": len(pairs_all), "agreement": po_all, "kappa": kappa_all},
            "covered_items(>=%d votes)" % args.min_votes:
                {"n": len(pairs_covered), "agreement": po_cov, "kappa": kappa_cov},
        },
        "krippendorff_alpha_crowd": alpha,
        "per_predicate": {p: {"n": len(ps), "agreement": cohen_kappa(ps)[0],
                              "kappa": cohen_kappa(ps)[1]}
                          for p, ps in sorted(per_pred.items())},
        "per_source_audit": {SOURCE_NAMES.get(s, s):
                             {"n": len(ps), "agreement": cohen_kappa(ps)[0],
                              "kappa": cohen_kappa(ps)[1]}
                             for s, ps in sorted(per_src.items())},
        "raters": rater_rows,
    }
    with open(out_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # --- readable summary ---
    print("\n=== crowd vs author ===")
    print("all voted items      : n=%3d  agreement=%.3f  kappa=%.3f" % (len(pairs_all), po_all, kappa_all))
    print("items with >=%d votes : n=%3d  agreement=%.3f  kappa=%.3f" % (args.min_votes, len(pairs_covered), po_cov, kappa_cov))
    print("crowd-internal Krippendorff alpha (items with >=2 votes): %.3f" % alpha)
    print("ties resolved to 'n': %d" % ties)
    print("\ncoverage: " + ", ".join("%s+ votes: %d items" % (k, v) for k, v in report["coverage"].items()))
    print("\n=== per predicate (majority vs author) ===")
    for p, st in report["per_predicate"].items():
        print("  %-18s n=%3d  agreement=%.3f" % (p, st["n"], st["agreement"]))
    print("\n=== per source audit ===")
    for s, st in report["per_source_audit"].items():
        print("  %-20s n=%3d  agreement=%.3f" % (s, st["n"], st["agreement"]))
    print("\n=== raters (check fast_frac ~1.0 or low agreement for spam) ===")
    print("  %-14s %5s %9s %9s %6s" % ("rater", "votes", "median_ms", "fast_frac", "agree"))
    for r in rater_rows:
        print("  %-14s %5d %9d %9.2f %6s" % (r["rater"], r["votes"], r["median_ms"],
                                             r["fast_frac"], r["agree_with_others"]))
    print("\nwrote %s and %s" % (out_dir / "majority_verdicts.csv", out_dir / "report.json"))


if __name__ == "__main__":
    sys.exit(main())
