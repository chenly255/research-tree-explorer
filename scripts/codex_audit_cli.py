#!/usr/bin/env python3
"""CLI fallback for the research-tree external codex audit.

Drop-in replacement for the `mcp__codex__codex` MCP call in SKILL.md step 6c
when the MCP server is not registered (e.g. only the `codex` CLI binary +
GPT-5.5 API key are available, as in the sc-bias project setup).

Produces `CODEX_AUDIT.json` with the v0.4.0 contract: nonce echo + per-file
SHA256 + **challenge-fragment responses**. The challenge-fragment scheme is
v0.4.0's answer to the v0.3.1 known limitation:

  Old (v0.3.1): orchestrator tells the model each file's SHA256 inside the
  prompt; model echoes the SHA back. Validator compared echoed SHA against
  disk SHA. Flaw: the SHA is *in the prompt*, so the model can echo it
  without actually parsing the inlined bytes.

  New (v0.4.0): orchestrator writes `AUDIT_CHALLENGES.json` with N random
  (file, byte_offset, length) tuples *before* calling the model. The prompt
  lists those challenges and the model must quote the exact verbatim text
  at each offset. Validator re-reads disk + cross-checks the quotes
  byte-for-byte. If the model didn't read the inlined content, it cannot
  reconstruct the verbatim fragment at a random offset — there is no way
  to fabricate it from prompt structure alone.

  SHA echo is kept as a secondary defense (defends against post-audit file
  edits) but is no longer the primary anti-fabrication mechanism.

This is honest external audit: GPT-5.5 has never seen the nonce, challenges,
or files until we open them in the prompt; the response is cryptographically
pinned to the file contents at audit time (we re-read and cross-check).

Usage (called by autopilot step 6c instead of mcp__codex__codex):
    python3 codex_audit_cli.py \\
        --branch-dir .research-tree/branches/1.1 \\
        --charter RESEARCH_CHARTER.md \\
        --nonce-file .research-tree/branches/1.1/AUDIT_NONCE \\
        --task-type data-acquisition \\
        --out .research-tree/branches/1.1/CODEX_AUDIT.json

Side effects: writes AUDIT_CHALLENGES.json next to --out (consumed by
charter_validator.py to cross-check the model's quoted fragments).

Exit codes:
    0 — wrote CODEX_AUDIT.json with verdict (PASS or FAIL) + nonce +
        files_read + challenge_responses
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
import random
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

# v0.3.1 — refuse to silently truncate inlined files. If a required file
# exceeds this, we fail loudly rather than continuing on a partial view that
# the model can SHA-echo without seeing the missing middle.
MAX_INLINE_BYTES = 12000

# v0.4.0 — challenge-fragment parameters.
# Each required file gets N_CHALLENGES_PER_FILE random byte-offset windows;
# each window is CHALLENGE_FRAGMENT_BYTES bytes long. Picking 3 windows per
# file gives the model multiple ways to fail and the validator multiple
# independent proofs the model actually parsed the file content. 64 bytes
# is short enough that the model can quote it but long enough that the
# space of guesses is astronomical for arbitrary file content.
N_CHALLENGES_PER_FILE = 3
CHALLENGE_FRAGMENT_BYTES = 64


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, content: str) -> None:
    """v0.4.0 codex-final P2-新1 — write `content` to `path` atomically.

    Writes to <path>.tmp.<pid>.<random>, fsync, then os.replace. If the
    process dies mid-write, the target remains either fully old or fully
    new — never a truncated/half-written JSON that breaks downstream
    parsers (validator chokes on CODEX_AUDIT.json mid-write).
    """
    import os as _os
    tmp = path.with_suffix(path.suffix + f".tmp.{_os.getpid()}.{_os.urandom(4).hex()}")
    try:
        with tmp.open("w") as f:
            f.write(content)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(str(tmp), str(path))
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


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


def build_challenges(
    files_with_content: dict[str, str],
    rng: random.Random,
) -> dict[str, dict]:
    """v0.4.0 — generate N random (offset, length) challenges per file.

    Returns dict keyed by challenge_id (e.g. "ch_RESULT.md_0") with
    {file, offset, length, expected_text}. expected_text is what
    charter_validator.py will compare against the model's response. The
    AUDIT_CHALLENGES.json file persists this for the validator to read.

    Challenge IDs are deterministic (file slug + index) so prompt rendering
    is stable; offset and expected_text are random per call so the model
    can't pre-bake answers.
    """
    challenges: dict[str, dict] = {}
    for rel, content in files_with_content.items():
        size = len(content)
        if size < CHALLENGE_FRAGMENT_BYTES:
            # File shorter than fragment window — challenge is the whole file.
            challenges[f"ch_{rel}_0"] = {
                "file": rel,
                "offset": 0,
                "length": size,
                "expected_text": content,
            }
            continue
        for i in range(N_CHALLENGES_PER_FILE):
            offset = rng.randint(0, size - CHALLENGE_FRAGMENT_BYTES)
            fragment = content[offset:offset + CHALLENGE_FRAGMENT_BYTES]
            challenges[f"ch_{rel}_{i}"] = {
                "file": rel,
                "offset": offset,
                "length": CHALLENGE_FRAGMENT_BYTES,
                "expected_text": fragment,
            }
    return challenges


def build_prompt(
    branch_dir: Path,
    charter_path: Path,
    nonce: str,
    task_type: str,
    files_with_content: dict[str, str],
    files_with_sha: dict[str, str],
    challenges: dict[str, dict],
) -> str:
    file_block = []
    for rel, content in files_with_content.items():
        # v0.3.1: never truncate. caller already filtered oversize files.
        file_block.append(
            f"---FILE START: {rel} (sha256={files_with_sha[rel]})---\n"
            f"{content}\n"
            f"---FILE END: {rel}---"
        )
    files_section = "\n\n".join(file_block)

    challenge_lines = []
    for cid, ch in challenges.items():
        challenge_lines.append(
            f"  {cid}: quote bytes [{ch['offset']}..{ch['offset'] + ch['length']}) "
            f"of {ch['file']} verbatim — exactly {ch['length']} characters, "
            f"preserving every space and newline."
        )
    challenges_section = "\n".join(challenge_lines)

    challenges_template = "\n".join(
        f'    "{cid}": "<the exact {challenges[cid]["length"]} characters at offset {challenges[cid]["offset"]} of {challenges[cid]["file"]}>",'
        for cid in challenges
    )
    if challenges_template:
        challenges_template = challenges_template.rstrip(",")

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

CHALLENGE FRAGMENTS (this is how the validator proves you actually read
the files, not just echoed prompt structure). For each challenge, return
the exact characters at the given byte range — copy them verbatim from
the inlined file content. Whitespace and newlines count. The validator
will re-read disk and cross-check byte-for-byte:

{challenges_section}

Files inlined below — answer the challenges above by quoting these bytes:

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
  }},
  "challenge_responses": {{
{challenges_template}
  }}
}}

Both `files_read` and `challenge_responses` are validated against disk by
the orchestrator. Adding or removing entries vs the required set will be
treated as audit failure. Quoting a challenge fragment incorrectly (even
by a single character) will be treated as failure to read the file.
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
    ap.add_argument("--challenges-out", default=None, type=Path,
                    help="path to write AUDIT_CHALLENGES.json (default: alongside --out)")
    ap.add_argument("--model", default=os.environ.get("CODEX_AUDIT_MODEL", "gpt-5.5"))
    ap.add_argument("--base-url",
                    default=os.environ.get("CODEX_AUDIT_BASE_URL",
                                           "https://api.biom.autos/v1"))
    ap.add_argument("--max-tokens", type=int, default=6000)
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
        size = p.stat().st_size
        if size > MAX_INLINE_BYTES:
            # v0.3.1 (codex review P1-3): refuse oversized inline rather than
            # silently truncate. With truncation the model could SHA-echo a
            # complete file it only half-saw. v0.4.0 challenge-fragment makes
            # this even more important — a random offset in a truncated file
            # would either be missing from the prompt or come from a section
            # the model never saw.
            print(
                f"ERROR: {rel} is {size} bytes > MAX_INLINE_BYTES={MAX_INLINE_BYTES}. "
                f"Refusing to truncate-and-audit (would invalidate both SHA-echo "
                f"and challenge-fragment proofs). Either summarize the file in a "
                f"smaller artifact, or implement chunked audit.",
                file=sys.stderr,
            )
            return 1
        try:
            files_with_content[rel] = p.read_text(errors="replace")
        except Exception as e:
            print(f"ERROR: cannot read {rel}: {e}", file=sys.stderr)
            return 1
        files_with_sha[rel] = sha256_file(p)

    # v0.4.0 — seed RNG from nonce so challenges are deterministic if the
    # caller reruns with the same nonce (useful for debugging). Different
    # nonce → different challenges. Same nonce → same challenges, which
    # matters because the validator reads AUDIT_CHALLENGES.json by file
    # path; two concurrent re-runs with the same nonce shouldn't disagree
    # on what was asked.
    #
    # v0.4.0 codex-final review P1-1: do NOT write expected_text to disk before
    # the codex call. Earlier draft put expected_text in AUDIT_CHALLENGES.json
    # pre-call — but the agent can read its own branch_dir, copy the expected
    # answers into a fake CODEX_AUDIT.json, never call codex, and pass the
    # validator. That defeats the whole anti-fabrication purpose.
    #
    # The new flow:
    #   1. Build challenges in memory (expected_text NOT on disk).
    #   2. Call codex with prompt containing challenges (no answers).
    #   3. Verify codex answers against in-memory expected_text BEFORE writing.
    #   4. Only AFTER verify, write a sanitized AUDIT_CHALLENGES.json with
    #      {file, offset, length} only — no expected_text. The validator
    #      will re-derive expected_text from disk (which is the actual file
    #      content, the same content the model saw inline) and compare
    #      against CODEX_AUDIT.json.challenge_responses.
    rng = random.Random(nonce)
    challenges = build_challenges(files_with_content, rng)

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
        challenges=challenges,
    )

    try:
        resp = client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "system", "content":
                    "You are an adversarial research auditor. Return only JSON. "
                    "Challenge fragments must be quoted verbatim — copy bytes from "
                    "the inlined file content, do not rephrase or summarize."},
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

    # v0.4.0 codex-final P1-1: verify challenge-fragment responses BEFORE
    # writing anything to disk. expected_text stays in memory only — never
    # persisted. If the model's answers match, sanitize the challenges
    # (strip expected_text) and write only the metadata for the validator
    # to re-derive from disk and double-check.
    responses = audit.get("challenge_responses")
    if not isinstance(responses, dict) or not responses:
        print(
            "ERROR: codex response missing `challenge_responses` dict. "
            "v0.4.0 anti-fabrication requires the model to quote verbatim "
            "fragments at random offsets of each required file.",
            file=sys.stderr,
        )
        return 1
    for cid, ch in challenges.items():
        expected = ch["expected_text"]
        got = responses.get(cid)
        if not isinstance(got, str):
            print(
                f"ERROR: challenge_responses[{cid!r}] missing or not a string. "
                f"Model failed to quote the fragment.",
                file=sys.stderr,
            )
            return 1
        if got != expected:
            print(
                f"ERROR: challenge {cid} ({ch['file']} bytes "
                f"[{ch['offset']}..{ch['offset'] + ch['length']})) — model "
                f"quoted {got[:80]!r}, expected {expected[:80]!r}. The model "
                f"fabricated the audit without reading the file content.",
                file=sys.stderr,
            )
            return 1

    # Sanitize challenges: strip expected_text. Validator will re-read disk
    # at recorded offsets and recompute expected_text fresh, comparing
    # against the model's responses in CODEX_AUDIT.json. This gives the
    # validator two independent sources of truth (disk + model) without
    # ever exposing expected_text to the agent.
    sanitized_challenges = {
        cid: {"file": ch["file"], "offset": ch["offset"], "length": ch["length"]}
        for cid, ch in challenges.items()
    }
    challenges_out = args.challenges_out or (args.out.parent / "AUDIT_CHALLENGES.json")
    # v0.4.0 codex-final P2-新1: atomic write — disk-full or kill -9 must not
    # leave a half-written JSON for the validator to choke on.
    atomic_write(challenges_out, json.dumps(sanitized_challenges, indent=2, ensure_ascii=False))
    atomic_write(args.out, json.dumps(audit, indent=2, ensure_ascii=False))
    verdict = audit.get("verdict", "MISSING")
    print(
        f"OK: wrote {args.out}  verdict={verdict}  task_type={args.task_type}  "
        f"challenges={len(challenges)} (all verified, expected_text not persisted)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
