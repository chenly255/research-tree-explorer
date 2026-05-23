#!/usr/bin/env bash
# v0.1.7 — data-acquisition template tests.
# Covers: cellxgene_discover.py CLI surface, geo_figshare_download.sh end-to-end
# against a local fake h5ad served via file://, and that the resulting
# DATA_MANIFEST.json + RESULT.md pass charter_validator with
# --task-type data-acquisition.
# Exits non-zero on any failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V="$REPO_ROOT/scripts/charter_validator.py"
EX="$REPO_ROOT/examples/data-acquisition"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
cd "$TMP"

echo "=== test 1: discover CLI surface ==="
python3 "$EX/cellxgene_discover.py" --help > /dev/null
python3 "$EX/cellxgene_discover.py" search --help > /dev/null
python3 "$EX/cellxgene_discover.py" list-collection --help > /dev/null
python3 "$EX/cellxgene_discover.py" inspect-dataset --help > /dev/null
echo "  CLI surface OK"

echo "=== test 2: build a tiny fake .h5ad for end-to-end (h5py only — sidesteps anndata numpy ABI) ==="
python3 - <<'PY'
import h5py, numpy as np
n_cells, n_genes = 123, 50
with h5py.File("fake_atlas.h5ad", "w") as f:
    obs = f.create_group("obs")
    obs.create_dataset("_index", data=np.array([f"c{i}" for i in range(n_cells)], dtype="S8"))
    var = f.create_group("var")
    var.create_dataset("_index", data=np.array([f"g{i}" for i in range(n_genes)], dtype="S8"))
    x = (np.random.default_rng(0).poisson(3, size=(n_cells, n_genes))).astype("float32")
    f.create_dataset("X", data=x)
print("wrote fake_atlas.h5ad", (n_cells, n_genes))
PY
[[ -f fake_atlas.h5ad ]] || { echo "FAIL: fake h5ad not written" >&2; exit 1; }

echo "=== test 3: geo_figshare_download.sh against local HTTP server, no proxy ==="
mkdir -p branch_geo
# wget does not support file://; spin up a one-shot HTTP server for the test.
# Use a wrapper that picks a port and writes it to a file before serving,
# so we don't need any system tool to read the bound port.
cat > "$TMP/serve.py" <<'PYWRAP'
import http.server, os, socketserver, sys
os.chdir(sys.argv[1])
port_file = sys.argv[2]
with socketserver.TCPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler) as h:
    open(port_file, "w").write(str(h.server_address[1]))
    h.serve_forever()
PYWRAP
python3 "$TMP/serve.py" "$TMP" "$TMP/port" > "$TMP/http.log" 2>&1 &
HTTP_PID=$!
trap "kill $HTTP_PID 2>/dev/null; rm -rf $TMP" EXIT
for _ in $(seq 1 30); do
  [[ -s "$TMP/port" ]] && break
  sleep 0.1
done
PORT=$(cat "$TMP/port" 2>/dev/null)
[[ -n "$PORT" ]] || { echo "FAIL: http server did not bind" >&2; cat "$TMP/http.log" >&2; exit 1; }
echo "  http server pid=$HTTP_PID listening on port $PORT"
FAKE_URL="http://127.0.0.1:$PORT/fake_atlas.h5ad"
PROXY="" SOURCE_URL="$FAKE_URL" \
  ATLAS_ID="fake2026" ATLAS_LABEL="Fake 2026 atlas" \
  PAPER_DOI="10.0/fake" \
  FILENAME="fake2026.h5ad" \
  OUT_DIR="$TMP/branch_geo" \
  bash "$EX/geo_figshare_download.sh" 2>&1 | tail -12
