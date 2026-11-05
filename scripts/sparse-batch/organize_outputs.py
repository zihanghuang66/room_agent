#!/usr/bin/env python3
"""Move legacy output directories under return_output. Run only when jobs are stopped."""
import argparse
import json
from pathlib import Path

OUTPUT_DIRS = ("artifacts", "environment_reports", "setup_reports", "wheelhouse",
               "reconstruction_reports", "validation_reports")


def organize(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Project directory does not exist")
    destination = root / "return_output"
    if destination.exists() and not destination.is_dir():
        raise ValueError("return_output is not a directory")
    moves = []
    empty_after_merge = []
    for name in OUTPUT_DIRS:
        source = root / name
        if source.is_symlink():
            raise ValueError("Refusing to move a symlink: " + str(source))
        if not source.exists():
            continue
        if not source.is_dir():
            raise ValueError("Expected output directory: " + str(source))
        target = destination / name
        if target.is_symlink():
            raise ValueError("Refusing to merge into a symlink: " + str(target))
        if target.exists():
            if not target.is_dir():
                raise ValueError("Output destination is not a directory: " + str(target))
            for item in sorted(source.iterdir()):
                next_target = target / item.name
                if next_target.exists() or next_target.is_symlink():
                    raise FileExistsError("No files moved; destination conflict: " + str(next_target))
                moves.append((item, next_target))
            empty_after_merge.append(source)
        else:
            moves.append((source, target))
    # Detect all name collisions before changing any source directory.
    destination.mkdir(exist_ok=True)
    for source, target in moves:
        source.rename(target)
    for source in empty_after_merge:
        source.rmdir()
    return [{"from": str(source), "to": str(target)} for source, target in moves]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default="/yinxin/projects/room")
    args = parser.parse_args()
    print(json.dumps(organize(args.project_root), ensure_ascii=False, indent=2))
