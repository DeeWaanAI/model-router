#!/usr/bin/env python3
"""Run model-generated Python and capture its output — for the experiment harness only.

NOT a sandbox, and it no longer pretends to be one. What it does:

* runs in a throwaway directory holding ONLY copies of the named corpus files;
* `sys.executable -I -E -S` — same interpreter as the harness, isolated from site dirs, user
  packages and PYTHON* variables (so a program that reaches for pandas fails honestly, and
  the prompt said standard library only);
* an environment containing nothing but PATH/HOME/LANG, so no API key, token or path from the
  parent can be read by code a model wrote;
* a hard wall-clock timeout.

What was removed, recorded rather than quietly deleted: an earlier version enforced a
hand-written stdlib import allowlist through a meta_path hook. Measured 2026-10-23 it broke
`import csv`, because csv imports re, re imports sre_compile, which imports `_sre` — absent
from the list. Every model program then failed for a reason internal to the harness, and the
run would have reported "models cannot write working code" as a finding about the *models*.
The list was also defeatable (`sys` had to be importable for the hook to exist), so it bought
false assurance at the cost of a corrupted measurement. Withholding the environment — where
the only real secrets on this machine actually are — replaces it.

Production execution of model-written code is Layer 3's permission problem, not this module's.
This exists so experiment 1 can measure "does the program run and print the right value"
instead of "can the model add up 800 numbers", which upstream measured at 0/12 and this layer
re-measured as every-arm-wrong.
"""
import json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

TIMEOUT_S = 30


def run_code(code, corpus_files, corpus_dir, timeout_s=TIMEOUT_S):
    """Returns {ran, stdout, stderr, parsed, exit_code, wall_s, error}."""
    tmp = Path(tempfile.mkdtemp(prefix="l5-exec-"))
    try:
        for fn in corpus_files:
            src = Path(corpus_dir) / fn
            if not src.exists():
                return {"ran": False, "error": f"missing corpus file {fn}", "stdout": "",
                        "stderr": "", "parsed": None, "exit_code": None, "wall_s": 0.0}
            shutil.copy(src, tmp / fn)
        script = tmp / "candidate.py"
        script.write_text(code or "")
        # Nothing inherited from the parent environment: no .env, no key, no token.
        env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(tmp),
               "LANG": "C.UTF-8"}
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, "-I", "-E", "-S", str(script)], cwd=str(tmp),
                               capture_output=True, text=True, timeout=timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return {"ran": False, "error": f"timeout after {timeout_s}s", "stdout": "",
                    "stderr": "", "parsed": None, "exit_code": None,
                    "wall_s": round(time.time() - t0, 3)}
        out = (p.stdout or "").strip()
        try:
            parsed = json.loads(out)
        except ValueError:
            parsed = None
        return {"ran": p.returncode == 0, "stdout": out[:2000],
                "stderr": (p.stderr or "")[:1500], "parsed": parsed,
                "exit_code": p.returncode, "wall_s": round(time.time() - t0, 3)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_code(text):
    """Models are told to emit a program and nothing else, so take the first fenced python
    block or, failing that, the whole reply. Deliberately simple: silently trimming code
    would make "did it run" unverifiable."""
    t = (text or "").strip()
    if "```" in t:
        parts = t.split("```")
        for i in range(1, len(parts)):
            block = parts[i]
            if block.strip().lower().startswith("python"):
                block = block[len("python"):]
            if block.strip():
                return block.strip()
    return t
