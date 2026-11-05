#!/usr/bin/env python3
"""Completeness QC — does the render capture every salient feature of the reference?

The geometry probe (probe_glb.py) proves a model is VALID (connected, sane joints, faces the
front) but is blind to MISSING features: a door that lacks its coat hooks, a cabinet missing a
handle, the wrong number of drawers. This closes that gap with a vision judge: it compares the
reference image against the render previews and lists what's present in the reference but absent
or wrong in the render.

Runs on the logged-in Claude via claude_agent_sdk (Read-only) — no API key, no Gemini. Kept
separate from probe_glb.py so that stays a pure, dependency-free geometric check.

Usage:
    completeness_check.py --ref <reference.png> --render <r.png> [--render <r2.png> ...] [--json]

Exit 0 = complete (nothing salient missing), 1 = missing features, 2 = bad usage / VLM error.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path

PROMPT = (
    "IMAGE 1 is the REFERENCE of a single object. The remaining images are RENDERS of a 3D model "
    "built to reproduce it (front / closed / open views). Judge only whether the render captures "
    "every SALIENT feature of the reference: the distinct parts (hooks, handles, latches, knobs, "
    "vents, shelves, panels, glazing, trim), their COUNT, and rough placement. IGNORE lighting, "
    "exact colour, background, resolution and micro-detail. "
    'Reply with ONLY a JSON object: {"pass": bool, "missing": ["<feature in the reference that is '
    'absent or clearly wrong in the render>", ...], "notes": "<one short line>"}. '
    "Set pass=true only when nothing salient is missing."
)


_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _blocks(images: list[Path]) -> list[dict]:
    """The images as Messages-API content blocks, each labelled so the prompt can refer to it."""
    out: list[dict] = []
    for i, p in enumerate(images):
        out.append({"type": "text", "text": f"IMAGE {i + 1} ({'REFERENCE' if i == 0 else p.name}):"})
        out.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _MIME.get(p.suffix.lower(), "image/png"),
                "data": base64.b64encode(p.read_bytes()).decode(),
            },
        })
    out.append({"type": "text", "text": PROMPT})
    return out


async def _ask(images):
    from litereality_agent.agent.room_codex import ask
    return await ask(PROMPT,cwd=images[0].resolve().parent,images=images,timeout=150)


def _parse(output):
    from litereality_agent.agent.room_codex import parse_json
    return parse_json(output,{'type':'object','required':['pass','missing','notes'],
        'properties':{'pass':{'type':'boolean'},'missing':{'type':'array','items':{'type':'string'}},'notes':{'type':'string'}}})


def check(ref: Path, renders: list[Path]) -> dict:
    imgs = [ref] + [r for r in renders if r.is_file()]
    if len(imgs) < 2:
        return {"pass": False, "missing": [], "notes": "no renders to compare", "error": "no_renders"}
    try:
        data = _parse(asyncio.run(_ask(imgs)))
    except Exception as e:  # noqa: BLE001 — never hard-fail delivery on a VLM hiccup
        return {"pass": False, "missing": [], "notes": f"vlm error: {type(e).__name__}", "error": str(e)}
    data.setdefault("pass", False)
    data.setdefault("missing", [])
    data.setdefault("notes", "")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--render", action="append", default=[], help="repeatable")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = check(Path(args.ref), [Path(r) for r in args.render])
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        verdict = "COMPLETE" if report["pass"] else "INCOMPLETE"
        print(f"completeness: {verdict}")
        for m in report.get("missing", []):
            print(f"  ✗ missing: {m}")
        if report.get("notes"):
            print(f"  note: {report['notes']}")
    return 2 if report.get("error") else (0 if report.get("pass", False) else 1)


if __name__ == "__main__":
    sys.exit(main())
