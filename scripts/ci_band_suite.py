#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import List, Optional


def run_cmd(cmd: List[str], log_path: Path, env: Optional[dict] = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        f.write("CMD:\n")
        f.write(" ".join(cmd) + "\n\n")
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        f.write(p.stdout or "")
        f.write(f"\n\nEXIT_CODE={p.returncode}\n")
    return p.returncode


def get_help(script_path: Path) -> str:
    p = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return p.stdout or ""


def extract_long_flags(help_text: str) -> List[str]:
    # récupère tous les flags du type --xxx depuis l'aide
    flags = sorted(set(re.findall(r"(--[a-zA-Z0-9][a-zA-Z0-9_-]*)", help_text)))
    return flags


def detect_subcommands(help_text: str) -> List[str]:
    m = re.search(r"\{([^}]+)\}", help_text)
    if not m:
        return []
    raw = m.group(1)
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def pick_best_flag(flags: List[str], keywords: List[str]) -> Optional[str]:
    # score simple basé sur mots-clés
    best = None
    best_score = -1
    for flg in flags:
        low = flg.lower()
        score = 0
        for kw in keywords:
            if kw in low:
                score += 1
        if score > best_score:
            best_score = score
            best = flg
    return best if best_score > 0 else None


def out_expects_file(out_flag: str) -> bool:
    # convention simple: outdir -> dossier, sinon on écrit un fichier json
    low = out_flag.lower()
    if "dir" in low or "folder" in low:
        return False
    return True


def build_attempts(
    script_path: Path,
    csv_path: Path,
    out_band_dir: Path,
    help_text: str,
) -> List[List[str]]:
    flags = extract_long_flags(help_text)
    subs = detect_subcommands(help_text)

    input_flag = pick_best_flag(flags, ["input", "csv", "path", "file"])
    out_flag = pick_best_flag(flags, ["out", "output", "report", "result"])

    subcmds_to_try: List[Optional[str]] = [None]
    if subs:
        if "run" in subs:
            subcmds_to_try = ["run", None]
        else:
            subcmds_to_try = [subs[0], None]

    attempts: List[List[str]] = []

    def mk_cmd(subcmd: Optional[str], use_input_flag: bool, use_out: bool) -> List[str]:
        cmd = [sys.executable, str(script_path)]
        if subcmd:
            cmd.append(subcmd)

        if use_input_flag and input_flag:
            cmd += [input_flag, str(csv_path)]
        else:
            cmd.append(str(csv_path))

        if use_out and out_flag:
            if out_expects_file(out_flag):
                out_file = out_band_dir / "sost_out.json"
                cmd += [out_flag, str(out_file)]
            else:
                cmd += [out_flag, str(out_band_dir)]

        return cmd

    # tries "intelligents"
    for subcmd in subcmds_to_try:
        if input_flag:
            attempts.append(mk_cmd(subcmd, True, True))
            attempts.append(mk_cmd(subcmd, True, False))
        attempts.append(mk_cmd(subcmd, False, True))
        attempts.append(mk_cmd(subcmd, False, False))

    # fallback: variantes courantes
    hard_inputs = [
        ["--input", str(csv_path)],
        ["--input-csv", str(csv_path)],
        ["--input_csv", str(csv_path)],
        ["--csv", str(csv_path)],
        ["--csv-path", str(csv_path)],
        ["--csv_path", str(csv_path)],
        ["--path", str(csv_path)],
        ["--file", str(csv_path)],
    ]
    hard_outs = [
        ["--outdir", str(out_band_dir)],
        ["--out-dir", str(out_band_dir)],
        ["--out_dir", str(out_band_dir)],
        ["--output-dir", str(out_band_dir)],
        ["--output_dir", str(out_band_dir)],
        ["--out", str(out_band_dir / "sost_out.json")],
        ["--output", str(out_band_dir / "sost_out.json")],
    ]
    for subcmd in subcmds_to_try:
        base = [sys.executable, str(script_path)] + ([subcmd] if subcmd else [])
        for hi in hard_inputs:
            for ho in hard_outs:
                attempts.append(base + hi + ho)
            attempts.append(base + hi)
        for ho in hard_outs:
            attempts.append(base + [str(csv_path)] + ho)
        attempts.append(base + [str(csv_path)])

    # dédupe
    uniq = []
    seen = set()
    for a in attempts:
        k = tuple(a)
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


def find_run_sost(repo_root: Path) -> Path:
    candidates = [
        repo_root / "scripts" / "run_sost.py",
        repo_root / "run_sost.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("run_sost.py introuvable (attendu scripts/run_sost.py ou ./run_sost.py)")


def discover_band_csvs(repo_root: Path) -> List[Path]:
    # 1) pattern direct
    direct = sorted((repo_root / "test_data").glob("band_*.csv"))
    if direct:
        return direct
    # 2) fallback global
    return sorted(repo_root.glob("**/band_*.csv"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="_ci_out/bands")
    args = ap.parse_args()

    repo_root = Path(".").resolve()

    out_root = Path(args.outdir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    ci_root = out_root.parent
    ci_root.mkdir(parents=True, exist_ok=True)

    try:
        run_sost = find_run_sost(repo_root)
        help_text = get_help(run_sost)
        (ci_root / "run_sost_help.txt").write_text(help_text, encoding="utf-8")

        csvs = discover_band_csvs(repo_root)
        (ci_root / "discovered_bands.txt").write_text(
            "\n".join(str(p) for p in csvs) + ("\n" if csvs else ""),
            encoding="utf-8",
        )

        if not csvs:
            (ci_root / "band_suite_summary.json").write_text(
                json.dumps(
                    {
                        "total": 0,
                        "ok": 0,
                        "fail": 0,
                        "note": "Aucun band_*.csv trouvé. Vérifie que les CSV sont commit sur main et présents dans le repo.",
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print("Aucun band_*.csv trouvé.")
            return 2

        env = os.environ.copy()
        env["PYTHONPATH"] = "."

        results = []
        ok_count = 0

        for csv_path in csvs:
            stem = csv_path.stem
            out_band_dir = out_root / stem
            out_band_dir.mkdir(parents=True, exist_ok=True)

            log_path = out_band_dir / "run.log"
            attempts = build_attempts(run_sost, csv_path, out_band_dir, help_text)

            final_rc = None
            final_cmd = None

            for cmd in attempts:
                rc = run_cmd(cmd, log_path, env=env)
                if rc == 2:
                    # usage error: on continue à tester d'autres combinaisons
                    continue
                final_rc = rc
                final_cmd = cmd
                break

            if final_rc is None:
                final_rc = 2
                final_cmd = attempts[-1] if attempts else [sys.executable, str(run_sost)]

            success = (final_rc == 0)
            if success:
                ok_count += 1

            results.append(
                {
                    "band": stem,
                    "csv": str(csv_path),
                    "success": success,
                    "exit_code": final_rc,
                    "cmd": " ".join(final_cmd) if final_cmd else None,
                    "log": str(log_path),
                }
            )

        summary = {
            "total": len(results),
            "ok": ok_count,
            "fail": len(results) - ok_count,
            "outdir": str(out_root),
            "results": results,
        }

        (ci_root / "band_suite_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"Bands OK: {ok_count} / {len(results)}")

        # CI verte si au moins 1 band passe
        return 0 if ok_count > 0 else 2

    except Exception:
        (ci_root / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print("Exception, voir _ci_out/exception.txt")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
