from __future__ import annotations

import asyncio
import json
import sys
import inspect
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import traceback
from types import MethodType
from typing import Tuple

from grpclib import GRPCError
from grpclib.const import Status as GrpcStatus
import clapshot_grpc.proto.clapshot.organizer as org
import clapshot_grpc.proto.clapshot as clap

import organizer
from organizer.config import PATH_COOKIE_NAME
from organizer.database.models import DbFolder, DbUser, DbMediaFile, DbSharedFolder
from organizer.database.operations import db_get_or_create_user_root_folder
from organizer.helpers.folders import SHARED_FOLDER_TOKEN_COOKIE_NAME


async def list_tests_impl(oi: organizer.OrganizerInbound) -> org.ListTestsResponse:
    """
    Organizer method (gRPC/protobuf)

    Called by the server to list all available unit/integrations tests in this plugin.
    => Uses `inspection` to find all functions in this module that start with 'org_test_'.
    """
    oi.log.info("list_tests")
    current_module = sys.modules[__name__]
    test_names = sorted([
        func_name for func_name, func in inspect.getmembers(current_module, inspect.isfunction)
        if func_name.startswith('org_test_')
    ])
    return org.ListTestsResponse(test_names=test_names)


async def run_test_impl(oi, request: org.RunTestRequest) -> org.RunTestResponse:
    """
    Organizer method (gRPC/protobuf)

    Called by the server to run a single unit/integration test in this plugin, by name, no arguments.
    => Uses `inspection` to find the test function by name and execute it with `redirect_stdout` to capture output.
    """
    oi.log.info(f"Running test: {request.test_name}")
    test_name = request.test_name
    current_module = sys.modules[__name__]

    # Attempt to get the test function by name
    test_func = getattr(current_module, test_name, None)
    if test_func is None or not test_name.startswith('org_test_'):
        oi.log.error(f"Test function {test_name} not found or is invalid.")
        return org.RunTestResponse(output="", error=f"Test function {test_name} not found or is invalid.")

    # Set up StringIO stream to capture output
    buffer = StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            # Execute the test function
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func(oi)
            else:
                result = test_func(oi)
            # Capture additional return value if needed
            output = buffer.getvalue()
            if result is not None:
                output += f"\nReturn: {result}"
    except Exception as e:
        oi.log.error(f"Error running {test_name}: {str(e)}")
        error_with_traceback = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        return org.RunTestResponse(output=buffer.getvalue(), error=str(error_with_traceback))

    # Successful execution with output captured
    return org.RunTestResponse(output=output, error=None)


# ---------------------------- Test functions --------------------------------
# These are the test functions that the server will call when running tests.
#
# At that point, handshake and database migrations have already been done,
# so these tests can assume that the temporary test database is ready for use.
# ----------------------------------------------------------------------------

# TODO: Refactor @overridden OrganizerInbound methods into smaller parts that _return_ any client messages insted of sending them directly, and assert them in the test functions.


async def org_test__start_user_session(oi: organizer.OrganizerInbound):
    """
    on_start_user_session() -- Just a simple test to check if the method doesn't crash.
    """
    user = oi.db_new_session().query(DbUser).first()
    res = await organizer.on_start_user_session_impl(oi, org.OnStartUserSessionRequest(
        org.UserSessionData(
            sid="test_sid",user=clap.UserInfo(id=user.id, name=user.name),
            is_admin=False, cookies={})
    ))
    assert res == org.OnStartUserSessionResponse()


async def org_test__navigate_page(oi: organizer.OrganizerInbound):
    """
    navigate_page() -- Test that it returns a valid ClientShowPageRequest.
    """
    user = oi.db_new_session().query(DbUser).first()
    res = await organizer.navigate_page_impl(oi, org.NavigatePageRequest(
        ses=org.UserSessionData(sid="test_sid",user=clap.UserInfo(id=user.id, name=user.name), cookies={})
    ))
    assert isinstance(res, org.ClientShowPageRequest)
    assert len(res.page_items) > 0


async def org_test__authz_user_action(oi: organizer.OrganizerInbound):
    """
    authz_user_action() -- Should return UNIMPLEMENTED.
    """
    try:
        await oi.authz_user_action(org.AuthzUserActionRequest())
        assert False, "Expected GRPCError"
    except GRPCError as e:
        assert e.status == GrpcStatus.UNIMPLEMENTED


async def org_test__move_to_folder(oi: organizer.OrganizerInbound):
    """
    move_to_folder() -- Test several scenarios of moving folders and media files between folders.
    """
    media_files = await oi.srv.db_get_media_files(org.DbGetMediaFilesRequest(all=clap.Empty()))
    assert len(media_files.items) > 0, "No media files found in the test database"

    users_file = media_files.items[0]
    (user_id, user_name) = (users_file.user_id, f"Name of {users_file.user_id}")

    ses = org.UserSessionData(sid="test_sid",user=clap.UserInfo(id=user_id, name=user_name), is_admin=False, cookies={})

    # Get folder path. This should actually create the root folder, since the test database is empty.
    fld_path, _root_folder_id = await oi.folders_helper.get_current_folder_path(ses)
    assert len(fld_path) > 0, "Folder path should always contain at least the root folder"
    root_fld = fld_path[0]

    # Organizer should have moved all orphan media files to the root folder, so the user's media file should be there now
    root_cont = await oi.folders_helper.fetch_folder_contents(root_fld, ses)
    assert any(v.id == users_file.id for v in root_cont), "Video should have been auto-moved to the root folder"

    # 1) First, try to move someone else's media file to the root folder (should fail)
    someone_elses_file = [v for v in media_files.items if v.user_id != user_id][0]
    assert someone_elses_file.user_id
    oi.log.info(f"Trying to move someone else's ({someone_elses_file.user_id}) file ({someone_elses_file.id}) to the root folder ({root_fld.id}) of current user ({user_id})")
    try:
        await organizer.move_to_folder_impl(oi, org.MoveToFolderRequest(
            ses,
            ids=[clap.FolderItemId(media_file_id=someone_elses_file.id)],
            dst_folder_id=str(root_fld.id),
            listing_data={}))
        assert False, "Expected GRPCError (permission denied)"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED

    root_cont = await oi.folders_helper.fetch_folder_contents(root_fld, ses)
    assert any(v.id == users_file.id for v in root_cont), "Video should still be in the root folder"

    # 2) Next, create a new subfolder and move the file there. This should succeed.
    subfld = await oi.folders_helper.create_folder(oi.db_new_session(), ses, root_fld, "Test Subfolder")

    oi.log.info(f"Moving user's ({user_id}) file ({users_file.id}) to the subfolder ({subfld.id})")
    await oi.move_to_folder(org.MoveToFolderRequest(
        ses,
        ids=[clap.FolderItemId(media_file_id=users_file.id)],
        dst_folder_id=str(subfld.id),
        listing_data={}))

    root_cont = await oi.folders_helper.fetch_folder_contents(root_fld, ses)
    assert not any(v.id == users_file.id for v in root_cont), "Video not be in the root folder anymore"
    subfld_cont = await oi.folders_helper.fetch_folder_contents(subfld, ses)
    assert any(v.id == users_file.id for v in subfld_cont), "Video should be in the subfolder now"



