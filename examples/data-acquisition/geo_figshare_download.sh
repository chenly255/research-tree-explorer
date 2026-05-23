#!/usr/bin/env bash
# Generic GEO / figshare / Zenodo / GitHub-Releases download template for
# data-acquisition branches. Use when the dataset is NOT redistributed on
# CELLxGENE Discover. For CELLxGENE Discover, use cellxgene_download.sh.
#
# What it does:
#   1. wget any single-file URL (.h5ad, .h5, .rds, .tar.gz, .mtx.gz, .zip, ...)
#      via the configured PROXY.
#   2. SHA256-checksum the result.
#   3. If the file is .h5ad, count cells with anndata. If anything else, the
#      caller MUST pass N_CELLS as an env var (we cannot count cells from
#      .rds without R; we cannot count cells from a .tar.gz without unpacking).
#      Failing to pass N_CELLS for non-h5ad files = manifest fails validation.
#   4. Write DATA_MANIFEST.json with the schema charter_validator demands.
#   5. Write RESULT.md with the data-acquisition charter table (rules 0/1/7).
#
# Inputs (env vars):
#   SOURCE_URL       — direct file URL (required)
#   ATLAS_ID         — short slug, e.g. "wu2021_bc"
#   ATLAS_LABEL      — human-readable name
#   PAPER_DOI        — DOI for citation
#   N_CELLS          — REQUIRED for non-h5ad files; OPTIONAL for h5ad
#                      (auto-computed when omitted)
#   FILENAME         — output filename within branch dir
#                      (defaults to basename of SOURCE_URL)
#   PROXY            — http proxy URL; sc-bias default 127.0.0.1:17891
#   OUT_DIR          — branch dir (default: current dir)
#   POST_DOWNLOAD_CMD — optional shell snippet to run after download
#                       (e.g., "tar xzf $FILENAME && rm $FILENAME" for archives).
#                       Receives env vars: $FILENAME, $OUT_DIR, $ATLAS_ID.
#                       If used, you MUST also set EXTRACTED_LOCAL_PATH so the
#                       manifest's local_path points to the extracted artifact
#                       the validator will check.
#   EXTRACTED_LOCAL_PATH — relative path inside OUT_DIR that the validator
#                          will verify exists (used when POST_DOWNLOAD_CMD
#                          unpacks an archive)
#
# GEO ftp URL example:
#   https://ftp.ncbi.nlm.nih.gov/geo/series/GSE193nnn/GSE193581/suppl/GSE193581_RAW.tar
# figshare DOI to direct URL: look up via figshare API; example direct:
#   https://ndownloader.figshare.com/files/12345678
# Zenodo:
#   https://zenodo.org/record/<id>/files/<filename>?download=1
# GitHub releases:
#   https://github.com/<org>/<repo>/releases/download/<tag>/<filename>

set -euo pipefail

: "${SOURCE_URL:?must set SOURCE_URL}"
: "${ATLAS_ID:=replace_atlas_slug}"
: "${ATLAS_LABEL:=Replace Atlas Label}"
: "${PAPER_DOI:=10.xxxx/replace}"
# `${VAR-default}` (no colon) so PROXY="" explicit means direct connect
PROXY="${PROXY-http://127.0.0.1:17891}"
: "${OUT_DIR:=.}"
: "${FILENAME:=}"
: "${N_CELLS:=}"
: "${POST_DOWNLOAD_CMD:=}"
: "${EXTRACTED_LOCAL_PATH:=}"

OUT_DIR=$(cd "$OUT_DIR" && pwd)
mkdir -p "$OUT_DIR"

if [[ -z "$FILENAME" ]]; then
  FILENAME=$(basename "${SOURCE_URL%%\?*}")
fi
DL_PATH="$OUT_DIR/$FILENAME"
MANIFEST_PATH="$OUT_DIR/DATA_MANIFEST.json"
RESULT_PATH="$OUT_DIR/RESULT.md"
LOG_PATH="$OUT_DIR/download.log"
STARTED_AT=$(date -Iseconds)

