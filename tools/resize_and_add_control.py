"""Shrink the deployed set to what actually has data, and add the control arm.

Target: 1,000 total. The floor is fixed by what already has votes -- those
ids can never move or disappear, or the votes pointing at them become
uninterpretable. Everything else is free to drop. 1,000 minus that floor is
spent on the control arm, split evenly across the seven predicates.

Fetches the live vote count itself, right before writing, rather than
trusting a file that could be minutes stale -- the one property that must
hold is "every id this script drops has zero votes at the moment of
writing", and that can only be checked against the live sheet.

    D:\\uni_project\\.venv\\Scripts\\python.exe tools/resize_and_add_control.py [--total 1000]
"""
import argparse
import csv
import json
import random
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_clean_images as R
from build_validation_set import PRED_ORDER, load_gold, render_claim

SITE = R.SITE
ITEMS = SITE / "items.json"
KEY_OUT = Path(__file__).resolve().parents[1] / "analysis" / "items_key.csv"
CONTROL_SEED = 20260811
COUNTS_URL = ("https://script.google.com/macros/s/AKfycbzi3O5jNeKeAcvO-"
              "JjdjWiEZcCLSpqK5Ag-_ojsmB4HLUo96otHAlEABkaBTH7QzPoo/exec"
              "?fn=counts")


def fetch_live_votes() -> dict[int, int]:
    with urllib.request.urlopen(COUNTS_URL, timeout=20) as resp:
        data = json.load(resp)
    return {int(k): v for k, v in data["counts"].items()}