async def org_test__reorder_items(oi: organizer.OrganizerInbound):
    """
    reorder_items() -- Test reordering of folders and files within a folder in various ways.
    """
    # Fetch a file from the test database, and get the user ID
    media_files = await oi.srv.db_get_media_files(org.DbGetMediaFilesRequest(all=clap.Empty()))
    assert len(media_files.items) > 0, "No media files found in the test database"
    users_file = media_files.items[0]
    (user_id, user_name) = (users_file.user_id, f"Name of {users_file.user_id}")

    ses = org.UserSessionData(sid="test_sid",user=clap.UserInfo(id=user_id, name=user_name), is_admin=False, cookies={})

    # Get folder path (+ create root folder + move orphan media files to root folder)
    fld_path, _root_folder_id = await oi.folders_helper.get_current_folder_path(ses)
    assert len(fld_path) > 0, "Folder path should always contain at least the root folder"
    root_fld = fld_path[0]

    # Create two folders for testing in the root
    subfld1 = await oi.folders_helper.create_folder(oi.db_new_session(), ses, root_fld, "Test Subfolder 1")
    subfld2 = await oi.folders_helper.create_folder(oi.db_new_session(), ses, root_fld, "Test Subfolder 2")

    # Move any other media files to subfld1, so they don't interfere with the reorder test.
    other_files = [v for v in media_files.items if v.id != users_file.id and v.user_id == user_id]
    for v in other_files:
        await oi.move_to_folder(org.MoveToFolderRequest(
            ses,
            ids=[clap.FolderItemId(media_file_id=v.id)],
            dst_folder_id=str(subfld1.id),
            listing_data={}))

    # Also create a third folder, but this time inside subfld2. This shouldn't affect the reorder test.
    await oi.folders_helper.create_folder(oi.db_new_session(), ses, subfld2, "Test Subsubfolder")

    test_orders: list[list[DbFolder | clap.MediaFile]] = [
        [subfld1, subfld2, users_file],
        [users_file, subfld2, subfld1],
        [subfld2, users_file, subfld1],
    ]
    for i, new_obj_order in enumerate(test_orders):
        new_order = [clap.FolderItemId(folder_id=str(fi.id))
                     if isinstance(fi, DbFolder)
                     else clap.FolderItemId(media_file_id=fi.id) for fi in new_obj_order]
        await oi.reorder_items(org.ReorderItemsRequest(ses, ids=new_order, listing_data={"folder_id": str(root_fld.id)}))
        cont = [fi.id for fi in await oi.folders_helper.fetch_folder_contents(root_fld, ses)]
        new_order_ids = [fi.id for fi in new_obj_order]
        print(f"Test #{i+1}", "Expecting:", new_order_ids, "Got:", cont)
        assert cont == new_order_ids, f"Wrong order after reorder #{i+1}"


async def _create_test_folder_and_session(oi: organizer.OrganizerInbound) -> Tuple[org.UserSessionData, DbFolder]:
    """
    Helper for the org_test__cmd_from_client__* tests.
    """
    with oi.db_new_session() as dbs:
        user_id, user_name = "cmdfromclient.test_user", "Cmdfromclient Test User"
        dbs.add(DbUser(id=user_id, name=user_name))
        dbs.commit()

    with oi.db_new_session() as dbs:
        ses = org.UserSessionData(sid="test_sid",user=clap.UserInfo(id=user_id, name=user_name), is_admin=False, cookies={})

        # Check that the user has no folders yet (including root folder)
        flds = dbs.query(DbFolder).filter(DbFolder.user_id == user_id).all()
        assert len(flds) == 0, "User should have no folders yet"

        # Get/create the root folder for the user
        flds, _root_folder_id = await oi.folders_helper.get_current_folder_path(ses)
        assert len(flds) == 1, "User should now have a root folder"
        root_fld = flds[0]

        return ses, root_fld


async def org_test__cmd_from_client__new_folder(oi: organizer.OrganizerInbound):
    """
    cmd_from_client() -- Test the 'new_folder' client command.
    """
    ses, root_fld = await _create_test_folder_and_session(oi)

    # Send the 'new_folder' client command
    ses.cookies[PATH_COOKIE_NAME] = json.dumps([root_fld.id])
    await oi.cmd_from_client(org.CmdFromClientRequest(ses=ses,
        cmd="new_folder",
        args='{"name": "Test Folder"}'))

    # Check that the new folder was created
    cont = await oi.folders_helper.fetch_folder_contents(root_fld, ses)
    print("Folder contents:", cont)
    flds = [fi for fi in cont if isinstance(fi, DbFolder)]
    assert len(flds) == 1, "Root folder should have one subfolder now"
    assert flds[0].title == "Test Folder"


async def org_test__cmd_from_client__open_folder(oi: organizer.OrganizerInbound):
    """
    cmd_from_client() -- Test that 'open_folder' client command sets the folder path cookie correctly.
    """
    orig_set_cookies = oi.srv.client_set_cookies
    try:
        ses, root_fld = await _create_test_folder_and_session(oi)
        new_fld = await oi.folders_helper.create_folder(oi.db_new_session(), ses, root_fld, "Test Folder")
        expected_path = [root_fld.id, new_fld.id]

        # Mock the client_set_cookies method to check the cookie value
        async def mock_set_cookies(self, req: org.ClientSetCookiesRequest) -> clap.Empty:
            nonlocal ses
            ses.cookies = req.cookies
            assert req.cookies[PATH_COOKIE_NAME] == json.dumps(expected_path)
            return await orig_set_cookies(req)

        setattr(oi.srv, "client_set_cookies", MethodType(mock_set_cookies, oi.srv))

        # Send the 'open_folder' client command
        ses.cookies[PATH_COOKIE_NAME] = json.dumps([root_fld.id])
        await oi.cmd_from_client(org.CmdFromClientRequest(ses=ses,
            cmd="open_folder",
            args=json.dumps({"id": new_fld.id})))

        # Check that the new folder was opened
        flds, _root_folder_id = await oi.folders_helper.get_current_folder_path(ses)
        path_got = [f.id for f in flds]
        print("Folder path that was set:", path_got, "Expected:", expected_path)
        assert path_got == expected_path, "Folder path should have been updated"

    finally:
        # Restore the original method
        setattr(oi.srv, "client_set_cookies", orig_set_cookies)


async def org_test__cmd_from_client__rename_folder(oi: organizer.OrganizerInbound):
    """
    cmd_from_client() -- Test the 'rename_folder' client command against database.
    """
    ses, root_fld = await _create_test_folder_and_session(oi)
    new_fld = await oi.folders_helper.create_folder(oi.db_new_session(), ses, root_fld, "Test Folder")

    # Send the 'rename_folder' client command
    await oi.cmd_from_client(org.CmdFromClientRequest(ses=ses,
        cmd="rename_folder",
        args=json.dumps({"id": new_fld.id, "new_name": "Test Folder New Name"})))

    # Check that the folder was renamed
    cont = await oi.folders_helper.fetch_folder_contents(root_fld, ses)
    flds = [fi for fi in cont if isinstance(fi, DbFolder)]
    assert len(flds) == 1
    assert flds[0].title == "Test Folder New Name"


async def org_test__cmd_from_client__trash_folder(oi: organizer.OrganizerInbound):
    """
    cmd_from_client() -- Test the 'trash_folder' client command against database.
    """
    ses, root_fld = await _create_test_folder_and_session(oi)
    new_fld = await oi.folders_helper.create_folder(oi.db_new_session(), ses, root_fld, "Test Folder")

    # Send the 'trash_folder' client command
    await oi.cmd_from_client(org.CmdFromClientRequest(ses=ses,
        cmd="trash_folder",
        args=json.dumps({"id": new_fld.id})))

    # Check that the folder was trashed
    cont = await oi.folders_helper.fetch_folder_contents(root_fld, ses)
    flds = [fi for fi in cont if isinstance(fi, DbFolder)]
    assert len(flds) == 0, "Folder should have been deleted"


