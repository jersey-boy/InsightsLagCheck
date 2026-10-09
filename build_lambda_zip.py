"""
Build a Lambda deployment zip for the Insights-vs-NationBuilder comparison.

Produces `lambda_deploy.zip` containing:
  * the application source (lambda_pipeline.py + the three pull/compare scripts
    + nb_insights.py) and the `nationbuilder_v2` package,
  * the dependencies that are NOT in the AWS-managed pandas layer, installed as
    Linux (manylinux) wheels so they run on the Lambda runtime.

pandas and numpy are intentionally EXCLUDED — they come from the AWS-managed
layer `AWSSDKPandas-Python312` attached to the function (see DEPLOY.md). boto3 is
also excluded because it is already in the Lambda runtime.

IMPORTANT — this project pulls from NationBuilder Insights via Tableau, so unlike
the V2-only ZipStateFed build it must bundle `tableau-api-lib` and its (non-pandas)
dependency chain: requests, requests-toolbelt, bleach, typeguard, packaging, etc.
Those are listed in DEPENDENCIES below; pip resolves their transitive deps.

Why a script (not `pip install` into a folder directly): on Windows, a plain
`pip install` grabs Windows wheels that will not run on Lambda. We force Linux
wheels with `--platform manylinux2014_x86_64 --only-binary=:all:`.

Usage (from the repo root, any OS with Python + pip):
    python build_lambda_zip.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build" / "package"
ZIP_PATH = ROOT / "lambda_deploy.zip"

# Application source files to include at the zip root.
SOURCE_FILES = [
    "lambda_pipeline.py",
    "pull_insights.py",
    "pull_nationbuilder.py",
    "compare_pipelines.py",
    "nb_insights.py",
]

# Application packages (directories) to include at the zip root.
SOURCE_PACKAGES = ["nationbuilder_v2"]

# Dependencies to bundle as Linux wheels. pandas/numpy (managed layer) and boto3
# (runtime) are deliberately omitted. `tableau-api-lib` drags in requests,
# requests-toolbelt, bleach, typeguard, and packaging — pip pulls those
# transitively, but we list the direct ones explicitly for clarity.
DEPENDENCIES = [
    "tableau-api-lib==0.1.45",
    "openpyxl==3.1.5",
    "python-dotenv==1.0.1",
    "httpx==0.27.2",
    "requests>2",
]

# Packages to prune after install because they are provided by the environment:
#   pandas / numpy -> AWS-managed pandas layer (large; always present there).
#   boto3 / botocore / s3transfer / jmespath -> Lambda runtime + pandas layer.
# NOTE: we deliberately KEEP pandas' small runtime deps (pytz, tzdata,
# python-dateutil, six) in the zip. They are provided by the pandas layer today,
# but they are tiny (~1 MB total) and bundling them guarantees pandas imports
# even if a future layer build omits one — a cheap insurance against a hard-to-
# diagnose cold-start ImportError.
PROVIDED_BY_LAYER = [
    "pandas",
    "numpy",
    "boto3",
    "botocore",
    "s3transfer",
    "jmespath",
]

# Lambda x86_64 / Python 3.12 wheel target.
PIP_PLATFORM = "manylinux2014_x86_64"
PYTHON_VERSION = "3.12"


def clean() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR.parent)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()


def pip_install_linux_wheels() -> None:
    """Install dependencies as Linux wheels into BUILD_DIR."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(BUILD_DIR),
        "--platform", PIP_PLATFORM,
        "--python-version", PYTHON_VERSION,
        "--implementation", "cp",
        "--only-binary=:all:",
        "--upgrade",
        *DEPENDENCIES,
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def prune_layer_packages() -> None:
    """Delete packages provided by the AWS-managed layer / runtime from BUILD_DIR."""
    removed = []
    for name in PROVIDED_BY_LAYER:
        for entry in BUILD_DIR.iterdir():
            base = entry.name.lower()
            if base == name or base.startswith(f"{name}-") or base.startswith(
                f"{name}."
            ):
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed.append(entry.name)
    print("Pruned layer-provided packages:", ", ".join(sorted(removed)) or "(none)")


def build_zip() -> None:
    """Zip the installed dependencies plus the application source."""
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        # Dependencies (contents of BUILD_DIR at the zip root).
        for path in BUILD_DIR.rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_file():
                zf.write(path, path.relative_to(BUILD_DIR))
        # Application source files at the zip root.
        for name in SOURCE_FILES:
            src = ROOT / name
            if not src.exists():
                raise FileNotFoundError(f"Missing source file: {src}")
            zf.write(src, name)
        # Application source packages (directories) at the zip root.
        for pkg in SOURCE_PACKAGES:
            pkg_dir = ROOT / pkg
            if not pkg_dir.is_dir():
                raise FileNotFoundError(f"Missing source package: {pkg_dir}")
            for path in pkg_dir.rglob("*"):
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                if path.is_file():
                    zf.write(path, path.relative_to(ROOT))
    print(f"Wrote {ZIP_PATH}  ({ZIP_PATH.stat().st_size / 1_048_576:.1f} MB)")


def main() -> None:
    clean()
    pip_install_linux_wheels()
    prune_layer_packages()
    build_zip()
    print("Done. Upload lambda_deploy.zip and attach the AWSSDKPandas layer.")


if __name__ == "__main__":
    main()
