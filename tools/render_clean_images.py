"""Re-render the 150 blind-audit images for the website, without burned-in boxes.

The original audit renders draw SUBJECT/OBJECT label banners that can cover
small far-away objects. For the site the browser draws the boxes as overlays
instead, so this script produces:

- docs/img/NNN.jpg   clean upright photo (EXIF-corrected), max width 1024
- docs/items.json    the 150 claims, now with normalised subject/object boxes
                     sb/ob = [x1, y1, x2, y2] in 0..1 image coordinates

Box sources: the blind key -> the source audit sheet (image_id) -> the
pipeline's geometry cache (outputs/geometry/<group>/<stem>.json), the same
data the original renders used. Names like "remote4" = label + object idx.

Run with the dissertation venv (PIL):
    D:\\uni_project\\.venv\\Scripts\\python.exe tools/render_clean_images.py
"""

import csv
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps

REPO = Path(r"D:\uni_project\spatial-auto-annotation")
BLIND = REPO / "outputs" / "audit_blind"
GEO = REPO / "outputs" / "geometry"
DATASET_IMG = REPO.parent / "SpatialAwareRobotDataset-main" / "SpatialAwareRobotDataset-main" / "img_data"
SITE = Path(__file__).resolve().parents[1] / "docs"

SOURCE_SHEETS = {
    "A": REPO / "outputs" / "audit" / "audit_sheet.csv",
    "B": REPO / "outputs" / "audit_v2" / "audit_sheet.csv",
    "C": REPO / "outputs" / "audit_plane" / "audit_sheet.csv",
}
MAX_W = 1024
JPEG_QUALITY = 87


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def name_idx(name):
    m = re.match(r"^(.*?)(\d+)$", name)
    if not m:
        raise ValueError("unparseable object name: %r" % name)
    return int(m.group(2))


def main():
    blind = read_csv(BLIND / "audit_sheet_blind.csv")
    key = {int(r["id"]): r["_key"] for r in read_csv(BLIND / "_key_do_not_share.csv")}
    sheets = {p: {r["id"]: r for r in read_csv(path)} for p, path in SOURCE_SHEETS.items()}

    (SITE / "img").mkdir(parents=True, exist_ok=True)
    items, errors = [], []

    for row in blind:
        bid = int(row["id"])
        prefix, src_id = key[bid].split(":")
        src = sheets[prefix][src_id]

        # sanity: the blind sheet must be the same claim as the source row
        for col in ("subject", "predicate", "object"):
            if row[col] != src[col]:
                errors.append("item %d: %s mismatch (%r vs %r)" % (bid, col, row[col], src[col]))

        group, stem = src["image_id"].split("/")
        geo = json.loads((GEO / group / (stem + ".json")).read_text(encoding="utf-8"))
        by_idx = {o["idx"]: o for o in geo}
        try:
            sb = by_idx[name_idx(row["subject"])]["box"]
            ob = by_idx[name_idx(row["object"])]["box"]
        except KeyError as e:
            errors.append("item %d: missing geometry idx %s in %s" % (bid, e, src["image_id"]))
            continue

        img_path = DATASET_IMG / group / (stem + ".jpg")
        img = ImageOps.exif_transpose(Image.open(img_path)).convert("RGB")
        if img.width > MAX_W:
            img = img.resize((MAX_W, round(img.height * MAX_W / img.width)), Image.LANCZOS)
        out_name = "%03d.jpg" % bid
        img.save(SITE / "img" / out_name, quality=JPEG_QUALITY, optimize=True)

        items.append({
            "id": bid, "img": out_name,
            "s": row["subject"], "p": row["predicate"], "o": row["object"],
            "sb": [round(v, 4) for v in sb],
            "ob": [round(v, 4) for v in ob],
        })

    if errors:
        print("FAILED:\n" + "\n".join(errors))
        return 1

    items.sort(key=lambda x: x["id"])
    with open(SITE / "items.json", "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(it, separators=(",", ":")) for it in items) + "\n]\n")

    # drop the old banner renders
    removed = 0
    for p in (SITE / "img").glob("*.png"):
        p.unlink()
        removed += 1
    print("rendered %d clean images -> %s (removed %d old PNGs)" % (len(items), SITE / "img", removed))
    sizes = sorted(p.stat().st_size for p in (SITE / "img").glob("*.jpg"))
    print("jpeg sizes: min %dK / median %dK / max %dK / total %.1fM"
          % (sizes[0] // 1024, sizes[len(sizes) // 2] // 1024, sizes[-1] // 1024, sum(sizes) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