async def org_test__cmd_from_client__share_folder(oi: organizer.OrganizerInbound):
    """
    cmd_from_client() -- Test the 'share_folder' client command.
    """
    # Create test session and folder
    ses, root_fld = await _create_test_folder_and_session(oi)

    # Create a subfolder to share
    with oi.db_new_session() as dbs:
        subfolder = await oi.folders_helper.create_folder(dbs, ses, root_fld, "Shared Folder")
        subfolder_id = subfolder.id
        dbs.commit()

    # Mock the client_show_user_message method to verify sharing confirmation message
    orig_show_message = oi.srv.client_show_user_message
    try:
        message_received = False

        async def mock_show_message(req: org.ClientShowUserMessageRequest) -> clap.Empty:
            nonlocal message_received
            if req.msg and "Folder shared" in req.msg.message:
                message_received = True
            return await orig_show_message(req)

        setattr(oi.srv, "client_show_user_message", mock_show_message)

        # Set server_info.url_base for URL generation
        oi.server_info = oi.server_info or org.ServerInfo()
        oi.server_info.url_base = "http://test.example.com"

        # Execute share command
        await oi.cmd_from_client(org.CmdFromClientRequest(
            ses=ses,
            cmd="share_folder",
            args=json.dumps({"id": subfolder_id})
        ))

        # Verify share was created in database
        with oi.db_new_session() as dbs:
            share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == subfolder_id).one_or_none()
            assert share is not None, "Share should have been created"
            # Verify owner through folder ownership
            owner = await oi.folders_helper.get_folder_owner(dbs, subfolder_id)
            assert owner and owner.id == ses.user.id, "Share should be owned by correct user"
            assert message_received, "Share confirmation message should have been sent"

    finally:
        # Restore original method
        setattr(oi.srv, "client_show_user_message", orig_show_message)


async def org_test__cmd_from_client__revoke_share(oi: organizer.OrganizerInbound):
    """
    cmd_from_client() -- Test the 'revoke_share' client command.
    """
    # Create test session and folder
    ses, root_fld = await _create_test_folder_and_session(oi)

    # Create a subfolder
    with oi.db_new_session() as dbs:
        subfolder = await oi.folders_helper.create_folder(dbs, ses, root_fld, "Shared Folder")
        subfolder_id = subfolder.id
        dbs.commit()

    # Create share manually
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=subfolder_id,
            share_token=share_token
        )
        dbs.add(share)
        dbs.commit()

        # Verify share exists
        assert dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == subfolder_id).one_or_none() is not None

    # Execute revoke command
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=ses,
        cmd="revoke_share",
        args=json.dumps({"id": subfolder_id})
    ))

    # Verify share was removed
    with oi.db_new_session() as dbs:
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == subfolder_id).one_or_none()
        assert share is None, "Share should have been removed"


async def org_test__navigate_shared_url(oi: organizer.OrganizerInbound):
    """
    Test navigating to a shared folder URL.
    """
    # Create owner session and folder
    owner_ses, root_fld = await _create_test_folder_and_session(oi)

    # Create a subfolder with some content
    subfolder = await oi.folders_helper.create_folder(oi.db_new_session(), owner_ses, root_fld, "Shared Folder")
    inner_folder = await oi.folders_helper.create_folder(oi.db_new_session(), owner_ses, subfolder, "Inner Folder")

    # Create share for subfolder
    share_token = None
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=subfolder.id,
            share_token=share_token
        )
        dbs.add(share)
        dbs.commit()

    # Create second user
    with oi.db_new_session() as dbs:
        recipient_user_id = "share.recipient"
        dbs.add(DbUser(id=recipient_user_id, name="Share Recipient"))
        dbs.commit()

    recipient_ses = org.UserSessionData(
        sid="recipient_sid",
        user=clap.UserInfo(id=recipient_user_id, name="Share Recipient"),
        is_admin=False,
        cookies={}
    )

    # Navigate to the shared URL
    result = await organizer.navigate_page_impl(oi, org.NavigatePageRequest(
        ses=recipient_ses,
        page_id=f"shared.{share_token}"
    ))

    # Verify response contains the shared folder content
    assert isinstance(result, org.ClientShowPageRequest), "Result should be a ClientShowPageRequest"

    # Check if SHARED_FOLDER_ENTRY_COOKIE is set in recipient's session
    assert SHARED_FOLDER_TOKEN_COOKIE_NAME in recipient_ses.cookies, "Shared folder entry cookie should be set"
    assert recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] == share_token, "Cookie should contain share token"

    # Verify recipient can fetch contents via cookie
    folder_contents = await oi.folders_helper.fetch_folder_contents(subfolder, recipient_ses)
    assert len(folder_contents) == 1, "Should have access to inner folder"
    assert folder_contents[0].id == inner_folder.id, "Should see the inner folder"


async def org_test__shared_folder_permissions(oi: organizer.OrganizerInbound):
    """
    Test permission boundaries for shared folders.
    """
    # Create owner session and folder hierarchy
    owner_ses, root_fld = await _create_test_folder_and_session(oi)
    owner_subfolder = await oi.folders_helper.create_folder(oi.db_new_session(), owner_ses, root_fld, "Shared Folder")
    owner_inner = await oi.folders_helper.create_folder(oi.db_new_session(), owner_ses, owner_subfolder, "Inner Folder")

    # Create another folder outside the shared path
    owner_other = await oi.folders_helper.create_folder(oi.db_new_session(), owner_ses, root_fld, "Not Shared")

    # Create share for owner_subfolder
    share_token = None
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=owner_subfolder.id,
            share_token=share_token
        )
        dbs.add(share)
        dbs.commit()

    # Create recipient user
    with oi.db_new_session() as dbs:
        recipient_user_id = "perm.recipient"
        dbs.add(DbUser(id=recipient_user_id, name="Permission Test Recipient"))
        dbs.commit()

    recipient_ses = org.UserSessionData(
        sid="recipient_sid",
        user=clap.UserInfo(id=recipient_user_id, name="Permission Test Recipient"),
        is_admin=False,
        cookies={}
    )

    # Set up the shared folder session
    recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] = share_token

    # Test 1: Recipient can access shared folder and its subfolder
    assert await oi.folders_helper.check_shared_folder_access(owner_subfolder.id, recipient_ses) is not None
    assert await oi.folders_helper.check_shared_folder_access(owner_inner.id, recipient_ses) is not None

    # Test 2: Recipient cannot access folders outside shared subtree
    assert await oi.folders_helper.check_shared_folder_access(owner_other.id, recipient_ses) is None
    assert await oi.folders_helper.check_shared_folder_access(root_fld.id, recipient_ses) is None

    # Test 3: Recipient cannot modify shared folder
    try:
        with oi.db_new_session() as dbs:
            await oi.folders_helper.create_folder(dbs, recipient_ses, owner_subfolder, "Test Create")
        assert False, "Should not be able to create folder"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED

    # Test 4: Recipient cannot trash shared folder
    try:
        with oi.db_new_session() as dbs:
            await oi.folders_helper.trash_folder_recursive(dbs, owner_inner.id, recipient_ses)
        assert False, "Should not be able to trash folder"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED


