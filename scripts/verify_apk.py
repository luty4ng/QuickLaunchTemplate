#!/usr/bin/env python
"""Verify that the APK we just built is really *our* app.

Two things are checked, both straight out of the archive, so this needs no
Android tooling on PATH (aapt2 lives inside build-tools and is easy to miss):

  1. the package id in the binary manifest is the one Capacitor generated;
  2. the compiled web bundle is inside assets/public/.

Usage:  python scripts/verify_apk.py path/to/app-debug.apk
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

EXPECTED_PACKAGE = "dev.quicklaunch.app"
REQUIRED_ASSET = "assets/public/index.html"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("--package", default=EXPECTED_PACKAGE)
    args = parser.parse_args()

    if not args.apk.is_file():
        print(f"FAIL no apk at {args.apk}")
        return 1

    size_mb = args.apk.stat().st_size / 1024 / 1024
    print(f"apk: {args.apk} ({size_mb:.1f} MB)")

    with zipfile.ZipFile(args.apk) as archive:
        names = archive.namelist()

        if "AndroidManifest.xml" not in names:
            print("FAIL no AndroidManifest.xml in the apk")
            return 1
        manifest = archive.read("AndroidManifest.xml")
        if args.package.encode() not in manifest:
            print(f"FAIL package id {args.package!r} not found in AndroidManifest.xml")
            return 1
        print(f"ok   package id {args.package} present in the binary manifest")

        if REQUIRED_ASSET not in names:
            print(f"FAIL {REQUIRED_ASSET} missing - the web bundle was not packaged")
            return 1
        print(f"ok   {REQUIRED_ASSET} present")

        html = archive.read(REQUIRED_ASSET).decode("utf-8", errors="replace")
        if '<div id="root">' not in html:
            print("FAIL assets/public/index.html is not the QuickLaunch shell")
            return 1
        print("ok   web shell inside the apk is the QuickLaunch bundle")

        assets = [n for n in names if n.startswith("assets/public/")]
        print(f"ok   {len(assets)} files packaged under assets/public/")

    print("APK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