def build_control_rows(per_predicate: int, taken_triplets: set):
    rng_c = random.Random(CONTROL_SEED)
    gold = load_gold()
    rows, images = [], {}
    cid = 3000
    for p in PRED_ORDER:
        pool = sorted(t for t in set(gold.get(p, [])) if (t, p) not in taken_triplets)
        rng_c.shuffle(pool)
        kept = 0
        for image_id, s_i, o_i in pool:
            if kept >= per_predicate:
                break
            res = render_claim(image_id, s_i, o_i)
            if res is None:
                continue
            item, _blurred = res
            cid += 1
            img_obj = item.pop("img_obj")
            name = "%04d.jpg" % cid
            rows.append({"id": cid, "img": name, "s": item["s"], "p": p,
                        "o": item["o"], "sb": item["sb"], "ob": item["ob"]})
            images[cid] = (name, img_obj, image_id, s_i, o_i)
            kept += 1
        print("  control %-16s kept %d" % (p, kept))
    return rows, images


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=1000)
    args = ap.parse_args()

    if not ITEMS.exists():
        print(f"  no deployed set at {ITEMS}")
        return 1
    deployed = json.loads(ITEMS.read_text(encoding="utf-8"))
    if max(it["id"] for it in deployed) >= 3001:
        print("  a control arm already appears deployed; not touching it")
        return 1
    if not KEY_OUT.exists():
        print(f"  no private key at {KEY_OUT}")
        return 1
    key_rows = list(csv.DictReader(KEY_OUT.open(encoding="utf-8")))
    key_by_id = {int(r["id"]): r for r in key_rows}

    print("  fetching live vote counts...")
    live = fetch_live_votes()
    voted_ids = {i for i, n in live.items() if n > 0 and i <= 2002}
    print(f"  {len(voted_ids)} treatment ids have at least one vote, right now")

    dropped = [it for it in deployed if it["id"] not in voted_ids]
    reused = [it for it in deployed if it["id"] in voted_ids]
    # the one check that must never fail
    unsafe = [it["id"] for it in dropped if live.get(it["id"], 0) > 0]
    if unsafe:
        print(f"  ABORT: {len(unsafe)} id(s) marked for removal actually have "
              f"votes: {unsafe[:10]}")
        return 1
    print(f"  keeping {len(reused)} voted treatment items, "
          f"dropping {len(dropped)} with zero votes (verified against the "
          "live sheet just now)")

    n_control = args.total - len(reused)
    if n_control <= 0 or n_control % 7 != 0:
        print(f"  {args.total} total minus {len(reused)} kept = {n_control} "
              f"for the control arm, which does not split evenly across 7 "
              f"predicates. Choose a --total such that (--total - "
              f"{len(reused)}) is a positive multiple of 7.")
        return 1
    per_predicate = n_control // 7
    print(f"  control arm: {n_control} claims, {per_predicate} per predicate")

    rows, images = build_control_rows(per_predicate, set())

    # ---- verification -------------------------------------------------
    problems = []
    if len(rows) != n_control:
        problems.append(f"{len(rows)} control rows, expected {n_control}")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        problems.append("duplicate control ids")
    if sorted(ids) != list(range(3001, 3001 + len(ids))):
        problems.append("control ids not a contiguous 3001.. block")
    deployed_images = {it["img"] for it in reused}
    collide = deployed_images & {r["img"] for r in rows}
    if collide:
        problems.append(f"image filename collision: {collide}")
    control_triplets = [(images[r["id"]][2], images[r["id"]][3],
                         images[r["id"]][4], r["p"]) for r in rows]
    if len(set(control_triplets)) != len(control_triplets):
        problems.append("duplicate (image, subject, object, predicate) "
                        "within the control arm")
    # Cross-arm duplicate check. Structurally these pools can't overlap --
    # treatment claims are predicates a triplet is NOT in gold for, control
    # claims are predicates it IS in gold for -- but "structurally can't"
    # is a claim worth actually checking rather than trusting.
    reused_triplets = {(key_by_id[i]["image_id"], key_by_id[i]["subject"],
                        key_by_id[i]["object"], key_by_id[i]["predicate"])
                       for i in (it["id"] for it in reused) if i in key_by_id}
    # compare on (image_id, subject-label, object-label, predicate) using the
    # same string form the key file stores, so this is a real apples-to-apples
    # check against reused_triplets rather than a differently-shaped tuple
    control_triplets_key_form = {(images[r["id"]][2], r["s"], r["o"], r["p"])
                                 for r in rows}
    cross_overlap = reused_triplets & control_triplets_key_form
    if cross_overlap:
        problems.append(f"{len(cross_overlap)} claim(s) appear in both arms: "
                        f"{list(cross_overlap)[:5]}")
    gold = load_gold()
    for r in rows:
        _name, _img, image_id, s_i, o_i = images[r["id"]]
        if (image_id, s_i, o_i) not in set(gold.get(r["p"], [])):
            problems.append(f"id {r['id']} not actually in gold for {r['p']!r}")
            break
    if len(reused) + len(rows) != args.total:
        problems.append(f"final total {len(reused) + len(rows)} != {args.total}")
    if problems:
        print("  ABORT:")
        for p in problems:
            print(f"    - {p}")
        return 1
    print(f"  verified: {len(reused)} kept + {len(rows)} control = "
          f"{len(reused) + len(rows)}, all ids unique and contiguous where "
          "expected, every control triplet confirmed in gold, no image "
          "collisions")

    # ---- write --------------------------------------------------------
    for r in rows:
        rid, name, img_obj, *_ = (r["id"], *images[r["id"]])
        img_obj.save(SITE / "img" / name, quality=R.JPEG_QUALITY, optimize=True)
    print(f"  wrote {len(rows)} new images")

    for it in dropped:
        p = SITE / "img" / it["img"]
        if p.exists():
            p.unlink()
    print(f"  removed {len(dropped)} images for dropped (zero-vote) items")

    merged_items = reused + rows
    ITEMS.write_text(
        "[\n" + ",\n".join(json.dumps(it, separators=(",", ":"))
                           for it in merged_items) + "\n]\n",
        encoding="utf-8")
    print(f"  {ITEMS}: {len(merged_items)} rows "
          f"({len(reused)} treatment, kept ids byte-unchanged; "
          f"{len(rows)} control, new)")

    kept_key_rows = [key_by_id[i] for i in (it["id"] for it in reused)]
    for r in kept_key_rows:
        r.setdefault("arm", "treatment")
    control_key_rows = []
    for r in rows:
        rid, name, img_obj, image_id, s_i, o_i = (r["id"], *images[r["id"]])
        control_key_rows.append({
            "id": rid, "image_id": image_id, "subject": r["s"],
            "predicate": r["p"], "object": r["o"], "author_verdict": "",
            "source": "", "arm": "control",
        })
    fieldnames = list(kept_key_rows[0].keys()) if kept_key_rows else \
        ["id", "image_id", "subject", "predicate", "object",
         "author_verdict", "source", "arm"]
    with KEY_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(kept_key_rows + control_key_rows)
    print(f"  {KEY_OUT}: {len(kept_key_rows)} treatment + "
          f"{len(control_key_rows)} control rows")
    print("\nNothing pushed. Bump the ?v= cache-buster in docs/index.html, "
          "then commit and push docs/ when ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