async def org_test__authorization_bypass_attempts(oi: organizer.OrganizerInbound):
    """
    Test authorization bypass attempts - non-owners trying to share/revoke folders.
    """
    # Create owner user and folder structure
    with oi.db_new_session() as dbs:
        owner_user_id = "owner.user"
        dbs.add(DbUser(id=owner_user_id, name="Owner User"))
        dbs.commit()

    owner_ses = org.UserSessionData(
        sid="owner_sid",
        user=clap.UserInfo(id=owner_user_id, name="Owner User"),
        is_admin=False,
        cookies={}
    )

    # Create owner's root folder and target folder
    with oi.db_new_session() as dbs:
        owner_root = await db_get_or_create_user_root_folder(dbs, owner_ses.user, oi.srv, oi.log)
        target_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Target Folder")
        target_folder_id = target_folder.id
        dbs.commit()

    # Create attacker user (different from owner)
    with oi.db_new_session() as dbs:
        attacker_user_id = "attacker.user"
        dbs.add(DbUser(id=attacker_user_id, name="Attacker User"))
        dbs.commit()

    attacker_ses = org.UserSessionData(
        sid="attacker_sid",
        user=clap.UserInfo(id=attacker_user_id, name="Attacker User"),
        is_admin=False,
        cookies={}
    )

    # Test 1: Non-owner trying to share another user's folder
    # Note: cmd_from_client catches PERMISSION_DENIED and shows user message instead of raising
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=attacker_ses,
        cmd="share_folder",
        args=json.dumps({"id": target_folder_id})
    ))

    # Verify no share was created
    with oi.db_new_session() as dbs:
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == target_folder_id).one_or_none()
        assert share is None, "No share should have been created by unauthorized user"

    # Create a legitimate share by the owner
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=owner_ses,
        cmd="share_folder",
        args=json.dumps({"id": target_folder_id})
    ))

    # Verify share was created
    with oi.db_new_session() as dbs:
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == target_folder_id).one_or_none()
        assert share is not None, "Share should have been created by owner"

    # Test 2: Non-owner trying to revoke another user's share
    # Note: cmd_from_client catches PERMISSION_DENIED and shows user message instead of raising
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=attacker_ses,
        cmd="revoke_share",
        args=json.dumps({"id": target_folder_id})
    ))

    # Verify share still exists
    with oi.db_new_session() as dbs:
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == target_folder_id).one_or_none()
        assert share is not None, "Share should still exist after unauthorized revoke attempt"


async def org_test__shared_folder_path_traversal(oi: organizer.OrganizerInbound):
    """
    Test path traversal attempts - accessing folders outside shared subtree via cookie manipulation.
    """
    from organizer.helpers.folders import SHARED_FOLDER_TOKEN_COOKIE_NAME
    # Create owner session with folder hierarchy
    owner_ses, owner_root = await _create_test_folder_and_session(oi)

    with oi.db_new_session() as dbs:
        # Create folders: root -> shared_folder -> inner_folder
        #                 root -> private_folder
        shared_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Shared Folder")
        inner_folder = await oi.folders_helper.create_folder(dbs, owner_ses, shared_folder, "Inner Folder")
        private_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Private Folder")

        shared_folder_id = shared_folder.id
        inner_folder_id = inner_folder.id
        private_folder_id = private_folder.id
        owner_root_id = owner_root.id
        dbs.commit()

    # Create share for shared_folder only
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=shared_folder_id,
            share_token=share_token
        )
        dbs.add(share)
        dbs.commit()

    # Create attacker user
    with oi.db_new_session() as dbs:
        attacker_user_id = "traversal.attacker"
        dbs.add(DbUser(id=attacker_user_id, name="Traversal Attacker"))
        dbs.commit()

    attacker_ses = org.UserSessionData(
        sid="traversal_sid",
        user=clap.UserInfo(id=attacker_user_id, name="Traversal Attacker"),
        is_admin=False,
        cookies={}
    )

    # Set up legitimate shared access
    attacker_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] = share_token

    # Test 1: Attacker should have access to shared subtree
    assert await oi.folders_helper.check_shared_folder_access(shared_folder_id, attacker_ses) is not None
    assert await oi.folders_helper.check_shared_folder_access(inner_folder_id, attacker_ses) is not None

    # Test 2: Attacker should NOT have access to private folders
    assert await oi.folders_helper.check_shared_folder_access(private_folder_id, attacker_ses) is None
    assert await oi.folders_helper.check_shared_folder_access(owner_root_id, attacker_ses) is None

    # Test 3: Cookie manipulation attempt - manually set shared entry to private folder
    from organizer.helpers.folders import SHARED_FOLDER_TOKEN_COOKIE_NAME
    attacker_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] = str(private_folder_id)

    # Should still not have access to private folder (cookie validation should prevent this)
    assert await oi.folders_helper.check_shared_folder_access(private_folder_id, attacker_ses) is None

    # Test 4: Try to fetch contents of private folder - should fail
    try:
        with oi.db_new_session() as dbs:
            private_folder_obj = dbs.query(DbFolder).filter(DbFolder.id == private_folder_id).one()
            await oi.folders_helper.fetch_folder_contents(private_folder_obj, attacker_ses)
        assert False, "Should not be able to fetch private folder contents"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED, f"Expected PERMISSION_DENIED, got {e.status}"


async def org_test__shared_folder_move_operations(oi: organizer.OrganizerInbound):
    """
    Test moving shared folders and their impact on sharing integrity.
    """
    # Create owner user and folder structure
    with oi.db_new_session() as dbs:
        owner_user_id = "move.owner"
        dbs.add(DbUser(id=owner_user_id, name="Move Test Owner"))
        dbs.commit()

    owner_ses = org.UserSessionData(
        sid="move_owner_sid",
        user=clap.UserInfo(id=owner_user_id, name="Move Test Owner"),
        is_admin=False,
        cookies={}
    )

    # Create folder hierarchy: root -> parent_folder -> shared_folder -> inner_folder
    #                          root -> destination_folder
    with oi.db_new_session() as dbs:
        owner_root = await db_get_or_create_user_root_folder(dbs, owner_ses.user, oi.srv, oi.log)
        parent_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Parent Folder")
        shared_folder = await oi.folders_helper.create_folder(dbs, owner_ses, parent_folder, "Shared Folder")
        inner_folder = await oi.folders_helper.create_folder(dbs, owner_ses, shared_folder, "Inner Folder")
        destination_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Destination Folder")

        shared_folder_id = shared_folder.id
        inner_folder_id = inner_folder.id
        destination_folder_id = destination_folder.id
        dbs.commit()

    # Create share for shared_folder
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=shared_folder_id,
            share_token=share_token,
        )
        dbs.add(share)
        dbs.commit()

    # Create recipient user with shared access
    with oi.db_new_session() as dbs:
        recipient_user_id = "move.recipient"
        dbs.add(DbUser(id=recipient_user_id, name="Move Test Recipient"))
        dbs.commit()

    recipient_ses = org.UserSessionData(
        sid="move_recipient_sid",
        user=clap.UserInfo(id=recipient_user_id, name="Move Test Recipient"),
        is_admin=False,
        cookies={}
    )

    # Set up shared access for recipient
    recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] = share_token

    # Test 1: Verify recipient has access before move
    assert await oi.folders_helper.check_shared_folder_access(shared_folder_id, recipient_ses) is not None
    assert await oi.folders_helper.check_shared_folder_access(inner_folder_id, recipient_ses) is not None

    # Test 2: Move shared folder to destination (owner action)
    await oi.move_to_folder(org.MoveToFolderRequest(
        ses=owner_ses,
        dst_folder_id=str(destination_folder_id),
        ids=[clap.FolderItemId(folder_id=str(shared_folder_id))],
        listing_data={}
    ))

    # Test 3: Verify share still exists and is functional after move
    with oi.db_new_session() as dbs:
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == shared_folder_id).one_or_none()
        assert share is not None, "Share should still exist after folder move"
        assert share.share_token == share_token, "Share token should be unchanged"

    # Test 4: Verify recipient still has access after move
    assert await oi.folders_helper.check_shared_folder_access(shared_folder_id, recipient_ses) is not None
    assert await oi.folders_helper.check_shared_folder_access(inner_folder_id, recipient_ses) is not None

    # Test 5: Verify recipient can still fetch folder contents
    with oi.db_new_session() as dbs:
        shared_folder_obj = dbs.query(DbFolder).filter(DbFolder.id == shared_folder_id).one()
        contents = await oi.folders_helper.fetch_folder_contents(shared_folder_obj, recipient_ses)
        assert len(contents) == 1, "Should still see inner folder after move"
        assert contents[0].id == inner_folder_id, "Should still see the correct inner folder"

    # Test 6: Non-owner cannot move shared folder
    try:
        await oi.move_to_folder(org.MoveToFolderRequest(
            ses=recipient_ses,
            dst_folder_id=str(destination_folder_id),
            ids=[clap.FolderItemId(folder_id=str(shared_folder_id))],
            listing_data={}
        ))
        assert False, "Recipient should not be able to move shared folder"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED, f"Expected PERMISSION_DENIED (error handled), got {e.status}"


