
#### for running in vscode (comment out when on Raspberry Pi)
import sys
import os

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
####

from src.utils.delete_points_by_epoch import delete_points_by_epoch_range

# Example 1: Delete all points between two times
count = delete_points_by_epoch_range(
    start_epoch=1737233513,  # Feb 16, 2026 12:00:00 UTC
    end_epoch=1771102313,    # Feb 17, 2026 12:00:00 UTC
    dry_run=True  # Preview first
)
# Example 2: Delete all points between two times
count = delete_points_by_epoch_range(
    start_epoch=1737233513,  # Feb 16, 2026 12:00:00 UTC
    end_epoch=1771102313,    # Feb 17, 2026 12:00:00 UTC
)

# # Example 2: Delete points for specific device
# delete_points_by_epoch_range(
#     start_epoch=1708108800,
#     end_epoch=1708195200,
#     device_id='pi001',
#     dry_run=True
# )

# # Example 3: Actually delete (remove dry_run or set to False)
# count = delete_points_by_epoch_range(
#     start_epoch=1708108800,
#     end_epoch=1708195200,
#     device_id='pi001'
# )
print(f"Deleted {count} points")