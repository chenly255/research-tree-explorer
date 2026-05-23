#!/usr/bin/env python3
"""CELLxGENE Discover dataset finder.

Use when an autopilot data-acquisition branch knows a paper / disease /
tissue / author but not the dataset UUID. Queries the public Discover
collections API (no auth, no proxy needed if you have direct internet;
goes through the configured PROXY otherwise).

Examples:
    # find lupus PBMC atlases mentioning "Perez"
    python3 cellxgene_discover.py search --query "Perez lupus PBMC"

    # list datasets in a known collection
    python3 cellxgene_discover.py list-collection \\
        --collection-id e2c257e7-6f79-487c-b81c-39451cd4ab3c

    # resolve a dataset_id to its download url + metadata
    python3 cellxgene_discover.py inspect-dataset \\
        --dataset-id 826f451b-68ac-4775-bbfa-6816e33f0091

Output is a single JSON object on stdout; pretty diagnostics go to stderr.

Auth: none. The Discover collections/datasets API is fully public.
Rate-limit: be polite, single-digit RPS is fine.
Source: https://api.cellxgene.cziscience.com/dp/v1/ (current as of v1 LTS).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Curation API exposes collection metadata (name, doi, datasets+disease+tissue)
# in a single call. The older /dp/v1/collections endpoint returns only ids,
# which is useless for keyword search.
CURATION = "https://api.cellxgene.cziscience.com/curation/v1"
DP = "https://api.cellxgene.cziscience.com/dp/v1"
COLLECTIONS_URL = f"{CURATION}/collections"
COLLECTION_URL = f"{CURATION}/collections"   # +/{collection_id}
DATASET_URL = f"{DP}/datasets"               # +/{dataset_id}
DISCOVER_DOWNLOAD = "https://datasets.cellxgene.cziscience.com/{dataset_id}.h5ad"


def _http_get(url: str, timeout: int = 60) -> dict:
    """GET JSON. Respects http_proxy / https_proxy env vars (urllib does so by default)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "rte-discover/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} on {url}", file=sys.stderr)
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            print(err_body[:1024], file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"URLError on {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _flat_text(c: dict) -> str:
    """Concatenate searchable fields from a curation-API collection record."""
    parts = [
        c.get("name") or "",
        (c.get("description") or "")[:2000],
        (c.get("publisher_metadata") or {}).get("journal", "") or "",
    ]
    # author names + paper title give us the "Wu 2021 breast" matchability
    pm = c.get("publisher_metadata") or {}
    for a in pm.get("authors") or []:
        parts.append(a.get("family", ""))
        parts.append(a.get("given", ""))
    for d in c.get("datasets") or []:
        parts.append(d.get("title", "") or "")
        for k in ("tissue", "disease", "cell_type"):
            for x in d.get(k) or []:
                parts.append((x or {}).get("label") or "")
    return " | ".join(parts).lower()


def cmd_search(args: argparse.Namespace) -> int:
    """Substring search across collection name + description + paper authors +
    dataset titles + tissue/disease labels (curation API, single bulk call)."""
    cols = _http_get(COLLECTIONS_URL)
    if isinstance(cols, dict) and "collections" in cols:
        cols = cols["collections"]
    q = args.query.lower()
    tokens = q.split()
    hits: list[dict[str, Any]] = []
    for c in cols:
        flat = _flat_text(c)
        if all(tok in flat for tok in tokens):
            datasets_summary = []
            for d in (c.get("datasets") or [])[:5]:
                datasets_summary.append({
                    "dataset_id": d.get("dataset_id"),
                    "title": (d.get("title") or "")[:120],
                    "cell_count": d.get("cell_count"),
                })
            pm = c.get("publisher_metadata") or {}
            hits.append({
                "collection_id": c.get("collection_id"),
                "name": c.get("name"),
                "doi": c.get("doi"),
                "journal": pm.get("journal"),
                "n_datasets": len(c.get("datasets") or []),
                "datasets_preview": datasets_summary,
            })
    out = {"query": args.query, "n_collections_total": len(cols), "n_matches": len(hits), "matches": hits[: args.limit]}
    print(json.dumps(out, indent=2))
    return 0


def cmd_list_collection(args: argparse.Namespace) -> int:
    url = f"{COLLECTION_URL}/{args.collection_id}"
    data = _http_get(url)
    out: dict[str, Any] = {
        "collection_id": args.collection_id,
        "name": data.get("name"),
        "doi": data.get("doi"),
        "description": (data.get("description") or "")[:400],
        "publisher_metadata": data.get("publisher_metadata"),
        "datasets": [],
    }
    for d in data.get("datasets") or []:
        dataset_id = d.get("dataset_id")  # curation API field
        # Resolve canonical download URL from assets (NOT pattern-based)
        assets = d.get("assets") or []
        h5ad_assets = [a for a in assets if (a.get("filetype") or "").upper() == "H5AD"]
        chosen = h5ad_assets[0] if h5ad_assets else (assets[0] if assets else None)
        download_url = chosen["url"] if chosen and chosen.get("url") else \
            (DISCOVER_DOWNLOAD.format(dataset_id=dataset_id) if dataset_id else None)
        out["datasets"].append(
            {
                "dataset_id": dataset_id,
                "title": d.get("title"),
                "cell_count": d.get("cell_count"),
                "assay": [a.get("label") for a in (d.get("assay") or [])],
                "tissue": [t.get("label") for t in (d.get("tissue") or [])],
                "disease": [x.get("label") for x in (d.get("disease") or [])],
                "schema_version": d.get("schema_version"),
                "download_url": download_url,
                "filesize_bytes": chosen.get("filesize") if chosen else None,
            }
        )
    print(json.dumps(out, indent=2))
    return 0


def cmd_inspect_dataset(args: argparse.Namespace) -> int:
    """Resolve a dataset_id to its actual download URL via the curation API.

    IMPORTANT: the canonical download URL is `assets[].url`, NOT
    `datasets.cellxgene.cziscience.com/<dataset_id>.h5ad`. Datasets get
    re-versioned and the asset UUID can differ from the dataset_id. Always
    use the resolved URL for downloads.
    """
    if args.collection_id:
        url = f"{COLLECTION_URL}/{args.collection_id}/datasets/{args.dataset_id}"
    else:
        # curation API supports the direct dataset endpoint too
        url = f"{CURATION}/datasets/{args.dataset_id}"
    d = _http_get(url)

    # Pick the H5AD asset (curation API may also list RDS); prefer h5ad.
    assets = d.get("assets") or []
    h5ad_assets = [a for a in assets if (a.get("filetype") or "").upper() == "H5AD"]
    chosen = h5ad_assets[0] if h5ad_assets else (assets[0] if assets else None)

    resolved_url = chosen["url"] if chosen and chosen.get("url") else None
    resolved_size = chosen["filesize"] if chosen and chosen.get("filesize") else None
    fallback_url = DISCOVER_DOWNLOAD.format(dataset_id=args.dataset_id)

    out = {
        "dataset_id": args.dataset_id,
        "title": d.get("title"),
        "collection_id": d.get("collection_id") or args.collection_id,
        "cell_count": d.get("cell_count"),
        "assay": [a.get("label") for a in (d.get("assay") or [])],
        "tissue": [t.get("label") for t in (d.get("tissue") or [])],
        "disease": [x.get("label") for x in (d.get("disease") or [])],
        "organism": [o.get("label") for o in (d.get("organism") or [])],
        "schema_version": d.get("schema_version"),
        "explorer_url": d.get("explorer_url"),
        # Canonical: use resolved_url. fallback_url works for some datasets
        # but NOT all; never trust it without verifying.
        "download_url": resolved_url or fallback_url,
        "download_url_source": "curation_api_assets" if resolved_url else "dataset_id_pattern_fallback",
        "filesize_bytes": resolved_size,
        "all_assets": assets,
    }
    print(json.dumps(out, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_s = sub.add_parser("search", help="search collections by name substrings")
    sp_s.add_argument("--query", required=True, help="space-separated tokens (AND match against collection name)")
    sp_s.add_argument("--limit", type=int, default=10)

    sp_lc = sub.add_parser("list-collection", help="list datasets in a collection")
    sp_lc.add_argument("--collection-id", required=True)

    sp_id = sub.add_parser("inspect-dataset", help="show metadata + download_url for one dataset_id")
    sp_id.add_argument("--dataset-id", required=True)
    sp_id.add_argument("--collection-id", default=None,
                       help="optional; enables curation API endpoint (richer metadata)")

    args = ap.parse_args()

    if args.cmd == "search":
        return cmd_search(args)
    if args.cmd == "list-collection":
        return cmd_list_collection(args)
    if args.cmd == "inspect-dataset":
        return cmd_inspect_dataset(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