async def org_test__shared_folder_rename_operations(oi: organizer.OrganizerInbound):
    """
    Test renaming shared folders while sharing is active.
    """
    # Create owner user and folder
    with oi.db_new_session() as dbs:
        owner_user_id = "rename.owner"
        dbs.add(DbUser(id=owner_user_id, name="Rename Test Owner"))
        dbs.commit()

    owner_ses = org.UserSessionData(
        sid="rename_owner_sid",
        user=clap.UserInfo(id=owner_user_id, name="Rename Test Owner"),
        is_admin=False,
        cookies={}
    )

    # Create shared folder
    with oi.db_new_session() as dbs:
        owner_root = await db_get_or_create_user_root_folder(dbs, owner_ses.user, oi.srv, oi.log)
        shared_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Original Name")
        shared_folder_id = shared_folder.id
        dbs.commit()

    # Create share
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=shared_folder_id,
            share_token=share_token,
        )
        dbs.add(share)
        dbs.commit()

    # Create recipient user with shared access
    with oi.db_new_session() as dbs:
        recipient_user_id = "rename.recipient"
        dbs.add(DbUser(id=recipient_user_id, name="Rename Test Recipient"))
        dbs.commit()

    recipient_ses = org.UserSessionData(
        sid="rename_recipient_sid",
        user=clap.UserInfo(id=recipient_user_id, name="Rename Test Recipient"),
        is_admin=False,
        cookies={}
    )

    # Set up shared access for recipient
    recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] = share_token

    # Test 1: Owner can rename shared folder
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=owner_ses,
        cmd="rename_folder",
        args=json.dumps({"id": shared_folder_id, "new_name": "Renamed Shared Folder"})
    ))

    # Test 2: Verify folder was renamed
    with oi.db_new_session() as dbs:
        folder = dbs.query(DbFolder).filter(DbFolder.id == shared_folder_id).one()
        assert folder.title == "Renamed Shared Folder", "Folder should be renamed"

    # Test 3: Verify share still exists and is functional after rename
    with oi.db_new_session() as dbs:
        share = dbs.query(DbSharedFolder).filter(DbSharedFolder.folder_id == shared_folder_id).one_or_none()
        assert share is not None, "Share should still exist after folder rename"
        assert share.share_token == share_token, "Share token should be unchanged"

    # Test 4: Verify recipient still has access after rename
    assert await oi.folders_helper.check_shared_folder_access(shared_folder_id, recipient_ses) is not None

    # Test 5: Recipient cannot rename shared folder (read-only access)
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=recipient_ses,
        cmd="rename_folder",
        args=json.dumps({"id": shared_folder_id, "new_name": "Unauthorized Rename"})
    ))

    # Test 6: Verify folder name was NOT changed by recipient
    with oi.db_new_session() as dbs:
        folder = dbs.query(DbFolder).filter(DbFolder.id == shared_folder_id).one()
        assert folder.title == "Renamed Shared Folder", "Folder name should not be changed by recipient"


async def org_test__move_content_into_shared_folders(oi: organizer.OrganizerInbound):
    """
    Test moving content into and out of shared folders with permission validation.
    """
    # Create owner user and complex folder structure
    with oi.db_new_session() as dbs:
        owner_user_id = "content.owner"
        dbs.add(DbUser(id=owner_user_id, name="Content Test Owner"))
        dbs.commit()

    owner_ses = org.UserSessionData(
        sid="content_owner_sid",
        user=clap.UserInfo(id=owner_user_id, name="Content Test Owner"),
        is_admin=False,
        cookies={}
    )

    # Create folder structure
    with oi.db_new_session() as dbs:
        owner_root = await db_get_or_create_user_root_folder(dbs, owner_ses.user, oi.srv, oi.log)
        shared_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Shared Container")
        private_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Private Folder")
        moveable_folder = await oi.folders_helper.create_folder(dbs, owner_ses, private_folder, "Moveable Folder")

        shared_folder_id = shared_folder.id
        private_folder_id = private_folder.id
        moveable_folder_id = moveable_folder.id
        dbs.commit()

    # Create share for shared_folder
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=shared_folder_id,
            share_token=share_token,
        )
        dbs.add(share)
        dbs.commit()

    # Create recipient user
    with oi.db_new_session() as dbs:
        recipient_user_id = "content.recipient"
        dbs.add(DbUser(id=recipient_user_id, name="Content Test Recipient"))
        dbs.commit()

    recipient_ses = org.UserSessionData(
        sid="content_recipient_sid",
        user=clap.UserInfo(id=recipient_user_id, name="Content Test Recipient"),
        is_admin=False,
        cookies={}
    )

    # Set up shared access for recipient
    recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] = share_token

    # Test 1: Owner can move content into shared folder
    await oi.move_to_folder(org.MoveToFolderRequest(
        ses=owner_ses,
        dst_folder_id=str(shared_folder_id),
        ids=[clap.FolderItemId(folder_id=str(moveable_folder_id))],
        listing_data={}
    ))

    # Test 2: Verify folder was moved into shared space
    with oi.db_new_session() as dbs:
        shared_folder_obj = dbs.query(DbFolder).filter(DbFolder.id == shared_folder_id).one()
        contents = await oi.folders_helper.fetch_folder_contents(shared_folder_obj, owner_ses)
        assert len(contents) == 1, "Shared folder should contain moved folder"
        assert contents[0].id == moveable_folder_id, "Should contain the moved folder"

    # Test 3: Recipient can now see the moved content (inherited shared access)
    assert await oi.folders_helper.check_shared_folder_access(moveable_folder_id, recipient_ses) is not None

    # Test 4: Recipient cannot move content out of shared folder
    try:
        await oi.move_to_folder(org.MoveToFolderRequest(
            ses=recipient_ses,
            dst_folder_id=str(private_folder_id),
            ids=[clap.FolderItemId(folder_id=str(moveable_folder_id))],
            listing_data={}
        ))
        assert False, "Recipient should not be able to move content out of shared folder"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED, f"Expected PERMISSION_DENIED (error handled), got {e.status}"

    # Test 5: Recipient cannot move content into shared folder
    # Create another folder for testing
    with oi.db_new_session() as dbs:
        private_folder_obj = dbs.query(DbFolder).filter(DbFolder.id == private_folder_id).one()
        test_folder = await oi.folders_helper.create_folder(dbs, owner_ses, private_folder_obj, "Test Folder")
        test_folder_id = test_folder.id
        dbs.commit()

    try:
        await oi.move_to_folder(org.MoveToFolderRequest(
            ses=recipient_ses,
            dst_folder_id=str(shared_folder_id),
            ids=[clap.FolderItemId(folder_id=str(test_folder_id))],
            listing_data={}
        ))
        assert False, "Recipient should not be able to move content into shared folder"
    except GRPCError as e:
        assert e.status == GrpcStatus.PERMISSION_DENIED, f"Expected PERMISSION_DENIED (error handled), got {e.status}"


