"""
Example metaplugin: Add a "calculate SHA256 hash" popup command for a single user.

It demonstrates how to implement:
- User-specific action filtering (only shows for 'docker' user)
- Asynchronous background processing with thread pools
- File system access to read original media files
- Adding automated comments via gRPC
- Real-time push progress notifications to clients via OrganizerOutbound
- Error handling and logging

Copyright 2025 Jarno Elonen
SPDX-License-Identifier: MIT
"""

import asyncio
import hashlib
import logging
from logging import Logger, LoggerAdapter
from pathlib import Path
from datetime import datetime
from textwrap import dedent
import time
from typing import Any, Optional, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import clapshot_grpc.proto.clapshot as clap
import clapshot_grpc.proto.clapshot.organizer as org

from organizer.metaplugin import OrganizerContext, FolderContext, MetaPluginInterface

if TYPE_CHECKING:
    from organizer import OrganizerInbound

try:
    from typing import override  # type: ignore   # Python 3.12+
except ImportError:
    def override(func):  # type: ignore
        return func


class Plugin(MetaPluginInterface):
    """
    Metaplugin that adds a SHA256 calculation action for the 'docker' user.

    When the user selects "SHA256" from a media file's popup menu:
    1. Returns immediately to client with feedback message
    2. Calculates SHA256 in background using thread pool (doesn't block async loop)
    3. Adds automated comment to the media file with the hash value
    4. Pushes media file player to client via OrganizerOutbound (real-time update!)

    This demonstrates asynchronous processing without blocking client requests.
    """

    PLUGIN_NAME = "calc_sha256"
    PLUGIN_VERSION = "1.0.0"

    # Only show this action for users with this ID
    TARGET_USER_ID = "docker"

    # Thread pool for CPU-intensive SHA256 calculation
    # Shared across all plugin instances
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sha256_")

    def __init__(self) -> None:
        """Initialize plugin instance. Context will be set in on_init()."""
        self.ctx: Optional[OrganizerContext] = None
        self.log: Logger | LoggerAdapter = logging.getLogger(self.PLUGIN_NAME)  # Set default for typing - this will be replaced in on_init()
        self.data_dir: Optional[Path] = None

    @override
    async def on_init(self, context: OrganizerContext) -> None:
        """
        Called during organizer initialization after handshake.

        Store the context and extract the data directory path from server info.
        """
        self.ctx = context
        self.log = self.ctx.log
        self.log.info(f"{self.PLUGIN_NAME} v{self.PLUGIN_VERSION} initialized")

    @override
    async def on_shutdown(self) -> None:
        """Called when organizer is shutting down."""
        # Shut down thread pool gracefully
        self._executor.shutdown(wait=True)
        if self.log:
            self.log.info(f"{self.PLUGIN_NAME} shutting down")

    @override
    def extend_actions(self, actions: dict[str, clap.ActionDef]) -> dict[str, clap.ActionDef]:
        """
        Add the SHA256 calculation action definition.

        This creates the action that will appear in popup menus. The action
        executes JavaScript in the client that calls back to this organizer
        via clapshot.callOrganizer().

        Note: The action is registered globally, but we filter its visibility
        per-item in augment_folder_listing() to only show for the demo user.
        """
        actions["calc_sha256"] = clap.ActionDef(
            ui_props=clap.ActionUiProps(
                label="SHA256",
                icon=clap.Icon(fa_class=clap.IconFaClass(
                    classes="fa fa-fingerprint",
                    color=clap.Color(r=100, g=200, b=100))),
                natural_desc="Calculate SHA256 hash of the original media file and add as comment"),
            action=clap.ScriptCall(
                lang=clap.ScriptCallLang.JAVASCRIPT,
                code=dedent("""
                    var item = _action_args.selected_items?.[0];
                    if (!item?.mediaFile) {
                        alert("No media file selected");
                        return;
                    }
                    clapshot.callOrganizer("calc_sha256", {media_file_id: item.mediaFile.id});
                """).strip()))

        return actions

    @override
    async def handle_custom_command(
        self, cmd: str, args: dict[str, Any], session: org.UserSessionData, organizer: "OrganizerInbound"
    ) -> bool:
        """
        Handle the calc_sha256 command from the client.

        This returns immediately without blocking. The actual SHA256 calculation
        happens asynchronously in the background, and the media player is pushed
        to the client once the result is ready.

        Args:
            cmd: Command name
            args: Command arguments (should contain 'media_file_id')
            session: UserSessionData with user info
            organizer: The OrganizerInbound instance

        Returns:
            True if this is our command (work will proceed in background)
            False if this is not our command (allows other handlers to try)

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If required arguments are missing
        """
        if cmd != "calc_sha256":
            return False  # Not our command, let other handlers try

        # Validate user
        if session.user.id != self.TARGET_USER_ID:
            self.log.warning(
                f"User {session.user.id} attempted SHA256 calculation, "
                f"but action is only available for {self.TARGET_USER_ID}"
            )
            raise PermissionError(
                f"SHA256 calculation is only available for user '{self.TARGET_USER_ID}'"
            )

        # Extract and validate arguments
        media_file_id = args.get("media_file_id")
        if not media_file_id:
            raise ValueError("calc_sha256 command missing media_file_id")

        # Launch background task - returns immediately!
        asyncio.create_task(self._sha256_async(media_file_id, session, organizer))

        return True  # Command accepted, processing in background

    async def _sha256_async(
        self, media_file_id: str, session: org.UserSessionData, organizer: "OrganizerInbound"
    ) -> None:
        """
        Background task: Calculate SHA256, add comment, notify user, optionally open media player.

        This runs asynchronously without blocking other requests. Uses thread pool
        for the CPU-intensive hash calculation.

        Gracefully handles session timeouts: the message persists in the database
        and reaches the user even if they've disconnected and reconnected.
        """
        try:
            # Get data directory from server info (set during handshake)
            if not organizer.server_info:
                raise RuntimeError("Server info not available")

            if not organizer.server_info.storage.local_fs:
                raise RuntimeError("Server storage is not local filesystem")

            data_dir = Path(organizer.server_info.storage.local_fs.base_dir)
            user_id = session.user.id
            self.log.info(f"Calculating SHA256 for media file: {media_file_id} (user: {user_id})")

            # Query database for media file metadata via gRPC
            assert self.ctx is not None
            media_files_response = await self.ctx.srv.db_get_media_files(
                org.DbGetMediaFilesRequest(
                    ids=org.IdList(ids=[media_file_id])
                )
            )

            if not media_files_response.items:
                raise ValueError(f"Media file {media_file_id} not found in database")

            media_file = media_files_response.items[0]
            media_file_title = media_file.processing_metadata.orig_filename if media_file.processing_metadata else media_file_id

            # Construct path to original file
            if not media_file.processing_metadata or not media_file.processing_metadata.orig_filename:
                raise ValueError(f"Media file {media_file_id} missing original filename metadata")

            orig_filename = media_file.processing_metadata.orig_filename
            file_path = data_dir / "videos" / media_file_id / "orig" / orig_filename

            if not file_path.exists():
                raise FileNotFoundError(f"Original media file not found at: {file_path}")

            # Run CPU-intensive hash calculation in thread pool
            # This doesn't block the async event loop
            self.log.info(f"Reading and hashing file: {file_path}")

            # Create a thread-safe queue for progress updates
            progress_queue: Queue[float] = Queue()

            # CPU bound operation: start the hashing in a background thread
            future = asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._compute_sha256_sync,
                file_path,
                progress_queue
            )

            # Relay progress reports from worker thread
            while not future.done():
                try:
                    progress = progress_queue.get_nowait()
                    progress_msg = clap.UserMessage(
                        type=clap.UserMessageType.PROGRESS,
                        message="Calculating SHA256...",
                        refs=clap.UserMessageRefs(media_file_id=media_file_id),
                        progress=progress
                    )
                    request = org.ClientShowUserMessageRequest(msg=progress_msg)
                    request.user_temp = user_id  # Message recipient = all Clapshot sessions of user who triggered this action
                    await self.ctx.srv.client_show_user_message(request)
                except Exception:
                    # No progress update available, wait a bit
                    await asyncio.sleep(0.1)

            # Get the final result
            hash_value = await future
            self.log.info(f"SHA256 calculated: {hash_value}")

            # Create comment with the result
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            comment = clap.Comment(
                media_file_id=media_file_id,
                user_id=None,
                username_ifnull="SHA256 Calculator",
                comment=f"🔐 SHA256: `{hash_value}`\n\n(Calculated at {timestamp})",
                created=datetime.now()
            )

            # Add comment to media file via gRPC
            await self.ctx.srv.db_upsert(org.DbUpsertRequest(comments=[comment]))
            self.log.info(f"Comment saved for media file {media_file_id}")

            # Send persistent notification to user
            # Using user_persist: reaches all sessions of this user and stores in DB
            # Even if the session times out during the calculation,
            # the user will see this notification when they reconnect
            msg = clap.UserMessage(
                type=clap.UserMessageType.OK,
                message=f"SHA256 calculated for '{media_file_title}':\n`{hash_value}`"
            )
            msg_request = org.ClientShowUserMessageRequest(msg=msg)
            msg_request.user_persist = user_id  # Persist in DB and send to all sessions
            await self.ctx.srv.client_show_user_message(msg_request)
            self.log.info(f"Persistent notification sent to user {user_id}")

            # Try to open media player for immediate viewing
            # If session has expired, this will fail gracefully and the user
            # can open the file manually (they'll see the comment + notification)
            try:
                self.log.info(f"Attempting to open media player for {media_file_id}")
                await self.ctx.srv.client_open_media_file(
                    org.ClientOpenMediaFileRequest(
                        sid=session.sid,
                        id=media_file_id
                    )
                )
                self.log.info("Media player opened successfully")
            except Exception as e:
                self.log.info(
                    f"Could not open media player (session may have expired): {e}. "
                    f"User will see result via persistent notification and comment."
                )

            self.log.info(f"SHA256 task complete for {media_file_id}")

        except Exception as e:
            self.log.error(f"SHA256 calculation failed: {e}", exc_info=True)

    @staticmethod
    def _compute_sha256_sync(file_path: Path, progress_queue: "Queue[float]") -> str:
        """
        Synchronous SHA256 computation (runs in thread pool).

        Args:
            file_path: Path to the file to hash
            progress_queue: Queue to report progress back to the async task

        Returns:
            The computed SHA256 hash as a hex string
        """
        sha256_hash = hashlib.sha256()
        file_size = file_path.stat().st_size
        bytes_processed = 0

        last_report_time = time.monotonic()
        with open(file_path, "rb") as f:
            # Read in 64KB chunks for efficiency
            for chunk in iter(lambda: f.read(65536), b""):
                sha256_hash.update(chunk)
                bytes_processed += len(chunk)

                # Send progress updates every 0.5 seconds
                now = time.monotonic()
                if (now - last_report_time) >= 0.5:
                    progress = min(1.0, bytes_processed / max(file_size, 1))  # 0.0-1.0
                    progress_queue.put(progress)
                    last_report_time = now

        # Final 100% progress update to allow client to clear progress bar
        progress_queue.put(1.0)

        return sha256_hash.hexdigest()

    @override
    async def augment_folder_listing(
        self, listing_items: list[clap.PageItemFolderListingItem], folder_context: FolderContext, session: org.UserSessionData
    ) -> list[clap.PageItemFolderListingItem]:
        """
        Filter the SHA256 action to only show for a certain user.

        This is called when constructing a folder listing page. We add the
        calc_sha256 action only to media files when owner is the target user.

        Args:
            listing_items: List of PageItem.FolderListing.Item protobuf objects
            folder_context: Current folder context
            session: UserSessionData with user info

        Returns:
            Modified listing_items
        """
        # Only add action for the target user
        if session.user.id != self.TARGET_USER_ID:
            return listing_items

        # Add action to all media files in the listing
        for item in listing_items:
            if item.media_file and item.media_file.id:
                # Add our SHA256 action to this media file's popup menu
                if "calc_sha256" not in item.popup_actions:
                    item.popup_actions.append("calc_sha256")

        return listing_items

    @override
    async def augment_listing_data(
        self, listing_data: dict[str, str], folder_context: FolderContext, session: org.UserSessionData
    ) -> dict[str, str]:
        """
        Add custom data to listing_data dictionary.

        This data is passed to JavaScript actions and can be used for
        client-side logic.
        """
        if session.user.id == self.TARGET_USER_ID:
            listing_data["sha256_calculator_enabled"] = "true"

        return listing_data
