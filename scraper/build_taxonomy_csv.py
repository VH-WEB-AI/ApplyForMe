"""Writes the industry x category x sub-category master CSV.

    python3 build_taxonomy_csv.py --out job_taxonomy_master.csv

Each row is one valid combination an admin dropdown may offer. `relevance` says
why the row exists: "core" means the function defines that industry, "support"
means it is back-office work every employer has. Filter on it when you want a
tighter list.
"""
import argparse
import csv
import re

from taxonomy_master import (
    CATEGORY_TREE,
    CATEGORY_TREE,
    INDUSTRIES,
    SUPPORT_SUBSETS,
    UNIVERSAL_CATEGORIES,
)

FIELDNAMES = [
    "industry_id",
    "industry",
    "category",
    "sub_category",
    "relevance",
    "code",
]


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def build_rows():
    rows = []
    for industry, (industry_id, core_categories) in INDUSTRIES.items():
        # Core first, then the universal ones the industry hasn't already
        # claimed as core — a category is never emitted twice for one industry.
        ordered = list(core_categories)
        ordered += [c for c in UNIVERSAL_CATEGORIES if c not in core_categories]
        for category in ordered:
            is_core = category in core_categories
            if is_core:
                sub_categories = CATEGORY_TREE[category]
            else:
                sub_categories = SUPPORT_SUBSETS.get(category, CATEGORY_TREE[category])
            for sub_category in sub_categories:
                rows.append({
                    "industry_id": industry_id,
                    "industry": industry,
                    "category": category,
                    "sub_category": sub_category,
                    "relevance": "core" if is_core else "support",
                    "code": f"{slug(industry)}|{slug(category)}|{slug(sub_category)}",
                })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="job_taxonomy_master.csv")
    parser.add_argument("--core-only", action="store_true",
                        help="drop the support rows, keeping only industry-defining functions")
    parser.add_argument("--pairs-out",
                        help="also write the deduped category/sub-category reference list here")
    args = parser.parse_args()

    rows = build_rows()
    if args.core_only:
        rows = [r for r in rows if r["relevance"] == "core"]

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    if args.pairs_out:
        # The category axis on its own — what a two-level category dropdown needs,
        # with no industry repetition.
        seen, pairs_rows = set(), []
        for category, sub_categories in CATEGORY_TREE.items():
            for sub_category in sub_categories:
                if (category, sub_category) in seen:
                    continue
                seen.add((category, sub_category))
                pairs_rows.append({
                    "category": category,
                    "sub_category": sub_category,
                    "code": f"{slug(category)}|{slug(sub_category)}",
                })
        with open(args.pairs_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["category", "sub_category", "code"],
                                    quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(pairs_rows)
        print(f"wrote {len(pairs_rows):,} rows -> {args.pairs_out}")

    industries = {r["industry"] for r in rows}
    categories = {r["category"] for r in rows}
    pairs = {(r["category"], r["sub_category"]) for r in rows}
    duplicate_codes = len(rows) - len({r["code"] for r in rows})

    print(f"wrote {len(rows):,} rows -> {args.out}")
    print(f"  industries      : {len(industries)}")
    print(f"  categories      : {len(categories)}")
    print(f"  sub-categories  : {len(pairs)} unique category/sub-category pairs")
    print(f"  core rows       : {sum(1 for r in rows if r['relevance'] == 'core'):,}")
    print(f"  support rows    : {sum(1 for r in rows if r['relevance'] == 'support'):,}")
    print(f"  duplicate codes : {duplicate_codes}")


if __name__ == "__main__":
    main()
