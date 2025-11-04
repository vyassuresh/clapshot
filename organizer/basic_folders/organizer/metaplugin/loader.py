"""
Metaplugin loader for the Basic Folders organizer.

Enables extensibility through user-defined Python modules that can hook into
various stages of the organizer's request processing.
"""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, TYPE_CHECKING
from logging import Logger, LoggerAdapter

import clapshot_grpc.proto.clapshot.organizer as org

from .types import OrganizerContext, MetaPluginInterface, FolderContext

if TYPE_CHECKING:
    from organizer import OrganizerInbound


class PluginLoggerAdapter(LoggerAdapter):
    """
    LoggerAdapter that prepends the plugin name to all log messages.

    Example:
        adapter = PluginLoggerAdapter(logger, "calculate_sha256")
        adapter.info("Starting calculation")  # Logs: [calculate_sha256] Starting calculation
    """

    def process(self, msg, kwargs):
        """Add plugin name prefix to every log message."""
        plugin_name = self.extra.get("plugin_name", "unknown")
        return f"[{plugin_name}] {msg}", kwargs


class MetaPluginLoader:
    """
    Loader for metaplugins.

    Discovers and loads Python modules from a configured directory,
    instantiates their Plugin class, and provides methods to call hooks.
    """

    def __init__(
        self,
        plugins_dir: str,
        logger: Logger,
    ):
        """
        Initialize the loader.

        Args:
            plugins_dir: Directory containing plugin files (.py files to load)
            logger: Logger instance
        """
        self.plugins_dir = Path(plugins_dir)
        self.log = logger
        self.loaded_plugins: list[MetaPluginInterface] = []

    def load_plugins(self) -> None:
        """
        Load all plugins from the plugins directory.

        Automatically discovers all .py files in the directory and attempts to load them.
        Logs errors but doesn't raise - allows organizer to continue
        even if individual plugins fail to load.
        """
        if not self.plugins_dir.exists():
            self.log.debug(
                f"Metaplugins directory does not exist: {self.plugins_dir}"
            )
            return

        # Find all .py files in the directory
        plugin_files = sorted(self.plugins_dir.glob("*.py"))

        if not plugin_files:
            self.log.debug(f"No metaplugins found in {self.plugins_dir}")
            return

        for plugin_path in plugin_files:
            plugin_name = plugin_path.stem  # filename without .py

            try:
                self._load_plugin_from_file(plugin_name, plugin_path)
            except Exception as e:
                self.log.error(
                    f"Failed to load metaplugin '{plugin_name}': {e}",
                    exc_info=True,
                )

    def _load_plugin_from_file(self, plugin_name: str, plugin_path: Path) -> None:
        """
        Load a single plugin from a Python file.

        Args:
            plugin_name: Plugin name (for module naming)
            plugin_path: Path to the .py file

        Raises:
            Various exceptions if loading fails
        """
        # Load module dynamically
        module_name = f"metaplugin_{plugin_name}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_path)
        if not spec or not spec.loader:
            raise ValueError(f"Could not create module spec for {plugin_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Instantiate plugin
        if not hasattr(module, "Plugin"):
            raise ValueError(f"Plugin class 'Plugin' not found in {plugin_path}")

        plugin_instance = module.Plugin()

        # Validate required attributes
        if not hasattr(plugin_instance, "PLUGIN_NAME"):
            raise ValueError(f"Plugin missing PLUGIN_NAME attribute")
        if not hasattr(plugin_instance, "PLUGIN_VERSION"):
            raise ValueError(f"Plugin missing PLUGIN_VERSION attribute")

        self.loaded_plugins.append(plugin_instance)

        self.log.info(
            f"Loaded metaplugin: {plugin_instance.PLUGIN_NAME} "
            f"v{plugin_instance.PLUGIN_VERSION}"
        )

    async def call_on_init_hooks(self, organizer_context: OrganizerContext) -> None:
        """
        Call on_init() hooks for all loaded metaplugins.

        Each metaplugin receives a context with a logger that automatically
        prepends the plugin's name to all log messages.

        Args:
            organizer_context: OrganizerContext to pass to metaplugins
        """
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "on_init"):
                continue

            try:
                # Create a plugin-specific logger that prepends the plugin name
                plugin_logger = PluginLoggerAdapter(
                    organizer_context.log,
                    {"plugin_name": plugin.PLUGIN_NAME}
                )

                # Create a new context with the plugin-specific logger
                plugin_context = replace(organizer_context, log=plugin_logger)

                await plugin.on_init(plugin_context)
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed in on_init: {e}",
                    exc_info=True,
                )

    async def call_on_shutdown_hooks(self) -> None:
        """Call on_shutdown() hooks for all loaded metaplugins."""
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "on_shutdown"):
                continue

            try:
                await plugin.on_shutdown()
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed in on_shutdown: {e}",
                    exc_info=True,
                )

    def call_extend_actions_hooks(self, actions: dict) -> dict:
        """
        Call extend_actions() hooks for all metaplugins.

        Args:
            actions: Initial actions dict

        Returns:
            Modified actions dict (or original if no metaplugins modify it)
        """
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "extend_actions"):
                continue

            try:
                actions = plugin.extend_actions(actions)
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed in extend_actions: {e}",
                    exc_info=True,
                )

        return actions

    async def call_handle_custom_command_hooks(
        self,
        cmd: str,
        args: dict[str, Any],
        session: org.UserSessionData,
        organizer_context: "OrganizerInbound",
    ) -> bool:
        """
        Call handle_custom_command() hooks for all metaplugins until one handles it.

        Args:
            cmd: Command name
            args: Parsed arguments
            session: UserSessionData
            organizer_context: OrganizerInbound instance

        Returns:
            True if any metaplugin handled the command, False otherwise
        """
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "handle_custom_command"):
                continue

            try:
                if await plugin.handle_custom_command(cmd, args, session, organizer_context):
                    return True  # Command was handled
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed handling command '{cmd}': {e}",
                    exc_info=True,
                )
                # Don't suppress exceptions - let them propagate to client
                raise

        return False  # No metaplugin handled it

    async def call_augment_folder_listing_hooks(
        self,
        listing_items: list,
        folder_context: FolderContext,
        session: org.UserSessionData,
    ) -> list:
        """
        Call augment_folder_listing() hooks for all metaplugins.

        Args:
            listing_items: Items to augment
            folder_context: FolderContext with current folder and parent folder
            session: UserSessionData

        Returns:
            Modified listing_items
        """
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "augment_folder_listing"):
                continue

            try:
                listing_items = await plugin.augment_folder_listing(
                    listing_items, folder_context, session
                )
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed in augment_folder_listing: {e}",
                    exc_info=True,
                )

        return listing_items

    async def call_augment_listing_data_hooks(
        self,
        listing_data: dict,
        folder_context: FolderContext,
        session: org.UserSessionData,
    ) -> dict:
        """
        Call augment_listing_data() hooks for all metaplugins.

        Args:
            listing_data: Data dict to augment
            folder_context: Folder context dict
            session: UserSessionData

        Returns:
            Modified listing_data
        """
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "augment_listing_data"):
                continue

            try:
                listing_data = await plugin.augment_listing_data(
                    listing_data, folder_context, session
                )
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed in augment_listing_data: {e}",
                    exc_info=True,
                )

        return listing_data

    async def call_check_action_authorization_hooks(
        self,
        action: str,
        folder: Any | None = None,
        media_file: Any | None = None,
        session: org.UserSessionData | None = None,
    ) -> bool | None:
        """
        Call check_action_authorization() hooks for all metaplugins.

        Returns the first non-None result (first plugin to decide wins),
        or None if no plugin has an opinion.

        Args:
            action: Operation name ("rename_folder", "trash_folder", etc.)
            folder: Folder being acted upon
            media_file: Media file being acted upon
            session: UserSessionData

        Returns:
            True/False if a metaplugin made a decision
            None if all plugins deferred to default checks
        """
        for plugin in self.loaded_plugins:
            if not hasattr(plugin, "check_action_authorization"):
                continue

            try:
                result = await plugin.check_action_authorization(
                    action, folder, media_file, session
                )
                if result is not None:
                    return result  # Plugin made a decision
            except Exception as e:
                self.log.error(
                    f"Metaplugin {plugin.PLUGIN_NAME} failed in check_action_authorization: {e}",
                    exc_info=True,
                )

        return None  # No plugin decided
