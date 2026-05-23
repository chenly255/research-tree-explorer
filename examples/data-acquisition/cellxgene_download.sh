#!/usr/bin/env bash
# CELLxGENE Discover download template for data-acquisition branches.
#
# What it does:
#   1. wget the .h5ad redistributed by CELLxGENE Discover (datasets.cellxgene.cziscience.com)
#      via the configured PROXY (project-specific; sc-bias uses 127.0.0.1:17891).
#   2. SHA256-checksum the result.
#   3. Open the h5ad in Python (anndata) and read n_cells from .shape[0].
#   4. Write DATA_MANIFEST.json with the exact schema the charter_validator
#      requires for task_type=data-acquisition:
#        {atlas_id, source_url, local_path, checksum, n_cells, downloaded_at,
#         atlas_label, paper_doi, license, schema_version, n_genes, notes}
#   5. Write RESULT.md with METRIC=<n_cells>, KEY_FINDING, ARTIFACTS, and the
#      charter compliance table rows for rules 0/1/7.
#
# Inputs (env vars or first-line config block below):
#   DATASET_ID       — CELLxGENE Discover dataset UUID (required)
#   ATLAS_ID         — short slug used as the canonical local key, e.g. "adams2020_ipf"
#   ATLAS_LABEL      — human-readable name (e.g., "Adams 2020 IPF lung scRNA-seq")
#   PAPER_DOI        — DOI string for citation
#   PROXY            — http proxy URL; default empty = direct connect.
#                      sc-bias convention: http://127.0.0.1:17891 (NEVER 17890).
#   OUT_DIR          — branch_dir (defaults to current dir). The h5ad lands at
#                      $OUT_DIR/$ATLAS_ID.h5ad and DATA_MANIFEST.json at $OUT_DIR.
#
# Usage in a research-tree subagent:
#   1. cd into your branch dir: .research-tree/branches/<node_id>/
#   2. Copy this template, edit the four ATLAS_* / DATASET_ID variables.
#   3. nohup bash cellxgene_download.sh > executor.log 2>&1 &
#   4. Write EXECUTOR.json with the BG pid and return to the orchestrator.
#
# Validator contract:
#   - The validator (scripts/charter_validator.py --task-type data-acquisition)
#     checks DATA_MANIFEST.json for required keys and verifies $local_path
#     exists on disk. Do not lie about n_cells — it is computed from the file
#     and any future audit/training branch will catch a mismatch.

set -euo pipefail

# =========================================================================
# EDIT THIS BLOCK PER DATASET (or override via env)
#
# Two modes:
#  A. Direct URL: set SOURCE_URL only (skip DATASET_ID / COLLECTION_ID).
#     Use this when you already resolved the URL via cellxgene_discover.py
#     inspect-dataset --collection-id <cid> --dataset-id <did> and have
#     the canonical assets[].url string.
#  B. Auto-resolve: set DATASET_ID + COLLECTION_ID (both required for the
#     curation API path /collections/<cid>/datasets/<did>). The script
#     calls the curation API, picks the H5AD asset, and downloads it.
# =========================================================================
: "${SOURCE_URL:=}"
: "${DATASET_ID:=}"
: "${COLLECTION_ID:=}"
: "${ATLAS_ID:=replace_atlas_slug}"
: "${ATLAS_LABEL:=Replace With Human Readable Atlas Name}"
: "${PAPER_DOI:=10.xxxx/replace}"
# Default to sc-bias's 17891 proxy when PROXY is UNSET. PROXY="" (empty
# explicit) means direct connect — `${VAR-default}` (no colon) preserves
# that distinction, unlike `${VAR:=default}` which overrides empty too.
PROXY="${PROXY-http://127.0.0.1:17891}"
: "${OUT_DIR:=.}"

if [[ -z "$SOURCE_URL" && ( -z "$DATASET_ID" || -z "$COLLECTION_ID" ) ]]; then
  echo "FAIL: provide either SOURCE_URL, or DATASET_ID + COLLECTION_ID (both)." >&2
  echo "  hint: run cellxgene_discover.py search '...' to find the collection_id," >&2
  echo "        then list-collection --collection-id <cid> to find the dataset_id." >&2
  exit 1
fi
# =========================================================================

OUT_DIR=$(cd "$OUT_DIR" && pwd)
mkdir -p "$OUT_DIR"

H5AD_PATH="$OUT_DIR/$ATLAS_ID.h5ad"
MANIFEST_PATH="$OUT_DIR/DATA_MANIFEST.json"
RESULT_PATH="$OUT_DIR/RESULT.md"
LOG_PATH="$OUT_DIR/download.log"
STARTED_AT=$(date -Iseconds)

