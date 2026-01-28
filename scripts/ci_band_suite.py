#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str, float]:
    t0 = time.time()
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    dt = time.time() - t0
    return p.returncode, p.stdout, p.stderr, dt


def detect_run_sost_cli(python_exe: str, runner: Path) -> Tuple[str, Optional[str]]:
    """
    Détecte le flag d'input et le flag de sortie sur scripts/run_sost.py
    Retour: (input_mode, out_flag)
      input_mode: "--input-csv" | "--input" | "positional"
      out_flag: "--outdir" | "--out" | None
    """
    rc, out, err, _ = run_cmd([python_exe, str(runner), "--help"])
    help_txt = (out or "") + "\n" + (err or "")

    if "--input-csv" in help_txt:
        input_mode = "--input-csv"
    elif "--input" in help_txt:
        input_mode = "--input"
    else:
        input_mode = "positional"

    if "--outdir" in help_txt:
        out_flag = "--outdir"
    elif "--out " in help_txt or "--out\n" in help_txt:
        out_flag = "--out"
    else:
        out_flag = None

    return input_mode, out_flag


@dataclass
class BandResult:
    band_file: str
    exit_code: int
    seconds: float
    out_dir: str
    produced_files: List[dict]
    stdout_log: str
    stderr_log: str


def main() -> int:
    ap = argparse.ArgumentParser(description="CI band suite runner for SOST")
    ap.add_argument("--pattern", default="test_data/band_*.csv", help="Glob pattern for band CSV inputs")
    ap.add_argument("--outdir", default="_ci_out", help="Output directory for CI artifacts")
    ap.add_argument("--runner", default="scripts/run_sost.py", help="Path to run_sost.py")
    ap.add_argument("--python", default=sys.executable, help="Python executable to use")
    ap.add_argument("--max", type=int, default=0, help="Limit number of bands (0 = no limit)")
    ap.add_argument("--fail-fast", action="store_true", help="Stop on first failure")
    args = ap.parse_args()

    repo_root = Path(".").resolve()
    outdir = (repo_root / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    runner = (repo_root / args.runner).resolve()
    if not runner.exists():
        (outdir / "band_suite_summary.json").write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Runner not found: {runner.as_posix()}",
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"ERROR: runner not found: {runner}", file=sys.stderr)
        return 2

    input_mode, out_flag = detect_run_sost_cli(args.python, runner)

    inputs = sorted(Path(".").glob(args.pattern))
    if args.max and args.max > 0:
        inputs = inputs[: args.max]

    if not inputs:
        (outdir / "band_suite_summary.json").write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": f"No inputs matched pattern: {args.pattern}",
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"ERROR: no inputs matched {args.pattern}", file=sys.stderr)
        return 2

    results: List[BandResult] = []
    failures = 0

    for csv_path in inputs:
        band_name = csv_path.stem
        band_out = outdir / "bands" / band_name
        band_out.mkdir(parents=True, exist_ok=True)

        stdout_log = band_out / "run_stdout.log"
        stderr_log = band_out / "run_stderr.log"

        cmd = [args.python, str(runner)]
        if input_mode == "--input-csv":
            cmd += ["--input-csv", str(csv_path)]
        elif input_mode == "--input":
            cmd += ["--input", str(csv_path)]
        else:
            cmd += [str(csv_path)]

        if out_flag is not None:
            cmd += [out_flag, str(band_out)]

        rc, out, err, dt = run_cmd(cmd, cwd=repo_root)

        stdout_log.write_text(out or "", encoding="utf-8", errors="replace")
        stderr_log.write_text(err or "", encoding="utf-8", errors="replace")

        produced = []
        for p in sorted(band_out.rglob("*")):
            if p.is_file():
                produced.append(
                    {
                        "path": p.relative_to(outdir).as_posix(),
                        "sha256": sha256_file(p),
                        "bytes": p.stat().st_size,
                    }
                )

        results.append(
            BandResult(
                band_file=str(csv_path),
                exit_code=rc,
                seconds=dt,
                out_dir=band_out.relative_to(outdir).as_posix(),
                produced_files=produced,
                stdout_log=stdout_log.relative_to(outdir).as_posix(),
                stderr_log=stderr_log.relative_to(outdir).as_posix(),
            )
        )

        if rc != 0:
            failures += 1
            if args.fail_fast:
                break

    summary = {
        "ok": failures == 0,
        "runner": str(runner),
        "input_mode": input_mode,
        "out_flag": out_flag,
        "pattern": args.pattern,
        "total": len(results),
        "failures": failures,
        "results": [
            {
                "band_file": r.band_file,
                "exit_code": r.exit_code,
                "seconds": r.seconds,
                "out_dir": r.out_dir,
                "stdout_log": r.stdout_log,
                "stderr_log": r.stderr_log,
                "produced_files": r.produced_files,
            }
            for r in results
        ],
    }

    (outdir / "band_suite_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"Bands OK: {summary['total'] - summary['failures']} / {summary['total']}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
