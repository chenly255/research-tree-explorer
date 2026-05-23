# Data-acquisition templates

Scripts here are copy-and-edit templates for `task_type=data-acquisition`
branches. They produce the exact `DATA_MANIFEST.json` schema that
`scripts/charter_validator.py --task-type data-acquisition` requires, so a
branch that uses one and passes the post-download contract will validate.

| Script | When to use |
|---|---|
| `cellxgene_discover.py` | Find a dataset UUID when you know the paper / disease / tissue but not the UUID. Returns JSON with collection ids, dataset ids, download URLs, cell counts, assays, tissues, diseases. |
| `cellxgene_download.sh` | Pull a known CELLxGENE Discover dataset by UUID. Auto-computes `n_cells` from the resulting `.h5ad`. |
| `geo_figshare_download.sh` | Pull from GEO ftp, figshare ndownloader, Zenodo records, GitHub releases, or any direct URL. Auto-counts cells if it's `.h5ad`; otherwise the caller must pass `N_CELLS` (from the paper or GEO description). |

## Recipe — autopilot subagent flow

1. **Discover** (only if dataset UUID not known):
   ```bash
   python3 .../examples/data-acquisition/cellxgene_discover.py search \
       --query "Adams IPF lung"
   # → pick collection, then list-collection, then pick dataset_id
   ```

2. **Download** in background (long-running, must survive session restart):
   ```bash
   cd .research-tree/branches/<node_id>/
   cp .../examples/data-acquisition/cellxgene_download.sh .
   # edit DATASET_ID / ATLAS_ID / ATLAS_LABEL / PAPER_DOI

   nohup bash cellxgene_download.sh > executor.log 2>&1 &
   BGPID=$!
   ```

3. **Write `EXECUTOR.json`** immediately so `stale_running_handler.py` can
   detect completion later:
   ```json
   {
     "pid": <BGPID>,
     "started_at": "<iso8601>",
     "command": "bash cellxgene_download.sh",
     "log_file": "executor.log",
     "expected_outputs": ["RESULT.md", "DATA_MANIFEST.json", "<atlas_id>.h5ad"],
     "timeout_hours": 12
   }
   ```

4. **Return to orchestrator.** The download keeps running detached.
   `stale_running_handler.py` will route the completed branch through the
   validation chain on the next autopilot cycle.

## Proxy policy (sc-bias project)

- `127.0.0.1:17891` — used for **all** downloads (figshare / GEO / CELLxGENE
  / model weights / pip). Plenty of bandwidth.
- `127.0.0.1:17890` — Claude Code's own metered upstream. **Never** use it
  for downloads — the scripts log a loud warning if `PROXY=*17890*` is
  passed.
- Direct connect — use for `localhost`, intranet mirrors, 国内 services.
  The scripts accept `PROXY=""` and clear all inherited proxy env vars in
  that case.

If your project does not use `17891`, override `PROXY` per-invocation:
```bash
PROXY="" bash cellxgene_download.sh                    # direct
PROXY="http://your-host:8080" bash cellxgene_download.sh
```

## Validator contract (what the post-download check enforces)

`charter_validator.py --task-type data-acquisition` reads
`DATA_MANIFEST.json` and demands these keys exist:

| Key | Type | Notes |
|---|---|---|
| `atlas_id` | string | short slug, used as canonical local key |
| `source_url` | string | exact URL the file came from |
| `local_path` | string | relative to branch_dir; validator confirms the file actually exists on disk |
| `checksum` | string | sha256 hex |
| `n_cells` | int | the file's cell count |
| `downloaded_at` | ISO8601 string | UTC offset OK |

Lying about `n_cells` is risky: any downstream audit / training branch
loads the file and a mismatch will be caught immediately.

## Beyond these templates

If you need protected-access data (EGA / dbGaP / IRB / private cloud
buckets), the data-acquisition subagent should NOT attempt to download
itself. Instead, write `DEAD.md` with `death_reason="needs_human:
protected-access data (<source>), requires <DAC application | account
provisioning | $/credits>"`. That surfaces the blocker to the human in
the next autopilot stuck-check and is the contract for hand-off.