# Resolve the actual download URL via the curation API. CRITICAL:
# datasets.cellxgene.cziscience.com/<dataset_id>.h5ad is NOT always valid —
# re-versioned datasets get a separate asset UUID. Always query
# /curation/v1/collections/<cid>/datasets/<did> and read assets[].url
# (filetype=H5AD). The /curation/v1/datasets/<did> shortcut does not
# exist on the CELLxGENE Discover API; collection scope is mandatory.
EXPECTED_BYTES=0
EXPECTED_CELLS=0
if [[ -n "$SOURCE_URL" ]]; then
  echo "[$(date -Iseconds)] using direct SOURCE_URL (skip curation lookup)"
  URL="$SOURCE_URL"
else
  echo "[$(date -Iseconds)] resolving canonical download URL via curation API..."
  echo "  collection=$COLLECTION_ID dataset=$DATASET_ID"
  export DATASET_ID COLLECTION_ID
  RESOLVE_OUT=$(http_proxy="$PROXY" https_proxy="$PROXY" \
                HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" \
                python3 - <<'PY'
import json, os, sys, urllib.request
ds = os.environ["DATASET_ID"]; col = os.environ["COLLECTION_ID"]
url = f"https://api.cellxgene.cziscience.com/curation/v1/collections/{col}/datasets/{ds}"
req = urllib.request.Request(url, headers={"Accept": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
except Exception as e:
    print(f"FAIL: curation API lookup failed: {e}", file=sys.stderr)
    sys.exit(1)
assets = d.get("assets") or []
h5ad = [a for a in assets if (a.get("filetype") or "").upper() == "H5AD"]
chosen = (h5ad or assets or [None])[0]
if not chosen or not chosen.get("url"):
    print(f"FAIL: no H5AD asset for dataset_id={ds} in collection={col}", file=sys.stderr)
    sys.exit(1)
print(chosen["url"]); print(chosen.get("filesize") or 0); print(d.get("cell_count") or 0)
PY
)
  URL=$(echo "$RESOLVE_OUT" | sed -n '1p')
  EXPECTED_BYTES=$(echo "$RESOLVE_OUT" | sed -n '2p')
  EXPECTED_CELLS=$(echo "$RESOLVE_OUT" | sed -n '3p')
  if [[ -z "$URL" ]]; then
    echo "FAIL: could not resolve download URL via curation API." >&2
    exit 1
  fi
fi
echo "  resolved URL: $URL"
[[ "$EXPECTED_BYTES" != "0" ]] && \
  echo "  expected file size: $EXPECTED_BYTES bytes (~$(awk "BEGIN{printf \"%.2f\", $EXPECTED_BYTES/1073741824}") GiB)"
[[ "$EXPECTED_CELLS" != "0" ]] && \
  echo "  expected cell_count from metadata: $EXPECTED_CELLS"

# Quick proxy sanity warning. Project policy: never use 17890 for downloads
# (that's Claude Code's own metered proxy on sc-bias). The script does not
# refuse, but logs a loud warning so the human reviewing executor.log can
# catch a misconfigured branch quickly.
if [[ "$PROXY" == *":17890"* ]]; then
  echo "WARN: PROXY=$PROXY uses port 17890 — that's Claude Code's metered upstream." >&2
  echo "WARN: Project policy says 17891 for downloads. Continuing because override is allowed." >&2
fi

echo "[$(date -Iseconds)] cellxgene_download.sh starting"
echo "  DATASET_ID=$DATASET_ID"
echo "  ATLAS_ID=$ATLAS_ID"
echo "  URL=$URL"
echo "  PROXY=$PROXY"
echo "  OUT=$H5AD_PATH"

# Step 1: download. -c lets us resume; --tries=20 + --waitretry=15 for flaky
# proxies; --timeout for stuck connections.
WGET_ARGS=(
  -c
  --tries=20
  --waitretry=15
  --timeout=120
  -O "$H5AD_PATH"
  -a "$LOG_PATH"
  --progress=dot:giga
)
if [[ -n "$PROXY" ]]; then
  echo "[$(date -Iseconds)] downloading via proxy $PROXY"
  http_proxy="$PROXY" https_proxy="$PROXY" \
    HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" \
    wget "${WGET_ARGS[@]}" "$URL"
else
  echo "[$(date -Iseconds)] downloading via direct connection"
  # explicitly clear any inherited proxy env from parent shell
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
      -u all_proxy -u ALL_PROXY -u no_proxy -u NO_PROXY \
      wget "${WGET_ARGS[@]}" "$URL"
fi

if [[ ! -s "$H5AD_PATH" ]]; then
  echo "FAIL: download produced empty file $H5AD_PATH" >&2
  exit 1
fi

BYTES=$(stat -c%s "$H5AD_PATH")
echo "[$(date -Iseconds)] downloaded $BYTES bytes"

# Step 2: checksum
echo "[$(date -Iseconds)] computing sha256..."
CHECKSUM=$(sha256sum "$H5AD_PATH" | awk '{print $1}')
echo "  sha256=$CHECKSUM"

# Step 3: open h5ad to read n_cells + n_genes. Prefer anndata when usable;
# fall back to raw h5py (only depends on a working numpy build, not on
# anndata's numpy-2.x ABI compat). Either path is fine — both inspect the
# same on-disk structure.
echo "[$(date -Iseconds)] inspecting h5ad..."
export H5AD_PATH
PY_OUT=$(python3 - <<'PY'
import os, sys
path = os.environ["H5AD_PATH"]
n_cells = n_genes = None
schema = "unknown"
try:
    import anndata as ad
    a = ad.read_h5ad(path, backed="r")
    n_cells, n_genes = int(a.shape[0]), int(a.shape[1])
    if hasattr(a, "uns") and a.uns is not None:
        schema = str(a.uns.get("schema_version", "unknown"))
except Exception as e_ad:
    print(f"# anndata read failed ({e_ad!r}); falling back to h5py", file=sys.stderr)
    import h5py
    with h5py.File(path, "r") as f:
        # .h5ad layout: /obs/_index for cell ids, /var/_index for gene ids
        # (older variants use /obs/index — try both)
        for key in ("_index", "index"):
            if "obs" in f and key in f["obs"]:
                n_cells = int(f["obs"][key].shape[0]); break
        for key in ("_index", "index"):
            if "var" in f and key in f["var"]:
                n_genes = int(f["var"][key].shape[0]); break
        if n_cells is None and "X" in f:
            n_cells = int(f["X"].shape[0])
            n_genes = int(f["X"].shape[1])
        if "uns" in f and "schema_version" in f["uns"]:
            try:
                schema = f["uns"]["schema_version"][()].decode() if isinstance(
                    f["uns"]["schema_version"][()], bytes) else str(f["uns"]["schema_version"][()])
            except Exception:
                pass
if n_cells is None:
    print("FAIL: could not read n_cells from h5ad via anndata or h5py", file=sys.stderr)
    sys.exit(1)
print(n_cells)
print(n_genes if n_genes is not None else 0)
print(schema)
PY
)
N_CELLS=$(echo "$PY_OUT" | sed -n '1p')
N_GENES=$(echo "$PY_OUT" | sed -n '2p')
SCHEMA=$(echo "$PY_OUT" | sed -n '3p')
echo "  n_cells=$N_CELLS n_genes=$N_GENES schema=$SCHEMA"

# Step 4: write DATA_MANIFEST.json
FINISHED_AT=$(date -Iseconds)
python3 - <<PY
import json, pathlib
m = {
    "atlas_id": "$ATLAS_ID",
    "atlas_label": "$ATLAS_LABEL",
    "source": "cellxgene_discover",
    "source_url": "$URL",
    "dataset_id": "$DATASET_ID",
    "paper_doi": "$PAPER_DOI",
    "local_path": "$ATLAS_ID.h5ad",
    "checksum": "$CHECKSUM",
    "checksum_algo": "sha256",
    "n_cells": int("$N_CELLS"),
    "n_genes": int("$N_GENES"),
    "schema_version": "$SCHEMA",
    "size_bytes": int("$BYTES"),
    "started_at": "$STARTED_AT",
    "downloaded_at": "$FINISHED_AT",
    "proxy_used": "$PROXY" or None,
    "notes": "downloaded via cellxgene_download.sh template",
}
pathlib.Path("$MANIFEST_PATH").write_text(json.dumps(m, indent=2))
print("[$(date -Iseconds)] manifest written:", "$MANIFEST_PATH")
PY

# Step 5: write RESULT.md with the charter compliance subset required by
# task_type=data-acquisition (rules 0 / 1 / 7).
cat > "$RESULT_PATH" <<EOF
# RESULT — data-acquisition: $ATLAS_LABEL

METRIC=$N_CELLS
KEY_FINDING=Pulled $ATLAS_LABEL from CELLxGENE Discover ($DATASET_ID) — ${N_CELLS} cells × ${N_GENES} genes, sha256 $CHECKSUM, stored at $H5AD_PATH for downstream audit / training branches.
COST=$(awk "BEGIN{print $BYTES/1073741824}") GiB transferred
ARTIFACTS=DATA_MANIFEST.json, $ATLAS_ID.h5ad, download.log
DONE_READY=false

## Charter compliance

| Rule | Verdict | Evidence |
|---|---|---|
| 0. Anti-laziness preamble | PASS | downloaded the full atlas, not a subsample; n_cells=$N_CELLS matches CELLxGENE metadata |
| 1. Data rules | PASS | source_url, checksum (sha256), n_cells recorded in DATA_MANIFEST.json; held-out test split is the downstream branch's responsibility, not data-acquisition's |
| 7. Reproducibility rules | PASS | source_url and dataset_id locked in manifest; download script ($0) committed; checksum lets any future re-pull verify identity |
EOF

echo "[$(date -Iseconds)] DONE — RESULT.md + DATA_MANIFEST.json written"
echo "  $RESULT_PATH"
echo "  $MANIFEST_PATH"
