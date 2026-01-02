from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CleanupReport:
    deleted_files: int = 0
    deleted_dirs: int = 0
    skipped: int = 0


def _repo_root() -> Path:
    # tools/cleanup.py -> repo root
    return Path(__file__).resolve().parents[1]


def _safe_unlink(path: Path, report: CleanupReport) -> None:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
            report.deleted_files += 1
        else:
            report.skipped += 1
    except Exception:
        report.skipped += 1


def _safe_rmtree(path: Path, report: CleanupReport) -> None:
    try:
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            report.deleted_dirs += 1
        else:
            report.skipped += 1
    except Exception:
        report.skipped += 1


def _delete_py_caches(root: Path) -> CleanupReport:
    report = CleanupReport()
    for p in root.rglob("__pycache__"):
        _safe_rmtree(p, report)
    for p in root.rglob("*.pyc"):
        _safe_unlink(p, report)
    return report


def _db_path(root: Path) -> Path | None:
    # Common Flask instance DB paths
    candidates = [
        root / "instance" / "app.db",
        root / "instance" / "garmin_tracker.db",
        root / "app.db",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def _db_user_ids(db_file: Path) -> set[str]:
    # Minimal, no ORM dependency: read user_id column from SQLite.
    user_ids: set[str] = set()
    con = sqlite3.connect(str(db_file))
    try:
        # SQLAlchemy model uses __tablename__ = "users".
        cur = con.execute("SELECT user_id FROM users")
        for (uid,) in cur.fetchall():
            if uid is not None:
                user_ids.add(str(uid))
    finally:
        con.close()
    return user_ids


def _vacuum_sqlite(db_file: Path) -> None:
    con = sqlite3.connect(str(db_file))
    try:
        con.execute("PRAGMA optimize")
        con.execute("VACUUM")
    finally:
        con.close()


def _cleanup_generated_static(root: Path) -> CleanupReport:
    report = CleanupReport()

    # Generated ECharts HTML. Safe to delete; regenerated on next page load.
    static_dir = root / "static"
    generated_roots = [
        static_dir / "activity",
        static_dir / "health",
        static_dir / "dashboard",
    ]

    for gr in generated_roots:
        if not gr.exists():
            continue

        # Remove all .html under these dirs (keep js/css).
        for html in gr.rglob("*.html"):
            _safe_unlink(html, report)

        # Remove empty dirs after deleting html
        # (walk bottom-up)
        for d in sorted([p for p in gr.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
            try:
                if not any(d.iterdir()):
                    d.rmdir()
                    report.deleted_dirs += 1
            except Exception:
                report.skipped += 1

    return report


def _cleanup_stale_user_data(root: Path, keep_user_ids: set[str]) -> CleanupReport:
    report = CleanupReport()
    data_dir = root / "data"
    if not data_dir.exists():
        return report

    # Only delete files that match the per-user naming scheme.
    suffixes = {
        "_activities.json",
        "_activity_details.json",
        "_garmin_methods.json",
        "_health.json",
        "_health_daily.json",
        "_profile.json",
    }

    for p in data_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        matched_suffix = next((s for s in suffixes if name.endswith(s)), None)
        if not matched_suffix:
            continue

        user_prefix = name[: -len(matched_suffix)]
        if user_prefix in keep_user_ids:
            continue

        _safe_unlink(p, report)

    # Also remove stale dashboard folders for users not in DB.
    dash_dir = root / "static" / "dashboard"
    if dash_dir.exists():
        for d in dash_dir.iterdir():
            if not d.is_dir():
                continue
            if d.name in keep_user_ids:
                continue
            _safe_rmtree(d, report)

    return report


def main() -> int:
    root = _repo_root()
    print(f"Repo: {root}")

    db_file = _db_path(root)
    keep_user_ids: set[str] = set()

    if db_file:
        print(f"DB:   {db_file}")
        try:
            keep_user_ids = _db_user_ids(db_file)
        except Exception as e:
            print(f"WARN: cannot read users from DB: {e}")
    else:
        print("DB:   (not found; skipping DB cleanup & stale-user pruning)")

    if keep_user_ids:
        print(f"Keep users: {sorted(keep_user_ids)}")

    r1 = _delete_py_caches(root)
    r2 = _cleanup_generated_static(root)
    r3 = _cleanup_stale_user_data(root, keep_user_ids) if keep_user_ids else CleanupReport()

    if db_file:
        try:
            _vacuum_sqlite(db_file)
            print("SQLite: VACUUM + optimize done")
        except Exception as e:
            print(f"WARN: SQLite vacuum failed: {e}")

    def _sum(*reports: CleanupReport) -> CleanupReport:
        out = CleanupReport()
        for r in reports:
            out.deleted_files += r.deleted_files
            out.deleted_dirs += r.deleted_dirs
            out.skipped += r.skipped
        return out

    total = _sum(r1, r2, r3)
    print(
        "Deleted files:",
        total.deleted_files,
        "Deleted dirs:",
        total.deleted_dirs,
        "Skipped:",
        total.skipped,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
