#!/usr/bin/env python3
"""CLI fallback for the research-tree external codex audit.

Drop-in replacement for the `mcp__codex__codex` MCP call in SKILL.md step 6c
when the MCP server is not registered (e.g. only the `codex` CLI binary +
GPT-5.5 API key are available, as in the sc-bias project setup).

Produces `CODEX_AUDIT.json` with the exact schema `charter_validator.py`
expects (nonce echo + per-file SHA256). The audit prompt is delivered to
GPT-5.5 via OpenAI-compatible chat.completions, and the response is parsed
into the validator's contract. If the LLM refuses to echo the nonce, returns
malformed JSON, or claims SHA256s that don't match disk, the orchestrator
treats the branch the same as if the MCP call had timed out — fail-CLOSED.

This is honest external audit, not pass-through: GPT-5.5 has never seen the
nonce or the files until we open them in the prompt, and its response is
cryptographically pinned to the file contents it inspects (we recompute and
cross-check).

Usage (called by autopilot step 6c instead of mcp__codex__codex):
    python3 codex_audit_cli.py \\
        --branch-dir .research-tree/branches/1.1 \\
        --charter RESEARCH_CHARTER.md \\
        --nonce-file .research-tree/branches/1.1/AUDIT_NONCE \\
        --task-type data-acquisition \\
        --out .research-tree/branches/1.1/CODEX_AUDIT.json

Exit codes:
    0 — wrote CODEX_AUDIT.json with verdict (PASS or FAIL) + nonce + files_read
    1 — local error (missing files, can't reach API, malformed LLM response).
        Caller MUST treat this the same as MCP unavailable → die branch.

Environment:
    OPENAI_API_KEY — falls back to ~/.codex/auth.json:OPENAI_API_KEY
    CODEX_AUDIT_BASE_URL — default https://api.biom.autos/v1
    CODEX_AUDIT_MODEL — default gpt-5.5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai SDK not installed. `pip install openai`.", file=sys.stderr)
    sys.exit(1)


REQUIRED_FILES_BY_TASK_TYPE: dict[str, list[str]] = {
    "training": ["RESULT.md", "metrics.json", "data/test_split.json"],
    "mixed": ["RESULT.md", "metrics.json", "data/test_split.json"],
    "audit": ["RESULT.md", "audit_report.json"],
    "analysis": ["RESULT.md", "analysis_output.json"],
    "data-acquisition": ["RESULT.md", "DATA_MANIFEST.json"],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    auth_path = Path.home() / ".codex" / "auth.json"
    if auth_path.exists():
        try:
            return json.loads(auth_path.read_text())["OPENAI_API_KEY"]
        except (json.JSONDecodeError, KeyError) as e:
            print(f"ERROR: ~/.codex/auth.json malformed: {e}", file=sys.stderr)
            sys.exit(1)
    print(
        "ERROR: no OPENAI_API_KEY in env and ~/.codex/auth.json absent. "
        "Cannot reach codex API.",
        file=sys.stderr,
    )
    sys.exit(1)


def build_prompt(
    branch_dir: Path,
    charter_path: Path,
    nonce: str,
    task_type: str,
    files_with_content: dict[str, str],
    files_with_sha: dict[str, str],
) -> str:
    file_block = []
    for rel, content in files_with_content.items():
        truncated = content
        if len(content) > 12000:
            truncated = content[:6000] + "\n…[truncated middle]…\n" + content[-6000:]
        file_block.append(
            f"---FILE START: {rel} (sha256={files_with_sha[rel]})---\n"
            f"{truncated}\n"
            f"---FILE END: {rel}---"
        )
    files_section = "\n\n".join(file_block)

    return f"""You are an external adversarial auditor for a research branch in a
tree-shaped exploration. Your job is to read the artifacts inlined below
and decide whether the branch genuinely meets the project's research
charter, or whether it appears to take shortcuts.

AUDIT NONCE (you MUST echo this back verbatim in your JSON's `nonce`
field — the orchestrator generated this fresh on disk before calling you,
and cross-checks it to defend against pre-fabricated audits):

    {nonce}

Task type: {task_type}
Branch directory: {branch_dir}
Charter path: {charter_path}

Required-files set the validator will cross-check `files_read` against
(for this task_type): {REQUIRED_FILES_BY_TASK_TYPE.get(task_type, REQUIRED_FILES_BY_TASK_TYPE['training'])}

Files inlined below (SHA256 was computed by the orchestrator from disk —
you must echo the same hash for each file in your `files_read` dict, so
the validator can confirm you actually read the right bytes):

{files_section}

