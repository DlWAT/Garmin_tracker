"""Legacy import path.

Prefer importing from `garmin_tracker.client_manager`.
"""

from garmin_tracker.client_manager import GarminClientHandler

__all__ = ["GarminClientHandler"]
