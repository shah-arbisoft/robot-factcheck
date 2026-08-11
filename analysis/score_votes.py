"""Score the crowdsourced validation votes.

Inputs
------
1. votes.csv        exported from the Google Sheet (File > Download > CSV),
                    columns: timestamp, rater, item, verdict, ms, note, batch
2. items_key.csv    written by tools/build_validation_set.py (next to this
                    script): id, image_id, subject, predicate, object,
                    author_verdict, source

What it reports
---------------
- CROWD PRECISION of the automatic labels: the fraction the crowd judged TRUE,
  overall and per predicate, with a 95% Wilson interval. This is the headline
  the 2000-claim set exists to produce.
- AUTHOR-BIAS CHECK on the subset that carries the author's own verdicts:
  agreement + Cohen's kappa (crowd majority vs author) — lets you state the
  earlier hand-verdicted audits were not biased.
- Crowd-internal reliability (Krippendorff alpha), coverage, ties, and a
  per-rater table for spotting inattentive raters.

Usage
-----
    python score_votes.py votes.csv
    python score_votes.py votes.csv --min-ms 800 --min-votes 3
"""

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

KEY_DEFAULT = Path(__file__).resolve().parent / "items_key.csv"
PRED_ORDER = ["on", "under", "to the left of", "to the right of",
              "in front of", "behind", "near"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def wilson(k, n, z=1.96):
    """95% Wilson score interval for a proportion k/n."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def cohen_kappa(pairs):
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
    o = defaultdict(float)
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
    return 1.0 - (n - 1) * d_o / d_e if d_e else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("votes_csv", help="votes.csv exported from the Google Sheet")
    ap.add_argument("--key", default=str(KEY_DEFAULT), help="items_key.csv")
    ap.add_argument("--min-ms", type=int, default=0,
                    help="drop votes answered faster than this (spam filter)")
    ap.add_argument("--min-votes", type=int, default=3,
                    help="votes needed for the 'well-covered' subset stats")
    ap.add_argument("--exclude-rater", action="append", default=[])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else Path(args.votes_csv).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    key = {int(r["id"]): r for r in read_csv(args.key)}
    author = {i: r["author_verdict"].strip().lower() for i, r in key.items()
              if r.get("author_verdict", "").strip().lower() in ("y", "n")}

    raw = read_csv(args.votes_csv)
    print("raw votes: %d   items in key: %d" % (len(raw), len(key)))

    seen, votes, dropped_fast = set(), [], 0
    for r in raw:
        try:
            item = int(r["item"])
            ms = int(float(r["ms"] or 0))
        except (ValueError, KeyError):
            continue
        v = (r.get("verdict") or "").strip().lower()
        if v not in ("y", "n") or item not in key:
            continue
        if r.get("rater") in args.exclude_rater:
            continue
        if args.min_ms and 0 < ms < args.min_ms:
            dropped_fast += 1
            continue
        k = (r.get("rater"), item)
        if k in seen:
            continue
        seen.add(k)
        votes.append({"rater": r.get("rater", ""), "item": item, "v": v, "ms": ms})
    print("clean votes: %d  (dropped %d fast, %d dup/invalid)"
          % (len(votes), dropped_fast, len(raw) - len(votes) - dropped_fast))

    by_item = defaultdict(list)
    for v in votes:
        by_item[v["item"]].append(v)

    # per-item majority (ties -> n, matching the "unclear = wrong" rule)
    majority, ties = {}, 0
    for item, vs in by_item.items():
        n_y = sum(1 for v in vs if v["v"] == "y")
        n_n = len(vs) - n_y
        if n_y == n_n:
            majority[item] = "n"
            ties += 1
        else:
            majority[item] = "y" if n_y > n_n else "n"

    # ---- crowd precision (headline) ----
    def precision(item_ids):
        ids = [i for i in item_ids if i in majority]
        k = sum(1 for i in ids if majority[i] == "y")
        return len(ids), wilson(k, len(ids))

    by_pred_items = defaultdict(list)
    for i, r in key.items():
        by_pred_items[r["predicate"]].append(i)

    overall_n, overall_ci = precision(list(majority))
    per_pred_prec = {}
    for p in PRED_ORDER:
        n, ci = precision(by_pred_items.get(p, []))
        per_pred_prec[p] = {"n": n, "precision": ci[0], "lo": ci[1], "hi": ci[2]}

    # ---- author-bias check ----
    author_pairs = [(majority[i], author[i]) for i in majority if i in author]
    # --- the control arm --------------------------------------------------
    # key rows carry arm=treatment (the tool's extra predictions) or
    # arm=control (relations a human annotator wrote down). Same raters, same
    # images, same instruction: the only difference is who produced the label.
    arms = defaultdict(list)
    for item in majority:
        arms[(key.get(item, {}).get("arm") or "treatment")].append(item)

    arm_stats = {}
    for arm, ids in sorted(arms.items()):
        n, (prop, lo, hi) = precision(ids)
        k = sum(1 for i in ids if majority[i] == "y")
        arm_stats[arm] = {"claims": n, "judged_true": k,
                          "precision": (k / n) if n else None,
                          "wilson95": [round(lo, 4), round(hi, 4)]}

    po_auth, kappa_auth = cohen_kappa(author_pairs)

    # ---- crowd-internal reliability ----
    alpha = krippendorff_alpha([[v["v"] for v in vs]
                                for vs in by_item.values() if len(vs) >= 2])

    # ---- per-rater table ----
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
            mc = Counter(others).most_common()
            if len(mc) > 1 and mc[0][1] == mc[1][1]:
                continue
            considered += 1
            agree += v["v"] == mc[0][0]
        rater_rows.append({
            "rater": rater[:12], "votes": len(vs),
            "median_ms": int(statistics.median(v["ms"] for v in vs)),
            "fast_frac": round(sum(v["ms"] < 800 for v in vs) / len(vs), 2),
            "agree_with_others": round(agree / considered, 2) if considered else None,
        })

    # ---- write per-item verdicts ----
    rows = []
    for i in sorted(key):
        vs = by_item.get(i, [])
        n_y = sum(1 for v in vs if v["v"] == "y")
        rows.append({
            "id": i, "image_id": key[i]["image_id"],
            "subject": key[i]["subject"], "predicate": key[i]["predicate"],
            "object": key[i]["object"], "votes": len(vs), "yes": n_y,
            "no": len(vs) - n_y, "crowd": majority.get(i, ""),
            "author": author.get(i, ""),
            "agree": "" if i not in majority or i not in author
                     else int(majority[i] == author[i]),
        })
    with open(out_dir / "majority_verdicts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    coverage = {str(k): sum(1 for vs in by_item.values() if len(vs) >= k)
                for k in (1, 2, 3, 5)}
    report = {
        "n_raw": len(raw), "n_clean": len(votes), "n_raters": len(by_rater),
        "n_items_total": len(key), "n_items_voted": len(by_item),
        "coverage_by_votes": coverage, "ties_resolved_to_n": ties,
        "crowd_precision_overall": {"n": overall_n, "precision": overall_ci[0],
                                    "ci95": [overall_ci[1], overall_ci[2]]},
        "crowd_precision_per_predicate": per_pred_prec,
        "author_bias_check": {"n": len(author_pairs), "agreement": po_auth,
                              "kappa": kappa_auth},
        "by_arm": arm_stats,
        "krippendorff_alpha_crowd": alpha,
        "raters": rater_rows,
    }
    with open(out_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # ---- readable summary ----
    p, lo, hi = overall_ci
    print("\n=== CROWD PRECISION of the automatic labels ===")
    print("overall: %.3f  (95%% CI %.3f-%.3f, n=%d judged items)" % (p, lo, hi, overall_n))
    print("  %-16s %6s %8s   %s" % ("predicate", "n", "precision", "95% CI"))
    for pr in PRED_ORDER:
        s = per_pred_prec[pr]
        if s["n"]:
            print("  %-16s %6d %8.3f   %.3f-%.3f" % (pr, s["n"], s["precision"], s["lo"], s["hi"]))
        else:
            print("  %-16s %6d      n/a" % (pr, 0))
    print("\n=== author-bias check (crowd vs author verdicts) ===")
    if len(arm_stats) > 1:
        print("\nBY ARM (the comparison the control exists for)")
        for arm in ("treatment", "control"):
            a = arm_stats.get(arm)
            if a and a["precision"] is not None:
                print("  %-10s %4d claims   crowd precision %.3f  [%.2f, %.2f]"
                      % (arm, a["claims"], a["precision"],
                         a["wilson95"][0], a["wilson95"][1]))
        t, c = arm_stats.get("treatment"), arm_stats.get("control")
        if t and c and t["precision"] is not None and c["precision"] is not None:
            d = t["precision"] - c["precision"]
            overlap = not (t["wilson95"][1] < c["wilson95"][0]
                           or c["wilson95"][1] < t["wilson95"][0])
            print("  difference %+.3f (tool minus human); intervals %s"
                  % (d, "overlap, so not separated at this sample"
                     if overlap else "are disjoint"))
    else:
        print("\nBY ARM: no control claims in the key yet -- rebuild the item "
              "set with --control N to make the precision figure interpretable")

    print("n=%d  agreement=%.3f  Cohen's kappa=%.3f" % (len(author_pairs), po_auth, kappa_auth))
    print("crowd-internal Krippendorff alpha (>=2 votes): %.3f" % alpha)
    print("ties resolved to 'n': %d" % ties)
    print("coverage: " + ", ".join("%s+ votes: %d" % (k, v) for k, v in coverage.items()))
    print("\n=== raters (watch fast_frac ~1.0 or low agreement) ===")
    print("  %-14s %5s %9s %9s %6s" % ("rater", "votes", "median_ms", "fast_frac", "agree"))
    for r in rater_rows:
        print("  %-14s %5d %9d %9.2f %6s" % (r["rater"], r["votes"], r["median_ms"],
                                             r["fast_frac"], r["agree_with_others"]))
    print("\nwrote %s and %s" % (out_dir / "majority_verdicts.csv", out_dir / "report.json"))


if __name__ == "__main__":
    sys.exit(main())
