#!/usr/bin/env python3
"""Build a reproducible, uploadable Agent Skill archive from repository files."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "jordan-curriculum-planning"
OUTPUT = ROOT / "dist" / "jordan-curriculum-planning.zip"


def main():
    if not (SKILL / "SKILL.md").is_file():
        raise SystemExit("SKILL.md missing")
    OUTPUT.parent.mkdir(exist_ok=True)
    files = sorted(p for p in SKILL.rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Archive integrity check failed")
        if f"{SKILL.name}/SKILL.md" not in archive.namelist():
            raise SystemExit("Unexpected archive layout")
    print(f"Built {OUTPUT.relative_to(ROOT)} ({len(files)} files)")


if __name__ == "__main__":
    main()