[[ -f branch_geo/DATA_MANIFEST.json ]] || { echo "FAIL: DATA_MANIFEST.json missing" >&2; exit 1; }
[[ -f branch_geo/RESULT.md ]]        || { echo "FAIL: RESULT.md missing" >&2; exit 1; }
[[ -f branch_geo/fake2026.h5ad ]]     || { echo "FAIL: downloaded file missing" >&2; exit 1; }
N_CELLS=$(python3 -c "import json;print(json.load(open('branch_geo/DATA_MANIFEST.json'))['n_cells'])")
[[ "$N_CELLS" == "123" ]] || { echo "FAIL: expected n_cells=123, got $N_CELLS" >&2; exit 1; }
CHECKSUM=$(python3 -c "import json;print(json.load(open('branch_geo/DATA_MANIFEST.json'))['checksum'])")
[[ -n "$CHECKSUM" && ${#CHECKSUM} -eq 64 ]] || { echo "FAIL: bad checksum '$CHECKSUM'" >&2; exit 1; }
echo "  download + manifest + n_cells + checksum OK"

echo "=== test 4: DATA_MANIFEST.json has all validator-required keys ==="
python3 - <<'PY'
import json
m = json.load(open("branch_geo/DATA_MANIFEST.json"))
required = {"atlas_id", "source_url", "local_path", "checksum", "n_cells", "downloaded_at"}
missing = required - set(m.keys())
assert not missing, f"missing required keys: {missing}"
assert m["n_cells"] == 123
print("  all required keys present")
PY

echo "=== test 5: requirements.txt for charter rule 7 reproducibility ==="
# The download templates do not auto-emit requirements.txt; the test
# branch must add one for charter_validator to PASS rule 7. The script
# itself counts as the "code" so a minimal requirements.txt is fine.
cat > branch_geo/requirements.txt <<'EOF'
anndata>=0.10
wget
EOF
# Also need to drop EXTRACTED_LOCAL_PATH check — file is at branch root.

echo "=== test 6: charter_validator passes with --task-type data-acquisition ==="
set +e
python3 "$V" "$TMP/branch_geo" --task-type data-acquisition > val.json 2> val.err
EXIT=$?
set -e
VERDICT=$(python3 -c "import json;print(json.load(open('val.json'))['verdict'])")
if [[ "$EXIT" -ne 0 || "$VERDICT" != "PASS" ]]; then
  echo "FAIL: validator did not PASS. exit=$EXIT verdict=$VERDICT" >&2
  cat val.json >&2
  cat val.err >&2
  exit 1
fi
echo "  validator PASS OK"

echo "=== test 7: missing required key in manifest → validator FAILS ==="
# Corrupt the manifest by removing 'checksum' and re-run.
python3 - <<'PY'
import json
m = json.load(open("branch_geo/DATA_MANIFEST.json"))
m.pop("checksum")
json.dump(m, open("branch_geo/DATA_MANIFEST.json", "w"))
PY
set +e
python3 "$V" "$TMP/branch_geo" --task-type data-acquisition > val2.json 2> val2.err
EXIT=$?
set -e
VERDICT=$(python3 -c "import json;print(json.load(open('val2.json'))['verdict'])")
[[ "$EXIT" -eq 2 && "$VERDICT" == "FAIL" ]] || {
  echo "FAIL: expected validator to FAIL on missing checksum, got exit=$EXIT verdict=$VERDICT" >&2
  cat val2.json >&2
  exit 1
}
grep -q "missing required key 'checksum'" val2.json || {
  echo "FAIL: expected failure message about missing checksum" >&2
  cat val2.json >&2
  exit 1
}
echo "  validator correctly rejects missing checksum"

echo "=== test 8: PROXY=:17890 warning logged ==="
# Verify the warning is emitted on stderr before any wget happens.
# Use a fake URL that resolves to an unreachable proxy; wget will fail
# but the WARN must appear first.
mkdir -p "$TMP/junk"
set +e
PROXY="http://127.0.0.1:17890" SOURCE_URL="http://127.0.0.1:1/nope.h5ad" \
  ATLAS_ID="x" ATLAS_LABEL="x" PAPER_DOI="x" \
  FILENAME="nope.h5ad" OUT_DIR="$TMP/junk" \
  timeout 5 bash "$EX/geo_figshare_download.sh" 2> proxy_err.log > /dev/null
set -e
grep -q "WARN: PROXY=.*17890" proxy_err.log || {
  echo "FAIL: expected 17890 warning, got:" >&2
  cat proxy_err.log >&2
  exit 1
}
echo "  17890 warning OK"

echo ""
echo "=== ALL data-acquisition TESTS PASSED ==="