Look for:
- fabricated numbers (metrics suspiciously round, std=0, identical across seeds)
- missing claimed artifacts (e.g. RESULT.md says "ablations/scale_8m" but the
  file content shows nothing like it)
- baselines not actually compared to SOTA
- internal inconsistencies between RESULT.md claims and the supporting files
- charter table rules marked PASS that the file evidence contradicts
- for data-acquisition: DATA_MANIFEST.json local_path / checksum mismatches,
  source_url that looks fabricated, n_cells absurd for the cited atlas
- for audit: blindspot_signal CI that doesn't bracket the metric, donor_bootstrap
  n_iter < 1000, protocol_comparison missing within_atlas / cross_batch fields

Return ONLY a JSON object, no prose around it, no markdown fence:
{{
  "nonce": "{nonce}",
  "verdict": "PASS" or "FAIL",
  "reasoning_summary": "<one sentence>",
  "reasoning": "<detailed 3-5 sentence justification>",
  "specific_concerns": ["<concern 1>", "<concern 2>", ...],
  "files_read": {{
    {", ".join(f'"{f}": "{files_with_sha[f]}"' for f in files_with_content)}
  }}
}}

In `files_read` copy the SHA256s exactly as given above — that is the
validator's proof-of-read. Adding or removing files vs the required set
will be treated as audit failure.
"""


def extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--branch-dir", required=True, type=Path)
    ap.add_argument("--charter", required=True, type=Path)
    ap.add_argument("--nonce-file", required=True, type=Path)
    ap.add_argument("--task-type", default="training",
                    choices=sorted(REQUIRED_FILES_BY_TASK_TYPE.keys()))
    ap.add_argument("--out", required=True, type=Path,
                    help="path to write CODEX_AUDIT.json")
    ap.add_argument("--model", default=os.environ.get("CODEX_AUDIT_MODEL", "gpt-5.5"))
    ap.add_argument("--base-url",
                    default=os.environ.get("CODEX_AUDIT_BASE_URL",
                                           "https://api.biom.autos/v1"))
    ap.add_argument("--max-tokens", type=int, default=4000)
    args = ap.parse_args()

    branch_dir = args.branch_dir.resolve()
    if not branch_dir.is_dir():
        print(f"ERROR: branch_dir not a directory: {branch_dir}", file=sys.stderr)
        return 1
    if not args.charter.exists():
        print(f"ERROR: charter not found: {args.charter}", file=sys.stderr)
        return 1
    if not args.nonce_file.exists():
        print(f"ERROR: nonce file not found: {args.nonce_file}", file=sys.stderr)
        return 1

    nonce = args.nonce_file.read_text().strip()
    if not nonce:
        print(f"ERROR: nonce file empty: {args.nonce_file}", file=sys.stderr)
        return 1

    required = REQUIRED_FILES_BY_TASK_TYPE[args.task_type]
    files_with_content: dict[str, str] = {}
    files_with_sha: dict[str, str] = {}
    for rel in required:
        p = branch_dir / rel
        if not p.exists():
            print(f"ERROR: required file missing for task_type={args.task_type}: {rel}",
                  file=sys.stderr)
            return 1
        try:
            files_with_content[rel] = p.read_text(errors="replace")
        except Exception as e:
            print(f"ERROR: cannot read {rel}: {e}", file=sys.stderr)
            return 1
        files_with_sha[rel] = sha256_file(p)

    api_key = load_api_key()
    no_proxy_env = {
        k: v for k, v in os.environ.items()
        if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    }
    os.environ.clear()
    os.environ.update(no_proxy_env)

    client = OpenAI(api_key=api_key, base_url=args.base_url)
    prompt = build_prompt(
        branch_dir=branch_dir,
        charter_path=args.charter.resolve(),
        nonce=nonce,
        task_type=args.task_type,
        files_with_content=files_with_content,
        files_with_sha=files_with_sha,
    )

    try:
        resp = client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "system", "content":
                    "You are an adversarial research auditor. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=args.max_tokens,
        )
    except Exception as e:
        print(f"ERROR: codex API call failed: {e}", file=sys.stderr)
        return 1

    raw = resp.choices[0].message.content or ""
    audit = extract_json(raw)
    if not audit:
        print(f"ERROR: codex returned non-JSON output:\n{raw[:2000]}", file=sys.stderr)
        return 1

    got_nonce = str(audit.get("nonce", "")).strip()
    if got_nonce != nonce:
        print(
            f"ERROR: codex nonce mismatch (got {got_nonce!r}, expected {nonce!r}). "
            f"Audit rejected.",
            file=sys.stderr,
        )
        return 1

    args.out.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    verdict = audit.get("verdict", "MISSING")
    print(f"OK: wrote {args.out}  verdict={verdict}  task_type={args.task_type}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
