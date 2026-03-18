"""
Download the latest TrackHist GPX snapshot for a race rider.

This standalone helper script is intended for manual testing/debug use from
the `tests/` folder.

USAGE EXAMPEL (BASH): .venv/bin/python tests/download_latest_track_hist_gpx.py <race_rider_id>
"""

#### for running in vscode (comment out when on Raspberry Pi)
import os
import sys

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
####

from pathlib import Path
from typing import Tuple
import argparse

from src.db.models import SessionLocal, TrackHist


def download_latest_track_hist_gpx(race_rider_id: int) -> Tuple[bool, str]:
    """
    Download the newest non-empty GPX snapshot from track_hist for a race rider.

    Parameters
    ----------
    race_rider_id : int
        The race_riders.id value to look up in track_hist.

    Returns
    -------
    (ok, path_or_error) : (bool, str)
        True and absolute output path when successful, otherwise False and an
        error message.

    Behavior
    --------
    - Reads TrackHist rows for the given race_rider_id.
    - Filters to rows that have a non-null, non-empty GPX payload.
    - Picks the latest row by updated_at_epoch (nulls last), then updated_at,
      then id.
    - Writes the GPX text to the project-level downloads directory:
      <repo_root>/downloads/.
    """
    session = SessionLocal()
    try:
        latest_track_hist = (
            session.query(TrackHist)
            .filter(
                TrackHist.race_rider_id == race_rider_id,
                TrackHist.gpx.isnot(None),
                TrackHist.gpx != "",
            )
            .order_by(
                TrackHist.updated_at_epoch.desc().nullslast(),
                TrackHist.updated_at.desc().nullslast(),
                TrackHist.id.desc(),
            )
            .first()
        )

        if not latest_track_hist:
            return False, f"No non-empty GPX found in track_hist for race_rider_id={race_rider_id}."

        # Build a stable absolute downloads path anchored to the repository root
        # so output location is consistent regardless of OS user home directory.
        repo_root = Path(__file__).resolve().parents[1]
        downloads_dir = repo_root / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        # Include race rider id + track_hist id in the filename so repeated runs
        # across different riders/snapshots remain easy to distinguish.
        output_file = downloads_dir / f"race_rider_{race_rider_id}_track_hist_{latest_track_hist.id}.gpx"
        output_file.write_text(latest_track_hist.gpx, encoding="utf-8")

        return True, str(output_file.resolve())
    except Exception as e:
        return False, f"download_latest_track_hist_gpx error: {e}"
    finally:
        session.close()


def main() -> int:
    """
    CLI entry point for downloading latest track_hist GPX by race_rider_id.

    Returns
    -------
    int
        Process exit code (0 success, 1 failure).
    """
    parser = argparse.ArgumentParser(description="Download latest track_hist GPX by race_rider_id.")
    parser.add_argument("race_rider_id", type=int, help="race_riders.id to fetch from track_hist")
    args = parser.parse_args()

    ok, path_or_error = download_latest_track_hist_gpx(args.race_rider_id)
    if not ok:
        print(path_or_error)
        return 1

    print(f"GPX saved to: {path_or_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
