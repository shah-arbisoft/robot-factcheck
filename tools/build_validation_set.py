"""Build a large validation set for the site (default 2000 claims).

Scales the crowd audit up from the original 150 blind-audit items to a
stratified sample of the tool's EXTRA predictions (auto labels on ordered
object pairs the human annotators never labelled — the part with no ground
truth, exactly what needs independent human judgement).

- stratified evenly across the 7 predicates (~N/7 each)
- the 150 author-verdicted audit triplets are force-included as a subset, so
  the kappa / author-bias check still runs on them
- faces anonymised and claim-object conflicts dropped, reusing the exact
  pipeline in render_clean_images.py
- writes docs/items.json + docs/img/NNNN.jpg (site, no verdicts) and
  analysis/items_key.csv (private: image_id, pair, predicate, author verdict)

    D:\\uni_project\\.venv\\Scripts\\python.exe tools/build_validation_set.py --n 2000
"""

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_clean_images as R  # blur/render pipeline + paths

PRED_ORDER = ["on", "under", "to the left of", "to the right of",
              "in front of", "behind", "near"]
PAIRS = R.REPO / "outputs" / "pairs.csv"
KEY_OUT = Path(__file__).resolve().parents[1] / "analysis" / "items_key.csv"


def load_author_verdicts():
    """(image_id, subj_idx, obj_idx, predicate) -> (verdict, source-prefix)."""
    out = {}
    for prefix, path in R.SOURCE_SHEETS.items():
        rows = R.read_csv(path)
        vcol = next(k for k in rows[0] if k.strip().lower().startswith("verdict"))
        for r in rows:
            key = (r["image_id"], R.name_idx(r["subject"]),
                   R.name_idx(r["object"]), r["predicate"])
            out[key] = (r[vcol].strip().lower(), prefix)
    return out


def load_extras():
    """predicate -> list of (image_id, subj, obj) with no human gold on the pair."""
    extras = defaultdict(list)
    with open(PAIRS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pred = set(r["pred"].split(";")) if r["pred"] else set()
            gold = set(r["gold"].split(";")) if r["gold"] else set()
            for k in pred - gold:
                extras[k].append((r["image_id"], int(r["subj"]), int(r["obj"])))
    return extras


def geometry(image_id):
    group, stem = image_id.split("/")
    geo = json.loads((R.GEO / group / (stem + ".json")).read_text(encoding="utf-8"))
    return group, stem, geo, {o["idx"]: o for o in geo}


def render_claim(image_id, subj, obj):
    """Return (item-dict-without-id, blurred?) or None if a claim object is
    unavoidably inside a face-blur region."""
    group, stem, geo, by_idx = geometry(image_id)
    if subj not in by_idx or obj not in by_idx:
        return None
    img = ImageOps.exif_transpose(
        Image.open(R.DATASET_IMG / group / (stem + ".jpg"))).convert("RGB")
    if img.width > R.MAX_W:
        img = img.resize((R.MAX_W, round(img.height * R.MAX_W / img.width)), Image.LANCZOS)
    W, H = img.size
    regs = R.face_regions(img, geo)
    for idx in (subj, obj):
        o = by_idx[idx]
        if o["label"] == "human":
            continue
        b = o["box"]
        bp = (b[0] * W, b[1] * H, b[2] * W, b[3] * H)
        if any(R._overlap_frac(bp, r) > 0.40 for r in regs):
            return None
    blurred = R.pixelate(img, regs)
    return {
        "img_obj": img,
        "s": f"{by_idx[subj]['label']}{subj}",
        "o": f"{by_idx[obj]['label']}{obj}",
        "sb": [round(v, 4) for v in by_idx[subj]["box"]],
        "ob": [round(v, 4) for v in by_idx[obj]["box"]],
    }, blurred


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000, help="target number of claims")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    author = load_author_verdicts()
    extras = load_extras()
    per_pred = round(args.n / len(PRED_ORDER))

    # candidate order per predicate: author-verdicted first, then fresh extras
    author_by_pred = defaultdict(list)
    for (image_id, s, o, p), (v, src) in author.items():
        author_by_pred[p].append((image_id, s, o, v, src))

    (R.SITE / "img").mkdir(parents=True, exist_ok=True)
    KEY_OUT.parent.mkdir(parents=True, exist_ok=True)

    items, key_rows = [], []
    blur_imgs = 0
    next_id = 0
    for p in PRED_ORDER:
        seen = set()
        candidates = []
        for image_id, s, o, v, src in author_by_pred.get(p, []):
            candidates.append((image_id, s, o, v, src))
            seen.add((image_id, s, o))
        fresh = [e for e in extras[p] if e not in seen]
        rng.shuffle(fresh)
        candidates += [(iid, s, o, "", "") for (iid, s, o) in fresh]

        kept = 0
        for image_id, s, o, v, src in candidates:
            if kept >= per_pred:
                break
            res = render_claim(image_id, s, o)
            if res is None:
                continue
            item, blurred = res
            next_id += 1
            blur_imgs += 1 if blurred else 0
            name = "%04d.jpg" % next_id
            item.pop("img_obj").save(R.SITE / "img" / name,
                                     quality=R.JPEG_QUALITY, optimize=True)
            items.append({"id": next_id, "img": name, "s": item["s"],
                          "p": p, "o": item["o"], "sb": item["sb"], "ob": item["ob"]})
            key_rows.append({"id": next_id, "image_id": image_id,
                             "subject": item["s"], "predicate": p, "object": item["o"],
                             "author_verdict": v, "source": src})
            kept += 1
        print("  %-16s kept %d" % (p, kept))

    # site items (shuffled so consecutive claims aren't same-predicate runs)
    rng.shuffle(items)
    with open(R.SITE / "items.json", "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(it, separators=(",", ":")) for it in items) + "\n]\n")

    # drop stale images from any previous build
    keep = {it["img"] for it in items}
    removed = 0
    for q in list((R.SITE / "img").glob("*.jpg")) + list((R.SITE / "img").glob("*.png")):
        if q.name not in keep:
            q.unlink()
            removed += 1

    with open(KEY_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(key_rows[0]))
        w.writeheader()
        w.writerows(sorted(key_rows, key=lambda r: r["id"]))

    n_auth = sum(1 for r in key_rows if r["author_verdict"] in ("y", "n"))
    print("\nbuilt %d claims -> %s (removed %d stale)" % (len(items), R.SITE / "img", removed))
    print("faces anonymised in %d images; %d carry an author verdict (kappa subset)"
          % (blur_imgs, n_auth))
    print("private key -> %s" % KEY_OUT)


if __name__ == "__main__":
    sys.exit(main())
