"""SO-101 arm control for the SharkByte 2026 waste sorter. See arm/README.md and ARCHITECTURE.md."""

from .backend import MockBackend, So101Backend
from .config import BINS, JOINTS, ArmConfig
from .controller import ArmController, ArmState
from .poses import Poses, PosesError, demo_poses, load as load_poses

__all__ = [
    "ArmConfig",
    "ArmController",
    "ArmState",
    "BINS",
    "JOINTS",
    "MockBackend",
    "Poses",
    "PosesError",
    "So101Backend",
    "demo_poses",
    "load_poses",
]