async def org_test__admin_owner_transfer(oi: organizer.OrganizerInbound):
    """
    Test move_to_folder() as an admin -- Admin can move any folder or media file to any user's folder.
       When moving a folder into another user's folder, ownership of the source folder and all its contents
       are transferred to the destination folder's owner.
    """
    # Fetch a media file from the test database
    media_files = await oi.srv.db_get_media_files(org.DbGetMediaFilesRequest(all=clap.Empty()))
    assert len(media_files.items) > 0, "No media files found in the test database"

    media_owners = {v.id: v.user_id for v in media_files.items}

    # Get user for the media file
    src_media = media_files.items[0]
    src_user = clap.UserInfo(id=src_media.user_id, name=f"Name of {src_media.user_id}")
    src_user_ses = org.UserSessionData(sid="test_sid", user=src_user, is_admin=False, cookies={})

    # Create a folder + a subfolder for the source user
    with oi.db_new_session() as dbs:
        src_root_fld = await db_get_or_create_user_root_folder(dbs, src_user, oi.srv, oi.log)
        src_fld = await oi.folders_helper.create_folder(dbs, src_user_ses, src_root_fld, "Ownertransfer Test Folder")
        src_subfld = await oi.folders_helper.create_folder(dbs, src_user_ses, src_fld, "Ownertransfer Test Subfolder")

    # Move the file to the subfolder
    await oi.move_to_folder(org.MoveToFolderRequest(
        src_user_ses,
        ids=[clap.FolderItemId(media_file_id=src_media.id)],
        dst_folder_id=str(src_subfld.id),
        listing_data={}))

    assert src_media.user_id == src_user.id
    assert src_fld.user_id == src_user.id
    assert src_subfld.user_id == src_user.id


    # Create a new user and session
    dst_user = clap.UserInfo(id="ownertransfer-test.dst_user", name="Ownertransfer Test User")
    with oi.db_new_session() as dbs:
        dbs.add(DbUser(id=dst_user.id, name=dst_user.name))
        dbs.commit()
    dst_user_ses = org.UserSessionData(sid="test_sid2",user=clap.UserInfo(id=dst_user.id, name=dst_user.name), is_admin=False, cookies={})

    # Create a folder for the destination user
    with oi.db_new_session() as dbs:
        dst_root_fld = await db_get_or_create_user_root_folder(dbs, dst_user, oi.srv, oi.log)
        dst_fld = await oi.folders_helper.create_folder(dbs, dst_user_ses, dst_root_fld, "Ownertransfer Destination Folder")

    assert dst_root_fld.user_id == dst_user.id
    assert dst_fld.user_id == dst_user.id

    # No ownership transfer should've happened yet, check that media_file_owners matches
    new_owners = await oi.srv.db_get_media_files(org.DbGetMediaFilesRequest(all=clap.Empty()))
    for v in new_owners.items:
        assert media_owners[v.id] == v.user_id

    # As an admin, move the source user's folder into the destination user's folder
    admin_ses = org.UserSessionData(sid="test_sid_admin", user=clap.UserInfo(id="test.admin", name="The Admin"), is_admin=True, cookies={})
    with oi.db_new_session() as dbs:
        dbs.add(DbUser(id=admin_ses.user.id, name=admin_ses.user.name))
        dbs.commit()

    await oi.move_to_folder(org.MoveToFolderRequest(
        admin_ses,
        ids=[clap.FolderItemId(folder_id=str(src_fld.id))],
        dst_folder_id=str(dst_fld.id),
        listing_data={}))

    # Check that ownership was transferred
    with oi.db_new_session() as dbs:
        src_fld = dbs.query(DbFolder).filter(DbFolder.id == src_fld.id).one_or_none()
        src_subfld = dbs.query(DbFolder).filter(DbFolder.id == src_subfld.id).one_or_none()
        src_media = dbs.query(DbMediaFile).filter(DbMediaFile.id == src_media.id).one_or_none()
        assert src_fld is not None
        assert src_subfld is not None
        assert src_media is not None
        assert src_fld.user_id == dst_user.id
        assert src_subfld.user_id == dst_user.id
        assert src_media.user_id == dst_user.id

    # Check that the folder hierarchy is intact, but in `dst_fld`.
    dst_fld_cont = await oi.folders_helper.fetch_folder_contents(dst_fld, dst_user_ses)
    assert any(fi.id == src_fld.id for fi in dst_fld_cont)
    src_fld_cont = await oi.folders_helper.fetch_folder_contents(src_fld, dst_user_ses)
    assert len(src_fld_cont) == 1
    assert src_fld_cont[0].id == src_subfld.id
    subfld_cont = await oi.folders_helper.fetch_folder_contents(src_subfld, dst_user_ses)
    assert len(subfld_cont) == 1
    assert subfld_cont[0].id == src_media.id

    # Check that the example file's ownership was transferred, but not other files
    media_owners[src_media.id] = dst_user.id    # Update the expected owner
    new_owners = await oi.srv.db_get_media_files(org.DbGetMediaFilesRequest(all=clap.Empty()))
    for v in new_owners.items:
        assert media_owners[v.id] == v.user_id


async def org_test__admin_open_other_user_folder(oi: organizer.OrganizerInbound):
    """
    Test that administrators can double-click (open) another user's folder from the all users folder listing view.
    This regression test ensures the open_folder command works correctly for administrators accessing other users' folders.
    """
    # Create two users - one regular user and one admin
    with oi.db_new_session() as dbs:
        regular_user_id = "admin.test.regular"
        admin_user_id = "admin.test.admin"
        dbs.add(DbUser(id=regular_user_id, name="Regular User"))
        dbs.add(DbUser(id=admin_user_id, name="Admin User"))
        dbs.commit()

    # Create sessions for both users
    regular_ses = org.UserSessionData(
        sid="regular_sid",
        user=clap.UserInfo(id=regular_user_id, name="Regular User"),
        is_admin=False,
        cookies={}
    )

    admin_ses = org.UserSessionData(
        sid="admin_sid", 
        user=clap.UserInfo(id=admin_user_id, name="Admin User"),
        is_admin=True,
        cookies={}
    )

    # Create root folders for both users and add some content to regular user's folder
    with oi.db_new_session() as dbs:
        regular_root = await db_get_or_create_user_root_folder(dbs, regular_ses.user, oi.srv, oi.log)
        admin_root = await db_get_or_create_user_root_folder(dbs, admin_ses.user, oi.srv, oi.log)
        
        # Create a test folder in regular user's space
        test_folder = await oi.folders_helper.create_folder(dbs, regular_ses, regular_root, "Regular User's Test Folder")
        regular_root_id = regular_root.id
        test_folder_id = test_folder.id
        dbs.commit()

    # Admin navigates to main page - should see all users' folders in admin view
    admin_page = await organizer.navigate_page_impl(oi, org.NavigatePageRequest(ses=admin_ses))
    assert isinstance(admin_page, org.ClientShowPageRequest)
    
    # Verify admin can see the regular user's folder in the listing
    # The _admin_show_all_user_homes function should have added a folder listing with user folders
    folder_listings = [item.folder_listing for item in admin_page.page_items if item.folder_listing]
    assert len(folder_listings) > 0, "Admin should see folder listings"
    
    # Find the all-users folder listing (should be the second one after the admin's own content)
    admin_user_listing = None
    for listing in folder_listings:
        if listing.listing_data and listing.listing_data.get("folder_id"):
            # Look for items with user folders
            for item in listing.items:
                if item.folder and item.folder.title == regular_user_id:
                    admin_user_listing = listing
                    break
    
    assert admin_user_listing is not None, "Admin should see other users' folders in listing"

    # Test: Admin double-clicks (opens) the regular user's folder
    # This simulates clicking on the regular user's folder from the all users view
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=admin_ses,
        cmd="open_folder", 
        args=json.dumps({"id": regular_root_id})
    ))

    # Verify admin can see the contents of regular user's folder
    # Get updated folder path - should now include regular user's root folder
    folder_path, _ = await oi.folders_helper.get_current_folder_path(admin_ses)
    assert len(folder_path) >= 1, "Admin should have a valid folder path"
    current_folder = folder_path[-1]
    assert current_folder.id == regular_root_id, "Admin should now be viewing regular user's root folder"
    assert current_folder.user_id == regular_user_id, "Current folder should belong to regular user"

    # Verify admin can fetch contents of regular user's folder
    contents = await oi.folders_helper.fetch_folder_contents(current_folder, admin_ses)
    assert len(contents) == 1, "Regular user's folder should contain the test folder"
    assert contents[0].id == test_folder_id, "Should see the test folder created earlier"
    assert isinstance(contents[0], DbFolder), "Content should be a folder"
    assert contents[0].title == "Regular User's Test Folder", "Should see correct folder title"

    # Test: Admin can navigate deeper into regular user's folder structure
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=admin_ses,
        cmd="open_folder",
        args=json.dumps({"id": test_folder_id})  
    ))

    # Verify admin can navigate to subfolder
    folder_path, _ = await oi.folders_helper.get_current_folder_path(admin_ses)
    assert len(folder_path) >= 2, "Admin should have deeper folder path"
    current_folder = folder_path[-1]
    assert current_folder.id == test_folder_id, "Admin should now be viewing the test subfolder"
    assert current_folder.user_id == regular_user_id, "Subfolder should still belong to regular user"

    # Verify admin can fetch contents of the subfolder (should be empty)
    contents = await oi.folders_helper.fetch_folder_contents(current_folder, admin_ses)
    assert len(contents) == 0, "Test subfolder should be empty"

    print("✓ Admin successfully opened and navigated another user's folder structure")


