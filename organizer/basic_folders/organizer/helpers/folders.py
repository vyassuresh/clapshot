from datetime import datetime
import json
import secrets
from typing import List, Optional, Tuple

from grpclib import GRPCError
from grpclib.const import Status as GrpcStatus

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.orm import Session

import clapshot_grpc.proto.clapshot as clap
import clapshot_grpc.proto.clapshot.organizer as org

from organizer.config import PATH_COOKIE_NAME
from organizer.database.models import DbFolder, DbFolderItems, DbMediaFile, DbSharedFolder, DbUser
from organizer.database.operations import db_get_or_create_user_root_folder
from organizer.helpers import media_type_to_vis_icon

# Cookie name for tracking the shared folder entry point
SHARED_FOLDER_TOKEN_COOKIE_NAME = "shared_folder_token"


class FoldersHelper:
    def __init__(self, db_new_session: orm.sessionmaker, srv: org.OrganizerOutboundStub, log):
        self.db_new_session = db_new_session
        self.srv = srv
        self.log = log

    async def check_shared_folder_access(self, folder_id: int, ses: org.UserSessionData) -> Optional[str]:
        """
        Check if a user has shared access to a folder.
        Returns the share token if the user has access, None otherwise.
        """
        with self.db_new_session() as dbs:
            folder = dbs.query(DbFolder).filter(DbFolder.id == folder_id).one_or_none()
            if not folder:
                return None

            if folder.user_id == ses.user.id or ses.is_admin:
                return None  # User already has natural access

            # Check for shared access via cookie
            share_token = ses.cookies.get(SHARED_FOLDER_TOKEN_COOKIE_NAME)
            if not share_token:
                return None

            # Verify token is valid
            shared_folder = dbs.query(DbSharedFolder).filter(DbSharedFolder.share_token == share_token).one_or_none()
            if not shared_folder:
                # Invalid share, clear the cookie
                ses.cookies.pop(SHARED_FOLDER_TOKEN_COOKIE_NAME, None)
                await self.srv.client_set_cookies(org.ClientSetCookiesRequest(
                    cookies=ses.cookies,
                    sid=ses.sid
                ))
                return None

            # Check if the requested folder is within the shared subtree
            if await self.is_folder_in_subtree(dbs, folder_id, shared_folder.folder_id):
                return share_token

        return None

    async def is_folder_in_subtree(self, dbs: Session, folder_id: int, root_id: int) -> bool:
        """
        Check if a folder is within a subtree rooted at root_id.
        Uses breadth-first search to find the path.
        """
        if folder_id == root_id:
            return True

        # BFS to find if folder_id is in the subtree of root_id
        queue = [root_id]
        visited = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue

            visited.add(current)

            # Get all subfolders of the current folder
            subfolder_items = dbs.query(DbFolderItems).filter(
                DbFolderItems.folder_id == current,
                DbFolderItems.subfolder_id != None      # noqa: E711
            ).all()

            subfolder_ids = [item.subfolder_id for item in subfolder_items if item.subfolder_id is not None]
            if folder_id in subfolder_ids:
                return True

            queue.extend(subfolder_ids)

        return False

    async def get_folder_ancestors(self, dbs: Session, folder_id: int) -> List[int]:
        """
        Get the ancestor folders of a given folder, ordered from root to the given folder.
        """
        ancestors: List[int] = []
        current_id: int|None = folder_id
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            ancestors.insert(0, current_id)

            # Find parent of current folder
            parent_item = dbs.query(DbFolderItems).filter(
                DbFolderItems.subfolder_id == current_id
            ).one_or_none()

            if not parent_item:
                break

            current_id = parent_item.folder_id

        return ancestors

    async def generate_share_token(self) -> str:
        """Generate a secure random token for folder sharing"""
        return secrets.token_urlsafe(32)

    async def get_current_folder_path(self, ses: org.UserSessionData, cookie_override: Optional[str]=None) -> Tuple[List[DbFolder], DbFolder]:
        """
        Get current folder path from cookies & DB.

        Returns a homogeneous ownership path - either all user-owned OR all shared-accessible.
        Never mixes different ownership contexts in breadcrumbs.

        If cookie_override is set, it will be used instead of the cookie from session.
        """
        ck = ses.cookies or {}
        with self.db_new_session() as dbs:
            user_root_folder = await db_get_or_create_user_root_folder(dbs, ses.user, self.srv, self.log)
            try:
                if folder_ids := json.loads((cookie_override or ck.get(PATH_COOKIE_NAME)) or '[]'):
                    assert all(isinstance(i, int) for i in folder_ids), "Folder path cookie contains non-integer IDs"

                    folders_unordered = dbs.query(DbFolder).filter(DbFolder.id.in_(folder_ids)).all()
                    if len(folders_unordered) == len(folder_ids):
                        # Reorder the the retrieved DB objects to match the order in the cookie
                        folders_by_id = {f.id: f for f in folders_unordered}
                        path_folders = [folders_by_id[id] for id in folder_ids if id in folders_by_id]

                        # Make sure all folders in the path are owned by the same user
                        # (otherwise it's an error state)
                        if path_folders and all(f.user_id == path_folders[0].user_id for f in path_folders):

                            # If not shared folders, make sure the first folder is the user's root
                            # (add it if necessary)
                            if path_folders[0].user_id == ses.user.id and user_root_folder.id not in folder_ids:
                                self.log.debug(f"User's root folder ({user_root_folder.id}) not in path, adding it to the start.")
                                path_folders.insert(0, user_root_folder)

                            return path_folders, path_folders[-1]
                        else:
                            self.log.warning("Mixed ownership in folder path. Clearing path cookie.")
                            await self._clear_path_cookie(ses)
                    else:
                        self.log.warning("Some unknown folder IDs in folder_path cookie. Clearing it.")
                        await self._clear_path_cookie(ses)

                self.log.debug("No valid folder_path cookie found. Returning user root folder.")

            except json.JSONDecodeError as e:
                self.log.error(f"Failed to parse folder_path cookie: {e}. Falling back to user root.")

            # Show user's root as a fallback
            return [user_root_folder], user_root_folder


    async def _clear_path_cookie(self, ses: org.UserSessionData):
        """Clear path cookie and send update to client."""
        ses.cookies.pop(PATH_COOKIE_NAME, None)
        await self.srv.client_set_cookies(org.ClientSetCookiesRequest(cookies=ses.cookies, sid=ses.sid))

    async def fetch_folder_contents(self, folder: DbFolder, ses: org.UserSessionData) -> List[DbMediaFile | DbFolder]:
        """
        Fetch the contents of a folder from the database, sorted by the specified criteria.
        """
        # Check for natural access first (ownership or admin)
        has_natural_access = folder.user_id == ses.user.id or ses.is_admin

        # If no natural access, check for shared access
        if not has_natural_access:
            if not await self.check_shared_folder_access(folder.id, ses):
                raise GRPCError(GrpcStatus.PERMISSION_DENIED, "Cannot fetch contents of another user's folder")

        with self.db_new_session() as dbs:
            folder_items = dbs.query(DbFolderItems).filter(DbFolderItems.folder_id == folder.id).all()

            # Get DbFolder and DbMediaFile objects for all folder items
            subfolder_ids = [fi.subfolder_id for fi in folder_items if fi.subfolder_id]
            subfolder_items = dbs.query(DbFolder).filter(DbFolder.id.in_(subfolder_ids)).all()
            subfolders_by_id = {f.id: f for f in subfolder_items}

            media_ids = [fi.media_file_id for fi in folder_items if fi.media_file_id]
            media_items = dbs.query(DbMediaFile).filter(DbMediaFile.id.in_(media_ids)).all()
            media_by_id = {v.id: v for v in media_items}

            # Replace folder item IDs with actual objects and their sort_order
            def _get_item(fi: DbFolderItems) -> Tuple[int, DbMediaFile | DbFolder]:
                if fi.media_file_id:
                    return (fi.sort_order, media_by_id[fi.media_file_id])
                elif fi.subfolder_id:
                    return (fi.sort_order, subfolders_by_id[fi.subfolder_id])
                else:
                    raise ValueError("Folder item has neither media file nor subfolder ID")

            items_with_sort_order = [_get_item(fi) for fi in folder_items]

            # Sort by sort_order first, then by type, and then by .created or .added_time (newest first)
            sorted_items = sorted(items_with_sort_order, key=lambda x: (
                x[0],
                isinstance(x[1], DbMediaFile),
                -(getattr(x[1], 'added_time', getattr(x[1], 'created', datetime(1970, 1, 1))).timestamp())
            ))

            # Extract the sorted objects
            res = [item[1] for item in sorted_items]

            return res

    async def trash_folder_recursive(self, dbs: Session, folder_id: int, ses: org.UserSessionData) -> List[str]:
        """
        Trash a folder and unbind its contents recursively.
        Returns a list of all media file IDs that are to be deleted.
        """
        fld = dbs.query(DbFolder).filter(DbFolder.id == folder_id).one_or_none()
        if not fld:
            raise GRPCError(GrpcStatus.NOT_FOUND, f"Folder ID '{folder_id}' not found")

        # Only allow trashing by the owner or an admin
        if fld.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(GrpcStatus.PERMISSION_DENIED, "Cannot trash another user's folder")

        # Check if the folder is currently being shared
        shared = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == folder_id).first()
        if shared:
            # First delete the share
            dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == folder_id).delete()

        folder_items = dbs.query(DbFolderItems).filter(DbFolderItems.folder_id == folder_id).all()
        media_ids = [it.media_file_id for it in folder_items if it.media_file_id]

        self.log.debug(f"Deleting folder '{folder_id}' ('{fld.title}') and its contents")

        # Recurse to subfolders
        for fi in [it.subfolder_id for it in folder_items if it.subfolder_id]:
            media_ids.extend(await self.trash_folder_recursive(dbs, fi, ses))

        # Remove content bindings
        dbs.query(DbFolderItems).filter(DbFolderItems.folder_id == folder_id).delete()

        # Delete the folder itself
        dbs.query(DbFolder).filter(DbFolder.id == folder_id).delete()
        return media_ids

    async def folder_to_page_item(self, fld: DbFolder, popup_actions: List[str], ses: org.UserSessionData) -> clap.PageItemFolderListingItem:
        """
        Convert a folder node to a page item.
        """
        pv_items = await self.preview_items_for_folder(fld, ses)

        # Determine if this folder is shared
        is_shared = False
        with self.db_new_session() as dbs:
            is_shared = await self.is_folder_shared(dbs, fld.id)

        # Determine if the user is the owner or admin
        is_owner = (fld.user_id == ses.user.id) or ses.is_admin

        # Add visual indicator for shared folders
        title = fld.title or "<UNNAMED>"
        if is_shared:
            title = f"🔗 {title}"  # Add link icon before title to indicate shared folder

        # Customize popup actions based on ownership and sharing status
        folder_actions = popup_actions.copy()
        if is_owner:
            if is_shared:
                folder_actions.append("revoke_share")
                folder_actions.append("copy_shared_link")
            else:
                folder_actions.append("share_folder")

        return clap.PageItemFolderListingItem(
            folder = clap.PageItemFolderListingFolder(
                id = str(fld.id),
                title = title,
                preview_items = pv_items),
            open_action = clap.ScriptCall(
                lang = clap.ScriptCallLang.JAVASCRIPT,
                code = f'clapshot.callOrganizer("open_folder", {{id: {fld.id}}});'),
            popup_actions = folder_actions)

    async def preview_items_for_folder(self, fld: DbFolder, ses: org.UserSessionData) -> List[clap.PageItemFolderListingItem]:
        """
        Get preview items for a folder.
        Used in folder listings to show a preview of the folder contents (contained media files and subfolders).
        """
        contained_items = await self.fetch_folder_contents(fld, ses)

        media_files = [item for item in contained_items if isinstance(item, DbMediaFile)][:4]
        folders = [item for item in contained_items if isinstance(item, DbFolder)][:4]

        media_by_id = {}
        if media_files:
            media_details = await self.srv.db_get_media_files(org.DbGetMediaFilesRequest(ids=org.IdList(ids=[v.id for v in media_files])))
            media_by_id = {v.id: v for v in media_details.items}

        # Prepare result list with up to 4 items, prioritizing media files
        result = [
            clap.PageItemFolderListingItem(
                media_file=media_by_id[v.id],
                vis=media_type_to_vis_icon(media_by_id[v.id].media_type))
            for v in media_files
        ] + [
            clap.PageItemFolderListingItem(
                folder=clap.PageItemFolderListingFolder(
                    id=str(f.id), title=f.title or "???")
            )
            for f in folders[: 4 - len(media_files)]
        ]

        return result

    async def create_folder(self, dbs: Session, ses: org.UserSessionData, parent_folder: DbFolder, new_folder_name: str) -> DbFolder:
        """
        Create a new folder in the parent folder.
        """
        assert parent_folder is not None, "Cannot create root folders with this function"

        # Only allow folder creation by owner or admin
        if parent_folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(GrpcStatus.PERMISSION_DENIED, "Cannot create folder in another user's folder")
        if len(new_folder_name) > 255:
            raise GRPCError(GrpcStatus.INVALID_ARGUMENT, "Folder name too long")
        if not new_folder_name:
            GRPCError(GrpcStatus.INVALID_ARGUMENT, "Folder name cannot be empty")

        if new_folder_name in [f.title for f in await self.fetch_folder_contents(parent_folder, ses)]:
            raise GRPCError(GrpcStatus.ALREADY_EXISTS, "Item with this name already exists in the this folder")

        with dbs.begin_nested():
            # Create the new folder
            new_folder = DbFolder(user_id=parent_folder.user_id, title=new_folder_name)
            dbs.add(new_folder)
            dbs.flush()

            # Add it at the end of the parent folder
            max_sort_order = dbs.query(sqlalchemy.func.max(DbFolderItems.sort_order)).filter(DbFolderItems.folder_id == parent_folder.id).scalar() or 0
            dbs.add(DbFolderItems(folder_id=parent_folder.id, subfolder_id=new_folder.id, sort_order=max_sort_order+1))
            return new_folder

    async def get_folder_owner(self, dbs: Session, folder_id: int) -> Optional[DbUser]:
        """
        Get the owner of a folder by its ID.
        Returns the DbUser object if found, None otherwise.
        """
        folder = dbs.query(DbFolder).filter(DbFolder.id == folder_id).one_or_none()
        if not folder:
            return None
        return dbs.query(DbUser).filter(DbUser.id == folder.user_id).one_or_none()

    async def create_folder_share(self, dbs: Session, folder_id: int, ses: org.UserSessionData) -> DbSharedFolder:
        """
        Create a shareable link for a folder.
        Returns the created DbSharedFolder object.
        """
        # Check if folder exists
        folder = dbs.query(DbFolder).filter(DbFolder.id == folder_id).one_or_none()
        if not folder:
            raise GRPCError(GrpcStatus.NOT_FOUND, f"Folder ID '{folder_id}' not found")

        # Only folder owner or admin can create a share
        if folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(GrpcStatus.PERMISSION_DENIED, "Only the folder owner can create shares")

        # Check if sharing already exists
        existing_share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == folder_id).one_or_none()
        if existing_share:
            return existing_share

        # Generate a secure token
        share_token = await self.generate_share_token()

        # Create the share
        share = DbSharedFolder(
            folder_id=folder_id,
            share_token=share_token
        )

        dbs.add(share)
        dbs.flush()

        return share

    async def revoke_folder_share(self, dbs: Session, folder_id: int, ses: org.UserSessionData) -> bool:
        """
        Revoke sharing for a folder.
        Returns True if a share was revoked, False if no share existed.
        """
        # Check if folder exists
        folder = dbs.query(DbFolder).filter(DbFolder.id == folder_id).one_or_none()
        if not folder:
            raise GRPCError(GrpcStatus.NOT_FOUND, f"Folder ID '{folder_id}' not found")

        # Only folder owner or admin can revoke sharing
        if folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(GrpcStatus.PERMISSION_DENIED, "Only the folder owner can revoke sharing")

        # Delete the share
        deleted = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == folder_id).delete()

        return deleted > 0

    async def get_share_by_token(self, dbs: Session, token: str) -> Optional[DbSharedFolder]:
        """
        Get a shared folder by its token.
        Returns the DbSharedFolder object if found, None otherwise.
        """
        return dbs.query(DbSharedFolder).filter(DbSharedFolder.share_token == token).one_or_none()


    async def is_folder_shared(self, dbs: Session, folder_id: int) -> bool:
        """
        Check if a folder is currently shared.
        """
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == folder_id).one_or_none()
        return share is not None

    async def get_shared_folder_breadcrumb_info(self, ses: org.UserSessionData, folder_path: List[DbFolder]) -> Optional[Tuple[str, str]]:
        """
        Get shared folder breadcrumb information if the user is viewing a shared folder.

        Returns:
            Optional tuple of (folder_title, owner_name) if viewing a shared folder, None otherwise
        """
        share_token = ses.cookies.get(SHARED_FOLDER_TOKEN_COOKIE_NAME)
        if not share_token or not folder_path:
            return None

        # Check if current user has natural access to the folder
        current_folder = folder_path[0]
        if current_folder.user_id == ses.user.id or ses.is_admin:
            return None  # User owns the folder => "natural access" (not accessing via share link)

        with self.db_new_session() as dbs:
            shared_folder = dbs.query(DbSharedFolder).filter(DbSharedFolder.share_token == share_token).one_or_none()
            if not shared_folder:
                return None

            # Check if we're in the shared folder's subtree
            if current_folder.id != shared_folder.folder_id and not await self.is_folder_in_subtree(dbs, current_folder.id, shared_folder.folder_id):
                return None

            owner = await self.get_folder_owner(dbs, shared_folder.folder_id)
            if not owner:
                return None # Owner not found, cannot provide breadcrumb info

            folder_title = current_folder.title or "UNNAMED"
            owner_name = owner.name or owner.id
            return (folder_title, owner_name)

    def get_shared_folder_tokens(self, folder_items: List[DbFolder]) -> dict[str, str]:
        """
        Get share tokens for a list of folders.

        Args:
            folder_items: List of folder objects to check for shares

        Returns:
            Dictionary mapping folder_id (as string) to share_token
        """
        shared_folder_tokens: dict[str, str] = {}
        if not folder_items:
            return shared_folder_tokens

        with self.db_new_session() as dbs:
            for folder_item in folder_items:
                share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == folder_item.id).one_or_none()
                if share:
                    shared_folder_tokens[str(folder_item.id)] = share.share_token

        return shared_folder_tokens
