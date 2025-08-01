"""Terminal module for Ashy Terminal."""

# Import translation utility
from ..utils.translation_utils import _

from .manager import TerminalManager
from .tabs import TabManager
from .spawner import ProcessSpawner, get_spawner

__all__ = [
    "TerminalManager",
    "TabManager", 
    "ProcessSpawner",
    "get_spawner"
]