if [[ "$PROXY" == *":17890"* ]]; then
  echo "WARN: PROXY=$PROXY uses port 17890 — that's Claude Code's metered upstream." >&2
  echo "WARN: Project policy says 17891 for downloads. Continuing because override is allowed." >&2
fi

echo "[$(date -Iseconds)] geo_figshare_download.sh starting"
echo "  SOURCE_URL=$SOURCE_URL"
echo "  ATLAS_ID=$ATLAS_ID"
echo "  OUT=$DL_PATH"
echo "  PROXY=$PROXY"

WGET_ARGS=(
  -c
  --tries=20
  --waitretry=15
  --timeout=120
  -O "$DL_PATH"
  -a "$LOG_PATH"
  --progress=dot:giga
)
if [[ -n "$PROXY" ]]; then
  echo "[$(date -Iseconds)] downloading via proxy $PROXY"
  http_proxy="$PROXY" https_proxy="$PROXY" \
    HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" \
    wget "${WGET_ARGS[@]}" "$SOURCE_URL"
else
  echo "[$(date -Iseconds)] downloading via direct connection"
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
      -u all_proxy -u ALL_PROXY -u no_proxy -u NO_PROXY \
      wget "${WGET_ARGS[@]}" "$SOURCE_URL"
fi

if [[ ! -s "$DL_PATH" ]]; then
  echo "FAIL: download produced empty file $DL_PATH" >&2
  exit 1
fi

BYTES=$(stat -c%s "$DL_PATH")
echo "[$(date -Iseconds)] downloaded $BYTES bytes"

# Optional post-download (unpack archive, convert format, etc.)
LOCAL_PATH_REL="$FILENAME"
if [[ -n "$POST_DOWNLOAD_CMD" ]]; then
  echo "[$(date -Iseconds)] running POST_DOWNLOAD_CMD..."
  (cd "$OUT_DIR" && FILENAME="$FILENAME" ATLAS_ID="$ATLAS_ID" bash -c "$POST_DOWNLOAD_CMD")
  if [[ -n "$EXTRACTED_LOCAL_PATH" ]]; then
    LOCAL_PATH_REL="$EXTRACTED_LOCAL_PATH"
    if [[ ! -e "$OUT_DIR/$LOCAL_PATH_REL" ]]; then
      echo "FAIL: POST_DOWNLOAD_CMD did not produce expected $LOCAL_PATH_REL" >&2
      exit 1
    fi
  fi
fi

CHECKSUM_TARGET="$OUT_DIR/$LOCAL_PATH_REL"
if [[ ! -e "$CHECKSUM_TARGET" ]]; then
  echo "FAIL: checksum target $CHECKSUM_TARGET does not exist" >&2
  exit 1
fi

# If the target is a directory (e.g. unpacked MEX), tar+sha256 to get a
# deterministic content hash. Otherwise hash the file directly.
echo "[$(date -Iseconds)] computing sha256..."
if [[ -d "$CHECKSUM_TARGET" ]]; then
  CHECKSUM=$(cd "$OUT_DIR" && tar --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - "$LOCAL_PATH_REL" | sha256sum | awk '{print $1}')
  echo "  sha256 (tar)=$CHECKSUM"
else
  CHECKSUM=$(sha256sum "$CHECKSUM_TARGET" | awk '{print $1}')
  echo "  sha256=$CHECKSUM"
fi

