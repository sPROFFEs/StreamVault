#!/usr/bin/env python3
"""Run shared checks from any directory without starting production services."""
import argparse
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def run(command, env=None):
    print("Running: " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def validate():
    from jinja2 import Environment

    python_files = sorted((ROOT / "vault/core").rglob("*.py"))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    templates = sorted((ROOT / "vault/core/templates").rglob("*.html"))
    environment = Environment()
    for path in templates:
        environment.parse(path.read_text(encoding="utf-8"))
    javascript = sorted((ROOT / "vault/assets/js").rglob("*.js"))
    for path in javascript:
        run(["node", "--check", str(path)])
    run(["sh", "-n", "vault/core/entrypoint.sh"])
    print(f"Validated {len(python_files)} Python modules, {len(templates)} templates, "
          f"{len(javascript)} JavaScript files and the core entrypoint.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("unit", "baseline", "browser", "validate", "build"))
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                        help="Additional pytest arguments, e.g. a test file or --viewport desktop")
    args = parser.parse_args()
    extra = args.pytest_args
    if extra[:1] == ["--"]:
        extra = extra[1:]
    if args.check in {"unit", "baseline", "browser"}:
        options = {
            "unit": ["-m", "not browser"],
            "baseline": ["--browser", "-m", "baseline"],
            "browser": ["--browser", "-m", "browser"],
        }[args.check]
        run([sys.executable, "-m", "pytest", *options, *extra])
        return
    if extra:
        parser.error("Additional arguments apply only to pytest checks")
    validate()
    if args.check == "build":
        if not shutil.which("docker"):
            raise RuntimeError("Production image build requires Docker with the Compose plugin and a running daemon")
        # Only validate/build images. Do not invoke up, restart or the updater.
        env = dict(os.environ)
        env.setdefault("SECRET_KEY", "streamvault-build-check-only-secret")
        compose = ["docker", "compose", "--env-file", "vault/.env.example", "-f", "vault/docker-compose.yaml"]
        run([*compose, "config", "--quiet"], env=env)
        run([*compose, "build", "core", "downloader", "cloudflared"], env=env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Check failed: {error}", file=sys.stderr)
        sys.exit(1)
