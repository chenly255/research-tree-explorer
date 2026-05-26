"""DataAcquisitionWorker — download + verify external data.

No model, no metric beyond cell count. Pure infrastructure. Templates in
examples/data-acquisition/ produce the exact schema this Worker expects.
"""
from __future__ import annotations

from .base import BaseWorker


class DataAcquisitionWorker(BaseWorker):
    task_type = "data-acquisition"

    def spawn_subagent_prompt(self, node, graph, ctx) -> str:
        return (
            self._common_prompt_header(node, graph, ctx)
            + "\n"
            + self._anti_laziness_block()
            + self._background_execution_block(node)
            + self._output_modes_block()
            + "PHYSICAL ARTIFACTS REQUIRED:\n\n"
            "    DATA_MANIFEST.json\n"
            "        Required keys: `atlas_id`, `source_url`, `local_path`, `checksum`,\n"
            "        `n_cells`, `downloaded_at`. The referenced `local_path` MUST exist\n"
            "        on disk after the download finishes — validator stat()s it.\n\n"
            "    requirements.txt OR the download script (.sh / .py) you used\n"
            "        Reproducibility — someone needs to be able to redo the pull.\n\n"
            "    NO model artifacts, NO test_split, NO ablations/.\n\n"
            "RESULT.md format:\n"
            "    METRIC=<n_cells>  # cells downloaded — the primary scalar\n"
            "    KEY_FINDING=<one line: which atlas, how many cells, where it lives>\n"
            "    ARTIFACTS=<list>\n"
            "    + the charter compliance table covering rules 0/1/7 only.\n\n"
            "USE THE TEMPLATES in $RTE_REPO/examples/data-acquisition/ — they emit the\n"
            "exact schema the validator expects:\n"
            "    cellxgene_discover.py — find dataset UUID when you have paper / disease / tissue\n"
            "    cellxgene_download.sh — for CELLxGENE datasets; edit env vars, run with nohup\n"
            "    geo_figshare_download.sh — for non-CELLxGENE sources (GEO ftp, figshare, Zenodo)\n\n"
            "PROXY POLICY (sc-bias hard rule, mirrored to other projects when applicable):\n"
            "    Downloads go through http://127.0.0.1:17891. NEVER 17890 (that is Claude Code's\n"
            "    metered upstream — one accidental 15 GB pull burned a quota). Templates\n"
            "    default to 17891 and log a loud WARN if 17890 is detected.\n\n"
            "PROTECTED ACCESS:\n"
            "    If the dataset is EGA / dbGaP / IRB-restricted / cloud-storage-with-credentials,\n"
            "    DO NOT brute-force download. Write DEAD.md with death_reason='needs_human:\n"
            "    protected-access data (<source>)' and a STUCK trigger surfaces to Lily.\n\n"
            "NO SILENT FORMAT CONVERSION:\n"
            "    If source provides .rds and project expects .h5ad, conversion is a SEPARATE\n"
            "    branch (task_type=analysis). Data acquisition's only job is pulling bytes and\n"
            "    verifying they match upstream metadata.\n"
        )
