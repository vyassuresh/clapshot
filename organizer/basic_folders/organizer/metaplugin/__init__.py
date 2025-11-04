"""
Metaplugin system for the Basic Folders Organizer.

This submodule provides the plugin loader, type definitions, and interfaces
for extending the organizer with custom Python modules.
"""

from .types import OrganizerContext, FolderContext, MetaPluginInterface
from .loader import MetaPluginLoader

__all__ = [
    "OrganizerContext",
    "FolderContext",
    "MetaPluginInterface",
    "MetaPluginLoader",
]