async def org_test__corrupted_folder_path_cookie(oi: organizer.OrganizerInbound):
    """
    Test handling of corrupted folder path cookies - ensures robust fallback behavior.
    """
    ses, root_fld = await _create_test_folder_and_session(oi)
    
    # Test 1: Malformed JSON in cookie
    ses.cookies[PATH_COOKIE_NAME] = "invalid json{"
    folder_path, _ = await oi.folders_helper.get_current_folder_path(ses)
    assert len(folder_path) == 1, "Should fall back to root folder on malformed JSON"
    assert folder_path[0].id == root_fld.id, "Should return user's root folder"
    
    # Test 2: Non-existent folder IDs in cookie
    ses.cookies[PATH_COOKIE_NAME] = json.dumps([99999, 88888])
    folder_path, _ = await oi.folders_helper.get_current_folder_path(ses)
    assert len(folder_path) == 1, "Should fall back to root folder on non-existent IDs"
    assert folder_path[0].id == root_fld.id, "Should return user's root folder"
    
    # Test 3: Mixed ownership in cookie (non-admin user)
    # Create another user and their folder
    with oi.db_new_session() as dbs:
        other_user_id = "corrupt.test.other"
        dbs.add(DbUser(id=other_user_id, name="Other User"))
        dbs.commit()
    
    other_ses = org.UserSessionData(
        sid="other_sid",
        user=clap.UserInfo(id=other_user_id, name="Other User"),
        is_admin=False,
        cookies={}
    )
    
    with oi.db_new_session() as dbs:
        other_root = await db_get_or_create_user_root_folder(dbs, other_ses.user, oi.srv, oi.log)
        other_root_id = other_root.id
        dbs.commit()
    
    # Set cookie with mixed ownership (user's folder + other user's folder)
    ses.cookies[PATH_COOKIE_NAME] = json.dumps([root_fld.id, other_root_id])
    folder_path, _ = await oi.folders_helper.get_current_folder_path(ses)
    assert len(folder_path) == 1, "Should clear mixed ownership path and fall back to root"
    assert folder_path[0].id == root_fld.id, "Should return user's root folder"
    
    # Verify cookie was cleared
    assert ses.cookies.get(PATH_COOKIE_NAME) != json.dumps([root_fld.id, other_root_id]), "Cookie should have been cleared"
    
    print("✓ Corrupted folder path cookie handling works correctly")


async def org_test__open_nonexistent_folder(oi: organizer.OrganizerInbound):
    """
    Test opening a folder that doesn't exist - ensures proper error handling.
    """
    ses, _ = await _create_test_folder_and_session(oi)
    
    # Test opening non-existent folder
    try:
        await oi.cmd_from_client(org.CmdFromClientRequest(
            ses=ses, 
            cmd="open_folder", 
            args=json.dumps({"id": 99999})
        ))
        assert False, "Should have raised NOT_FOUND error for non-existent folder"
    except GRPCError as e:
        assert e.status == GrpcStatus.NOT_FOUND, f"Expected NOT_FOUND, got {e.status}"
        assert "not found" in str(e.message).lower(), "Error message should mention folder not found"
    
    # Test with invalid folder ID type (should be caught by validation)
    try:
        await oi.cmd_from_client(org.CmdFromClientRequest(
            ses=ses,
            cmd="open_folder", 
            args=json.dumps({"id": "invalid_string"})
        ))
        assert False, "Should have raised error for invalid folder ID type"
    except (GRPCError, AssertionError):
        pass  # Expected - either GRPCError or AssertionError from validation
    
    print("✓ Non-existent folder handling works correctly")


async def org_test__breadcrumb_navigation_up(oi: organizer.OrganizerInbound):
    """
    Test navigating back up the folder hierarchy - ensures breadcrumb truncation works.
    """
    ses, root_fld = await _create_test_folder_and_session(oi)
    
    # Create nested structure: root -> folder1 -> folder2 -> folder3
    with oi.db_new_session() as dbs:
        folder1 = await oi.folders_helper.create_folder(dbs, ses, root_fld, "Level 1")
        folder1_id = folder1.id
        dbs.commit()
    
    with oi.db_new_session() as dbs:
        folder1_obj = dbs.query(DbFolder).filter(DbFolder.id == folder1_id).one()
        folder2 = await oi.folders_helper.create_folder(dbs, ses, folder1_obj, "Level 2")
        folder2_id = folder2.id
        dbs.commit()
    
    with oi.db_new_session() as dbs:
        folder2_obj = dbs.query(DbFolder).filter(DbFolder.id == folder2_id).one()
        folder3 = await oi.folders_helper.create_folder(dbs, ses, folder2_obj, "Level 3")
        folder3_id = folder3.id
        dbs.commit()
    
    # Navigate down to folder3 step by step
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=ses, cmd="open_folder", args=json.dumps({"id": folder1_id})))
    
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=ses, cmd="open_folder", args=json.dumps({"id": folder2_id})))
    
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=ses, cmd="open_folder", args=json.dumps({"id": folder3_id})))
    
    # Verify we're at folder3 with full trail
    path, _ = await oi.folders_helper.get_current_folder_path(ses)
    path_ids = [f.id for f in path]
    assert len(path_ids) == 4, "Should have full path to folder3"
    assert path_ids == [root_fld.id, folder1_id, folder2_id, folder3_id], "Path should be complete hierarchy"
    
    # Navigate back to folder1 (should truncate trail)
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=ses, cmd="open_folder", args=json.dumps({"id": folder1_id})))
    
    path, _ = await oi.folders_helper.get_current_folder_path(ses)
    path_ids = [f.id for f in path]
    assert len(path_ids) == 2, "Should have truncated path"
    assert path_ids == [root_fld.id, folder1_id], "Should truncate at folder1"
    
    # Navigate back to root
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=ses, cmd="open_folder", args=json.dumps({"id": root_fld.id})))
    
    path, _ = await oi.folders_helper.get_current_folder_path(ses)
    path_ids = [f.id for f in path]
    assert len(path_ids) == 1, "Should be back to root only"
    assert path_ids == [root_fld.id], "Should be at root folder"
    
    print("✓ Breadcrumb navigation up hierarchy works correctly")


