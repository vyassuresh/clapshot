"""
Type definitions for metaplugins.

Metaplugin developers should install this package in a venv and import from here
for full IDE support and type checking:

    pip install -e /path/to/clapshot-organizer-basic-folders
    pip install -e /path/to/clapshot-protobuf

Then in your metaplugin:

    from organizer.metaplugin import OrganizerContext, MetaPluginInterface
"""

from dataclasses import dataclass
from logging import Logger, LoggerAdapter
from typing import Any, Optional, TYPE_CHECKING
from sqlalchemy import orm

from organizer.database.models import DbFolder
import clapshot_grpc.proto.clapshot.organizer as org
import clapshot_grpc.proto.clapshot as clap

if TYPE_CHECKING:
    from organizer.helpers.folders import FoldersHelper
    from organizer.helpers.pages import PagesHelper
    from organizer import OrganizerInbound


@dataclass(frozen=True)
class FolderContext:
    """
    Context about the current folder being displayed.

    Passed to metaplugin hooks when constructing folder listings.
    """

    folder: DbFolder
    """Current folder being displayed"""

    parent: Optional[DbFolder] = None
    """Parent folder, or None if at root"""


@dataclass(frozen=True)
class OrganizerContext:
    """
    Context passed to metaplugins during initialization.

    All metaplugins receive this immutable context in their on_init() method.
    It provides access to the organizer's internal utilities and server connection.
    """

    db_session: orm.sessionmaker
    """SQLAlchemy sessionmaker for database operations"""

    srv: org.OrganizerOutboundStub
    """gRPC stub for bidirectional communication with Clapshot server"""

    log: Logger | LoggerAdapter
    """Logger instance for the organizer (may be wrapped with LoggerAdapter for plugin-specific logging)"""

    folders_helper: "FoldersHelper"
    """Helper for folder operations and queries"""

    pages_helper: "PagesHelper"
    """Helper for constructing UI pages"""


