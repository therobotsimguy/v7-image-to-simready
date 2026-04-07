#!/usr/bin/env python3
"""V5 AI Agents — uses Claude and Gemini APIs for reasoning.

Multi-agent parallel calls for speed.
Reads BEHAVIOR_DEFINITIONS.md as context for the 16×15 matrix.
"""

import json
import os
import sys
import threading
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS_DIR = os.path.dirname(_DIR)
_TOOLS_DIR = os.path.dirname(_ASSETS_DIR)
_DOCS_DIR = os.path.join(_ASSETS_DIR, "docs")


def load_api_keys():
    with open(os.path.join(_TOOLS_DIR, "api_keys.json")) as f:
        return json.load(f)


def call_claude(api_key, prompt, model="claude-opus-4-6", max_tokens=16384):
    """Call Claude via CLI (uses subscription) with API key fallback."""
    import subprocess
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr[:200]}")
    return result.stdout


def call_gemini(api_key, prompt, model="gemini-2.5-pro"):
    """Call Gemini API."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=types.GenerateContentConfig(max_output_tokens=65536, temperature=0.1),
    )
    return resp.text


def parse_json(text):
    """Extract JSON from AI response."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    # Try array
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return json.loads(text)


def load_behavior_definitions():
    """Load BEHAVIOR_DEFINITIONS.md as context for AI agents."""
    path = os.path.join(_DOCS_DIR, "BEHAVIOR_DEFINITIONS.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def ai_validate_geometry(contract, blender_report: dict) -> dict:
    """Use Claude to reason about geometry correctness before USD export.

    Only calls Claude when the blender_report contains WARN or FAIL parts.

    Args:
        contract: BehaviorContract — has bbox, dims_mm, primary_behavior per part.
        blender_report: dict — geometry validation output, expected schema:
            {
              "parts": {
                "<part_name>": {
                  "status": "PASS" | "WARN" | "FAIL",
                  "actual_dims_mm": [x, y, z],       # optional
                  "vertex_center": [x, y, z],         # optional
                  "issues": ["...", ...]               # list of issue strings
                },
                ...
              }
            }

    Returns:
        dict of {part_name: action} where action is one of:
            "keep_as_is"  — geometry is correct, proceed to export
            "re_export"   — re-export this part from source
            "re_merge"    — merge/join sub-meshes and retry
            "mark_static" — treat as non-moving (fixed joint)
        Empty dict if all parts PASS (Claude not called).
    """
    parts_report = blender_report.get("parts", {})

    # Collect only WARN or FAIL parts — skip if all PASS
    flagged = {
        name: info
        for name, info in parts_report.items()
        if info.get("status") in ("WARN", "FAIL")
    }
    if not flagged:
        return {}

    # ── Build compact prompt ──────────────────────────────────────────────────
    root = contract.get_part(contract.root_part)
    root_dims = (
        f"{root.dims_mm[0]:.0f}×{root.dims_mm[1]:.0f}×{root.dims_mm[2]:.0f}mm"
        if root else "unknown"
    )

    lines = [
        f"Object: {contract.object_name} ({contract.object_type})",
        f"Root chassis dims: {root_dims}",
        "",
        "Flagged parts — assess geometry and recommend an action:",
        "",
    ]

    for part_name, info in flagged.items():
        status = info.get("status", "?")
        issues = info.get("issues", [])
        actual = info.get("actual_dims_mm")
        vcenter = info.get("vertex_center")

        # Expected dims from contract
        cp = contract.get_part(part_name)
        expected_dims = None
        behavior_type = None
        joint_type = None
        if cp:
            expected_dims = cp.dims_mm
            if cp.primary_behavior:
                behavior_type = cp.primary_behavior.behavior_type
                joint_type = cp.primary_behavior.joint_type

        lines.append(f"Part: {part_name}  [{status}]")
        if expected_dims:
            lines.append(
                f"  Expected dims: {expected_dims[0]:.0f}×{expected_dims[1]:.0f}×{expected_dims[2]:.0f}mm"
            )
        if actual:
            lines.append(
                f"  Actual dims:   {actual[0]:.0f}×{actual[1]:.0f}×{actual[2]:.0f}mm"
            )
        if vcenter:
            lines.append(f"  Vertex center: ({vcenter[0]:.3f}, {vcenter[1]:.3f}, {vcenter[2]:.3f})")
        if behavior_type:
            lines.append(f"  Primary behavior: {behavior_type} ({joint_type or 'no joint'})")
        if issues:
            for iss in issues[:4]:  # cap at 4 issues per part to save tokens
                lines.append(f"  Issue: {iss}")
        lines.append("")

    lines += [
        "For each flagged part respond with exactly one action:",
        "  keep_as_is  — geometry is correct despite the warning",
        "  re_export   — part needs to be re-exported from the source file",
        "  re_merge    — sub-meshes are split; join them and retry",
        "  mark_static — part cannot articulate; treat as fixed/static",
        "",
        "Respond ONLY with a JSON object, no commentary:",
        '{"<part_name>": "<action>", ...}',
    ]

    prompt = "\n".join(lines)

    # ── Call Claude ───────────────────────────────────────────────────────────
    keys = load_api_keys()
    api_key = (keys.get("anthropic") or {}).get("api_key") or keys.get("claude")
    if not api_key:
        raise RuntimeError("No Claude API key found in api_keys.json")

    raw = call_claude(
        api_key,
        prompt,
        model="claude-opus-4-6",
        max_tokens=512,  # actions only — very short
    )

    # ── Parse response ────────────────────────────────────────────────────────
    valid_actions = {"keep_as_is", "re_export", "re_merge", "mark_static"}
    try:
        result = parse_json(raw)
    except Exception as exc:
        print(f"  ai_validate_geometry: failed to parse Claude response — {exc}")
        print(f"  Raw: {raw[:200]}")
        return {}

    if not isinstance(result, dict):
        print(f"  ai_validate_geometry: unexpected response type {type(result)}")
        return {}

    # Sanitise: keep only known actions for parts that were actually flagged
    cleaned = {}
    for part_name, action in result.items():
        if part_name not in flagged:
            continue  # Claude hallucinated an extra part name — ignore
        if action not in valid_actions:
            print(f"  ai_validate_geometry: unknown action '{action}' for {part_name} — defaulting to keep_as_is")
            action = "keep_as_is"
        cleaned[part_name] = action

    # Fill in keep_as_is for any flagged part Claude forgot to mention
    for part_name in flagged:
        if part_name not in cleaned:
            print(f"  ai_validate_geometry: no action for {part_name} — defaulting to keep_as_is")
            cleaned[part_name] = "keep_as_is"

    return cleaned


def run_parallel_agents(tasks):
    """Run multiple AI calls in parallel.

    Args:
        tasks: list of (name, callable) tuples

    Returns:
        dict of {name: result}
    """
    results = {}
    errors = {}

    def _run(name, fn):
        try:
            results[name] = fn()
        except Exception as e:
            errors[name] = str(e)

    threads = []
    for name, fn in tasks:
        t = threading.Thread(target=_run, args=(name, fn))
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=120)

    if errors:
        for name, err in errors.items():
            print(f"  Agent {name} FAILED: {err[:100]}")

    return results, errors