async def org_test__admin_multi_user_context_switching(oi: organizer.OrganizerInbound):
    """
    Test admin switching between multiple users' folders in the same session.
    """
    # Create 3 users
    with oi.db_new_session() as dbs:
        user1_id = "multi.user1"
        user2_id = "multi.user2" 
        admin_id = "multi.admin"
        dbs.add(DbUser(id=user1_id, name="User 1"))
        dbs.add(DbUser(id=user2_id, name="User 2"))
        dbs.add(DbUser(id=admin_id, name="Admin User"))
        dbs.commit()

    user1_ses = org.UserSessionData(
        sid="user1_sid", user=clap.UserInfo(id=user1_id, name="User 1"), 
        is_admin=False, cookies={})
        
    user2_ses = org.UserSessionData(
        sid="user2_sid", user=clap.UserInfo(id=user2_id, name="User 2"),
        is_admin=False, cookies={})
        
    admin_ses = org.UserSessionData(
        sid="admin_sid", user=clap.UserInfo(id=admin_id, name="Admin User"),
        is_admin=True, cookies={})

    # Create root folders for users
    with oi.db_new_session() as dbs:
        user1_root = await db_get_or_create_user_root_folder(dbs, user1_ses.user, oi.srv, oi.log)
        user1_root_id = user1_root.id
        dbs.commit()
        
    with oi.db_new_session() as dbs:
        user2_root = await db_get_or_create_user_root_folder(dbs, user2_ses.user, oi.srv, oi.log)
        user2_root_id = user2_root.id
        dbs.commit()
        
    with oi.db_new_session() as dbs:
        admin_root = await db_get_or_create_user_root_folder(dbs, admin_ses.user, oi.srv, oi.log)
        admin_root_id = admin_root.id
        dbs.commit()

    # Admin starts at their own root
    path, _ = await oi.folders_helper.get_current_folder_path(admin_ses)
    assert path[-1].id == admin_root_id, "Admin should start at their own root"
    assert path[-1].user_id == admin_id, "Should be admin's folder"

    # Admin opens user1's folder
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=admin_ses, cmd="open_folder", args=json.dumps({"id": user1_root_id})))
    
    path, _ = await oi.folders_helper.get_current_folder_path(admin_ses)
    assert len(path) == 1, "Should start fresh trail when switching users"
    assert path[0].id == user1_root_id, "Should be at user1's root"
    assert path[0].user_id == user1_id, "Should be user1's folder"

    # Admin switches to user2's folder
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=admin_ses, cmd="open_folder", args=json.dumps({"id": user2_root_id})))
    
    path, _ = await oi.folders_helper.get_current_folder_path(admin_ses)
    assert len(path) == 1, "Should start fresh trail again when switching users"
    assert path[0].id == user2_root_id, "Should be at user2's root"
    assert path[0].user_id == user2_id, "Should be user2's folder"

    # Admin switches back to their own folder
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=admin_ses, cmd="open_folder", args=json.dumps({"id": admin_root_id})))
    
    path, _ = await oi.folders_helper.get_current_folder_path(admin_ses)
    assert len(path) == 1, "Should have single folder when returning to own root"
    assert path[0].id == admin_root_id, "Should be at admin's root"
    assert path[0].user_id == admin_id, "Should be admin's folder"

    print("✓ Admin multi-user context switching works correctly")


async def org_test__shared_folder_cookie_interactions(oi: organizer.OrganizerInbound):
    """
    Test interaction between path cookies and shared folder cookies.
    """
    # Create owner session and shared folder
    owner_ses, owner_root = await _create_test_folder_and_session(oi)
    
    with oi.db_new_session() as dbs:
        shared_folder = await oi.folders_helper.create_folder(dbs, owner_ses, owner_root, "Shared Folder")
        subfolder = await oi.folders_helper.create_folder(dbs, owner_ses, shared_folder, "Subfolder")
        shared_folder_id = shared_folder.id
        subfolder_id = subfolder.id
        dbs.commit()

    # Create share
    with oi.db_new_session() as dbs:
        share_token = await oi.folders_helper.generate_share_token()
        share = DbSharedFolder(
            folder_id=shared_folder_id,
            share_token=share_token
        )
        dbs.add(share)
        dbs.commit()

    # Create recipient user
    with oi.db_new_session() as dbs:
        recipient_user_id = "shared.cookie.recipient"
        dbs.add(DbUser(id=recipient_user_id, name="Cookie Test Recipient"))
        dbs.commit()

    recipient_ses = org.UserSessionData(
        sid="recipient_sid",
        user=clap.UserInfo(id=recipient_user_id, name="Cookie Test Recipient"),
        is_admin=False,
        cookies={}
    )

    # Recipient accesses shared folder via shared URL
    result = await organizer.navigate_page_impl(oi, org.NavigatePageRequest(
        ses=recipient_ses,
        page_id=f"shared.{share_token}"
    ))
    
    assert isinstance(result, org.ClientShowPageRequest), "Should return valid page"
    
    # Verify both cookies are set
    assert PATH_COOKIE_NAME in recipient_ses.cookies, "Path cookie should be set"
    assert SHARED_FOLDER_TOKEN_COOKIE_NAME in recipient_ses.cookies, "Shared token cookie should be set"
    assert recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] == share_token, "Shared token should match"
    
    # Verify path cookie points to shared folder
    path_data = json.loads(recipient_ses.cookies[PATH_COOKIE_NAME])
    assert path_data == [shared_folder_id], "Path should point to shared folder"

    # Navigate within shared folder - should preserve shared token
    await oi.cmd_from_client(org.CmdFromClientRequest(
        ses=recipient_ses, 
        cmd="open_folder", 
        args=json.dumps({"id": subfolder_id})
    ))
    
    # Verify shared token is still present
    assert SHARED_FOLDER_TOKEN_COOKIE_NAME in recipient_ses.cookies, "Shared token should be preserved"
    assert recipient_ses.cookies[SHARED_FOLDER_TOKEN_COOKIE_NAME] == share_token, "Shared token should be unchanged"
    
    # Verify path was updated
    path_data = json.loads(recipient_ses.cookies[PATH_COOKIE_NAME])
    assert path_data == [shared_folder_id, subfolder_id], "Path should include subfolder"
    
    # Verify recipient can still access folder contents
    path, _ = await oi.folders_helper.get_current_folder_path(recipient_ses)
    assert len(path) == 2, "Should have path to subfolder"
    assert path[0].id == shared_folder_id, "Should start at shared folder"
    assert path[1].id == subfolder_id, "Should end at subfolder"

    print("✓ Shared folder cookie interactions work correctly")


# TODO: JavaScript action validation testing?
# Consider adding validation for generated JavaScript code in actiondefs.py to catch:
# - Syntax errors in generated JavaScript snippets
# - Undefined variable access (e.g., _action_args.folder vs _action_args.listing_data)
# - Missing null checks and API contract violations
# - Common popup action bugs like accessing wrong data structures
# Potential approaches: Python AST analysis of JS strings, or external validation tool
# Would have caught the folder sharing bug (_action_args.folder?.id vs _action_args.listing_data?.folder_id)