class MetaPluginInterface:
    """
    Base class for metaplugins.

    Metaplugins should inherit from this class and override the hooks they need.
    All methods have sensible defaults, so you only need to implement what you use.

    Required attributes:
        PLUGIN_NAME: str - Unique metaplugin identifier (e.g., "my_metaplugin")
        PLUGIN_VERSION: str - Metaplugin version string (e.g., "1.0.0")
    """

    PLUGIN_NAME: str
    """Unique metaplugin identifier (e.g., "my_metaplugin")"""

    PLUGIN_VERSION: str
    """Metaplugin version string (e.g., "1.0.0")"""

    async def on_init(self, context: OrganizerContext) -> None:
        """
        Called once during organizer initialization after server handshake.

        Use this to store the context and initialize your metaplugin.

        Args:
            context: OrganizerContext with access to database, server, and helpers

        Example:
            async def on_init(self, context: OrganizerContext):
                self.ctx = context
                self.log = context.log
                self.log.info(f"{self.PLUGIN_NAME} initialized")
        """
        pass

    async def on_shutdown(self) -> None:
        """
        Called when organizer is shutting down.

        Use this to clean up resources, close connections, flush data, etc.

        Example:
            async def on_shutdown(self):
                self.log.info(f"{self.PLUGIN_NAME} shutting down")
        """
        pass

    def extend_actions(self, actions: dict[str, clap.ActionDef]) -> dict[str, clap.ActionDef]:
        """
        Add or modify action definitions available to users.

        Called during user session startup to define popup menu actions.

        Args:
            actions: dict of action_name -> ActionDef protobuf objects

        Returns:
            Modified actions dictionary

        Example:
            def extend_actions(self, actions):
                from textwrap import dedent
                import clapshot_grpc.proto.clapshot as clap

                actions["my_action"] = clap.ActionDef(
                    ui_props=clap.ActionUiProps(
                        label="My Action",
                        icon=clap.Icon(fa_class=clap.IconFaClass(
                            classes="fa fa-star"))),
                    action=clap.ScriptCall(
                        lang=clap.ScriptCallLang.JAVASCRIPT,
                        code="clapshot.callOrganizer('my_command', {...});"))

                return actions
        """
        return actions

    async def handle_custom_command(
        self, cmd: str, args: dict[str, Any], session: org.UserSessionData, organizer: "OrganizerInbound"
    ) -> bool:
        """
        Handle custom commands from the client.

        Called when client executes a command (via clapshot.callOrganizer).
        Metaplugins are tried in load order - return True if your metaplugin handles it.

        Args:
            cmd: Command name (e.g., "my_command")
            args: Parsed JSON arguments from client
            session: UserSessionData protobuf with user info, is_admin, cookies, etc.
            organizer: OrganizerInbound instance (for access to context)

        Returns:
            True if command was handled, False to try other metaplugins/handlers

        Example:
            async def handle_custom_command(self, cmd, args, session, organizer):
                if cmd != "my_command":
                    return False

                # Do something
                media_file_id = args.get("media_file_id")
                # ... process ...

                # Refresh UI
                page = await organizer.pages_helper.construct_navi_page(session, None)
                await organizer.srv.client_show_page(page)

                return True
        """
        return False

    async def augment_folder_listing(
        self, listing_items: list[clap.PageItemFolderListingItem], folder_context: "FolderContext", session: org.UserSessionData
    ) -> list[clap.PageItemFolderListingItem]:
        """
        Modify folder listing items before they're displayed to the user.

        Called when constructing a folder listing page. Metaplugins are called in load order,
        each receiving the output of the previous metaplugin.

        Args:
            listing_items: list of PageItem.FolderListing.Item protobuf objects
                - Each item has either item.folder or item.media_file
                - Each has popup_actions list and optional vis (visualization)
            folder_context: FolderContext with current folder and parent folder
            session: UserSessionData with user info

        Returns:
            Modified listing_items list

        Example:
            async def augment_folder_listing(self, listing_items, folder_context, session):
                for item in listing_items:
                    if not item.media_file:
                        continue

                    # Add custom action to service account videos
                    if item.media_file.user_id == "service_bot":
                        item.popup_actions.append("my_action")

                return listing_items
        """
        return listing_items

    async def augment_listing_data(
        self, listing_data: dict[str, str], folder_context: "FolderContext", session: org.UserSessionData
    ) -> dict[str, str]:
        """
        Add custom data to the folder listing context (passed to JavaScript).

        Called when constructing a folder listing. The listing_data dict is passed
        to client-side JavaScript via _action_args.listing_data.

        Args:
            listing_data: String-keyed dict (values are strings for JavaScript)
            folder_context: FolderContext with current folder and parent folder
            session: UserSessionData

        Returns:
            Modified listing_data dict

        Example:
            async def augment_listing_data(self, listing_data, folder_context, session):
                listing_data["my_metaplugin_active"] = "true"
                listing_data["folder_id"] = str(folder_context.folder.id)
                return listing_data
        """
        return listing_data

    async def check_action_authorization(
        self,
        action: str,
        folder: DbFolder | None = None,
        media_file: clap.MediaFile | None = None,
        session: org.UserSessionData | None = None,
    ) -> bool | None:
        """
        Override permission checks for folder/file operations.

        Called before operations like rename, move, delete, trash, etc.
        Allows metaplugins to implement custom authorization rules (LDAP, service accounts, etc.).

        Args:
            action: Operation name ("rename_folder", "trash_folder", "move_to_folder", etc.)
            folder: The folder being acted upon (for folder operations)
            media_file: The media file being acted upon (for file operations)
            session: UserSessionData with user info, is_admin flag, etc.

        Returns:
            True to allow the operation
            False to deny the operation (will raise PERMISSION_DENIED to client)
            None to use the default permission check

        Example:
            async def check_action_authorization(self, action, folder, media_file, session):
                # Allow LDAP admins to rename any folder
                if action == "rename_folder" and self.is_ldap_admin(session.user.id):
                    return True

                # Deny rename for folders owned by service accounts
                if action == "rename_folder" and folder.user_id == "service_bot":
                    return False

                # Use default checks for everything else
                return None
        """
        return None
