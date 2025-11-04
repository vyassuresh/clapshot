from __future__ import annotations
from typing import TYPE_CHECKING

import clapshot_grpc.proto.clapshot.organizer as org
from grpclib import GRPCError
from grpclib.const import Status as GrpcStatus

from organizer.database.models import DbFolder, DbMediaFile
import organizer

if TYPE_CHECKING:
    from organizer import OrganizerInbound


def check_upload_permission(ses: org.UserSessionData) -> bool | None:
    """Check if user has upload permission based on X-Remote-User-Can-Upload header.
    
    Returns:
        True if upload is allowed
        False if upload is denied  
        None if no header found (should fall back to server default)
    """
    # Look for X-Remote-User-Can-Upload header (normalized to lowercase)
    for header_name, header_value in ses.http_headers.items():
        if header_name.lower() in ['x-remote-user-can-upload', 'x_remote_user_can_upload']:
            return header_value.strip().lower() in ['true', '1', 'yes']
    
    # No header found - should fall back to server default
    return None


async def check_action_authorization(
    oi: "OrganizerInbound",
    action: str,
    folder: DbFolder | None = None,
    media_file: DbMediaFile | None = None,
    ses: org.UserSessionData | None = None,
) -> None:
    """
    Check if user is authorized to perform an action on a folder or media file.

    Plugins can override this via metaplugin hooks. If no plugin decides,
    falls back to hardcoded permission checks.

    Args:
        oi: OrganizerInbound instance
        action: Operation name ("rename_folder", "trash_folder", etc.)
        folder: The folder being acted upon
        media_file: The media file being acted upon
        ses: UserSessionData with user info

    Raises:
        GRPCError with PERMISSION_DENIED if unauthorized
    """
    # Try metaplugin hooks first
    plugin_decision = await oi.metaplugin_loader.call_check_action_authorization_hooks(
        action, folder, media_file, ses
    )

    if plugin_decision is True:
        return  # Plugin allows it
    elif plugin_decision is False:
        raise GRPCError(
            GrpcStatus.PERMISSION_DENIED,
            f"Operation '{action}' denied by authorization plugin"
        )

    # No plugin decided, use default checks
    if ses is not None:
        _check_action_authorization_default(action, folder, media_file, ses)


def _check_action_authorization_default(
    action: str,
    folder: DbFolder | None,
    media_file: DbMediaFile | None,
    ses: org.UserSessionData,
) -> None:
    """
    Default permission checks (what was hardcoded before).

    Used when no metaplugin overrides the decision.
    """
    if action == "rename_folder":
        if not folder:
            raise ValueError("rename_folder requires folder argument")
        if folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(
                GrpcStatus.PERMISSION_DENIED,
                "Cannot rename another user's folder"
            )

    elif action == "trash_folder":
        if not folder:
            raise ValueError("trash_folder requires folder argument")
        if folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(
                GrpcStatus.PERMISSION_DENIED,
                "Cannot trash another user's folder"
            )

    elif action == "move_item":
        if not folder:
            raise ValueError("move_item requires folder argument")
        if folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(
                GrpcStatus.PERMISSION_DENIED,
                "Cannot move items to another user's folder"
            )

    elif action == "upload_to_folder":
        if not folder:
            raise ValueError("upload_to_folder requires folder argument")
        if folder.user_id != ses.user.id and not ses.is_admin:
            raise GRPCError(
                GrpcStatus.PERMISSION_DENIED,
                "Cannot upload to another user's folder"
            )

    # Unknown action - allow by default (backward compat)


async def authz_user_action_impl(oi: organizer.OrganizerInbound, authz_user_action_request: org.AuthzUserActionRequest) -> org.AuthzResponse:
    """Check upload authorization based on X-Remote-User-Can-Upload header."""
    ses = authz_user_action_request.ses

    # Handle upload media file authorization
    if (hasattr(authz_user_action_request, 'other_op') and
        authz_user_action_request.other_op and
        authz_user_action_request.other_op.op == org.AuthzUserActionRequestOtherOpOp.UPLOAD_MEDIA_FILE):

        oi.log.debug(f"Upload auth check for '{ses.user.id}'")

        upload_permission = check_upload_permission(ses)
        if upload_permission is True:
            return org.AuthzResponse(is_authorized=True)
        elif upload_permission is False:
            return org.AuthzResponse(
                is_authorized=False,
                message="Upload permission denied",
                details="Your account does not have upload permissions. Contact your administrator.")
        # upload_permission is None - fall back to server default

    # All other operations use server default authorization
    raise GRPCError(GrpcStatus.UNIMPLEMENTED)