# n_cells: auto-detect h5ad, otherwise require caller-supplied N_CELLS.
N_GENES=""
if [[ -z "$N_CELLS" ]]; then
  if [[ "$LOCAL_PATH_REL" == *.h5ad ]]; then
    echo "[$(date -Iseconds)] reading n_cells from h5ad (anndata, fallback h5py)..."
    export H5AD_INSPECT_PATH="$CHECKSUM_TARGET"
    PY_OUT=$(python3 - <<'PY'
import os, sys
path = os.environ["H5AD_INSPECT_PATH"]
n_cells = n_genes = None
try:
    import anndata as ad
    a = ad.read_h5ad(path, backed="r")
    n_cells, n_genes = int(a.shape[0]), int(a.shape[1])
except Exception as e:
    print(f"# anndata failed ({e!r}); falling back to h5py", file=sys.stderr)
    import h5py
    with h5py.File(path, "r") as f:
        # Modern .h5ad: X is CSR group with attrs['shape']; reliable single source
        if "X" in f and isinstance(f["X"], h5py.Group):
            shp = f["X"].attrs.get("shape")
            if shp is not None and len(shp) == 2:
                n_cells, n_genes = int(shp[0]), int(shp[1])
        if n_cells is None and "X" in f and isinstance(f["X"], h5py.Dataset):
            n_cells, n_genes = int(f["X"].shape[0]), int(f["X"].shape[1])
        if n_cells is None and "obs" in f:
            idx = f["obs"].attrs.get("_index", "_index")
            if isinstance(idx, bytes): idx = idx.decode()
            if idx in f["obs"] and isinstance(f["obs"][idx], h5py.Dataset):
                n_cells = int(f["obs"][idx].shape[0])
        if n_genes is None and "var" in f:
            idx = f["var"].attrs.get("_index", "_index")
            if isinstance(idx, bytes): idx = idx.decode()
            if idx in f["var"] and isinstance(f["var"][idx], h5py.Dataset):
                n_genes = int(f["var"][idx].shape[0])
if n_cells is None:
    print("FAIL: could not read n_cells from h5ad", file=sys.stderr); sys.exit(1)
print(n_cells); print(n_genes if n_genes is not None else 0)
PY
)
    N_CELLS=$(echo "$PY_OUT" | sed -n '1p')
    N_GENES=$(echo "$PY_OUT" | sed -n '2p')
  else
    echo "FAIL: file is not .h5ad and N_CELLS not provided." >&2
    echo "  pass N_CELLS=<int> env var (from the paper / GEO description) so the manifest can record it." >&2
    exit 1
  fi
fi
echo "  n_cells=$N_CELLS n_genes=${N_GENES:-unknown}"

FINISHED_AT=$(date -Iseconds)
python3 - <<PY
import json, pathlib
m = {
    "atlas_id": "$ATLAS_ID",
    "atlas_label": "$ATLAS_LABEL",
    "source": "geo_or_figshare_or_zenodo",
    "source_url": "$SOURCE_URL",
    "paper_doi": "$PAPER_DOI",
    "local_path": "$LOCAL_PATH_REL",
    "checksum": "$CHECKSUM",
    "checksum_algo": "sha256",
    "n_cells": int("$N_CELLS"),
    "n_genes": int("$N_GENES") if "$N_GENES" else None,
    "size_bytes": int("$BYTES"),
    "started_at": "$STARTED_AT",
    "downloaded_at": "$FINISHED_AT",
    "proxy_used": "$PROXY" or None,
    "notes": "downloaded via geo_figshare_download.sh template",
}
pathlib.Path("$MANIFEST_PATH").write_text(json.dumps(m, indent=2))
print("[$(date -Iseconds)] manifest written:", "$MANIFEST_PATH")
PY

cat > "$RESULT_PATH" <<EOF
# RESULT — data-acquisition: $ATLAS_LABEL

METRIC=$N_CELLS
KEY_FINDING=Pulled $ATLAS_LABEL from $SOURCE_URL — ${N_CELLS} cells, sha256 $CHECKSUM, stored at $LOCAL_PATH_REL for downstream branches.
COST=$(awk "BEGIN{print $BYTES/1073741824}") GiB transferred
ARTIFACTS=DATA_MANIFEST.json, $LOCAL_PATH_REL, download.log
DONE_READY=false

## Charter compliance

| Rule | Verdict | Evidence |
|---|---|---|
| 0. Anti-laziness preamble | PASS | full file downloaded, no subsetting; n_cells recorded |
| 1. Data rules | PASS | source_url, sha256 checksum, n_cells recorded in DATA_MANIFEST.json |
| 7. Reproducibility rules | PASS | source_url + checksum + this download script committed; any re-pull verifiable |
EOF

echo "[$(date -Iseconds)] DONE — RESULT.md + DATA_MANIFEST.json written"
echo "  $RESULT_PATH"
echo "  $MANIFEST_PATH"
