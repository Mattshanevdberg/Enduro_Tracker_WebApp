"""
Utility to delete entries from the Points table within a specified epoch time range.

Usage:
    from delete_points_by_epoch import delete_points_by_epoch_range
    
    # Delete all points between these times
    deleted_count = delete_points_by_epoch_range(
        start_epoch=1708108800,  # Feb 16, 2026 12:00:00 UTC
        end_epoch=1708195200,    # Feb 17, 2026 12:00:00 UTC
        device_id="pi001"        # Optional: filter by device
    )
    print(f"Deleted {deleted_count} points")
"""

from datetime import datetime, timezone
from sqlalchemy import and_
import sys
import os

# Add src to path so we can import db models
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db.models import SessionLocal, Point


def delete_points_by_epoch_range(
    start_epoch: int,
    end_epoch: int,
    device_id: str = None,
    dry_run: bool = False
) -> int:
    """
    Delete entries from the Points table within a specified epoch time range.
    
    Args:
        start_epoch (int): Start time in epoch seconds (UTC). Points with t_epoch >= this value will be deleted.
        end_epoch (int): End time in epoch seconds (UTC). Points with t_epoch <= this value will be deleted.
        device_id (str, optional): If specified, only delete points from this device. Defaults to None (all devices).
        dry_run (bool, optional): If True, show what would be deleted without actually deleting. Defaults to False.
    
    Returns:
        int: Number of points deleted (or would be deleted if dry_run=True).
    
    Raises:
        ValueError: If start_epoch >= end_epoch.
    
    Example:
        >>> # Delete all points from device 'pi001' on Feb 16, 2026
        >>> delete_points_by_epoch_range(
        ...     start_epoch=1708108800,
        ...     end_epoch=1708195200,
        ...     device_id='pi001'
        ... )
        >>> # Delete all points from all devices between Feb 16-17, 2026
        >>> delete_points_by_epoch_range(
        ...     start_epoch=1708108800,
        ...     end_epoch=1708195200
        ... )
    """
    
    # Validate input
    if start_epoch >= end_epoch:
        raise ValueError(
            f"start_epoch ({start_epoch}) must be less than end_epoch ({end_epoch})"
        )
    
    session = SessionLocal()
    
    try:
        # Build the query
        query = session.query(Point).filter(
            and_(
                Point.t_epoch >= start_epoch,
                Point.t_epoch <= end_epoch
            )
        )
        
        # Optional: filter by device
        if device_id:
            query = query.filter(Point.device_id == device_id)
        
        # Count records that will be deleted
        count = query.count()
        
        # Get sample of records for logging
        sample_records = query.limit(5).all()
        
        if dry_run:
            print(f"\n[DRY RUN] Would delete {count} points:")
            print(f"  Time range: {_epoch_to_datetime(start_epoch)} to {_epoch_to_datetime(end_epoch)}")
            if device_id:
                print(f"  Device: {device_id}")
            print(f"\nSample records:")
            for point in sample_records:
                print(f"  - ID: {point.id}, Device: {point.device_id}, t_epoch: {point.t_epoch} "
                      f"({_epoch_to_datetime(point.t_epoch)}), Pos: ({point.lat:.4f}, {point.lon:.4f})")
            if count > len(sample_records):
                print(f"  ... and {count - len(sample_records)} more")
            return count
        
        # Delete the records
        query.delete(synchronize_session="fetch")
        session.commit()
        
        print(f"\n✓ Successfully deleted {count} points:")
        print(f"  Time range: {_epoch_to_datetime(start_epoch)} to {_epoch_to_datetime(end_epoch)}")
        if device_id:
            print(f"  Device: {device_id}")
        
        return count
        
    except Exception as e:
        session.rollback()
        print(f"\n✗ Error deleting points: {e}", file=sys.stderr)
        raise
    finally:
        session.close()


def delete_points_by_device_and_epoch_range(
    device_id: str,
    start_epoch: int,
    end_epoch: int,
    dry_run: bool = False
) -> int:
    """
    Delete entries from the Points table for a specific device within a specified epoch time range.
    
    This is a convenience wrapper around delete_points_by_epoch_range() with device_id pre-filled.
    
    Args:
        device_id (str): Device ID to delete points from.
        start_epoch (int): Start time in epoch seconds (UTC).
        end_epoch (int): End time in epoch seconds (UTC).
        dry_run (bool, optional): If True, show what would be deleted without actually deleting. Defaults to False.
    
    Returns:
        int: Number of points deleted.
    
    Example:
        >>> delete_points_by_device_and_epoch_range(
        ...     device_id='pi001',
        ...     start_epoch=1708108800,
        ...     end_epoch=1708195200,
        ...     dry_run=True  # First do a dry run to see what would be deleted
        ... )
        >>> # If the output looks correct, run without dry_run
        >>> delete_points_by_device_and_epoch_range(
        ...     device_id='pi001',
        ...     start_epoch=1708108800,
        ...     end_epoch=1708195200
        ... )
    """
    return delete_points_by_epoch_range(
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        device_id=device_id,
        dry_run=dry_run
    )


def _epoch_to_datetime(epoch: int) -> str:
    """Convert epoch seconds to readable datetime string."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


if __name__ == "__main__":
    """
    Example usage: Run script from command line to delete points.
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Delete points from the Points table within a specified epoch time range."
    )
    parser.add_argument(
        "start_epoch",
        type=int,
        help="Start time in epoch seconds (UTC)"
    )
    parser.add_argument(
        "end_epoch",
        type=int,
        help="End time in epoch seconds (UTC)"
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Optional: device ID to filter by"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    
    args = parser.parse_args()
    
    try:
        count = delete_points_by_epoch_range(
            start_epoch=args.start_epoch,
            end_epoch=args.end_epoch,
            device_id=args.device,
            dry_run=args.dry_run
        )
    except Exception as e:
        sys.exit(1)

# Use this:


