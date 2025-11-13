"""
Metaplugin tests and mock plugin code.

This module contains:
1. Mock plugin code strings that are written to temporary directories during testing
2. Test functions that exercise the metaplugin loader and hooks

The test functions are imported into testing_methods.py so they can be discovered
by the test framework's module inspection.
"""

import tempfile
from pathlib import Path

# Simple plugin with just required attributes
MOCK_PLUGIN_BASIC = '''
class Plugin:
    PLUGIN_NAME = "test_mock_basic"
    PLUGIN_VERSION = "1.0.0"

    def __init__(self):
        self.initialized = False
'''

# Plugin with on_init hook that records the call
MOCK_PLUGIN_ON_INIT = '''
class Plugin:
    PLUGIN_NAME = "test_mock_on_init"
    PLUGIN_VERSION = "1.0.0"

    def __init__(self):
        self.init_called = False
        self.context_received = None

    async def on_init(self, context):
        self.init_called = True
        self.context_received = context
'''

# Plugin that extends actions
MOCK_PLUGIN_EXTEND_ACTIONS = '''
class Plugin:
    PLUGIN_NAME = "test_mock_extend_actions"
    PLUGIN_VERSION = "1.0.0"

    def extend_actions(self, actions):
        actions["test_custom_action"] = {"marker": "mock_plugin_was_here"}
        return actions
'''

# Plugin that augments folder listing
MOCK_PLUGIN_AUGMENT_LISTING = '''
class Plugin:
    PLUGIN_NAME = "test_mock_augment_listing"
    PLUGIN_VERSION = "1.0.0"

    async def augment_folder_listing(self, listing_items, folder_context, session):
        for item in listing_items:
            item["_test_marker"] = "augmented_by_mock"
        return listing_items
'''

# Plugin that augments listing data
MOCK_PLUGIN_AUGMENT_DATA = '''
class Plugin:
    PLUGIN_NAME = "test_mock_augment_data"
    PLUGIN_VERSION = "1.0.0"

    async def augment_listing_data(self, listing_data, folder_context, session):
        listing_data["test_custom_key"] = "test_custom_value"
        listing_data["plugin_active"] = "true"
        return listing_data
'''

# Plugin that throws an exception
MOCK_PLUGIN_EXCEPTION = '''
class Plugin:
    PLUGIN_NAME = "test_mock_exception"
    PLUGIN_VERSION = "1.0.0"

    def extend_actions(self, actions):
        raise ValueError("Intentional test exception")
'''

# First plugin for multi-plugin test
MOCK_PLUGIN_FIRST = '''
class Plugin:
    PLUGIN_NAME = "test_mock_first"
    PLUGIN_VERSION = "1.0.0"

    def extend_actions(self, actions):
        actions["_call_order"] = actions.get("_call_order", [])
        actions["_call_order"].append("first")
        return actions
'''

# Second plugin for multi-plugin test
MOCK_PLUGIN_SECOND = '''
class Plugin:
    PLUGIN_NAME = "test_mock_second"
    PLUGIN_VERSION = "1.0.0"

    def extend_actions(self, actions):
        actions["_call_order"] = actions.get("_call_order", [])
        actions["_call_order"].append("second")
        return actions
'''

# Invalid plugin (missing Plugin class)
MOCK_PLUGIN_INVALID_MISSING_CLASS = 'def some_function(): pass'

# Invalid plugin (missing PLUGIN_NAME)
MOCK_PLUGIN_INVALID_MISSING_NAME = '''
class Plugin:
    PLUGIN_VERSION = "1.0.0"
'''

# Valid plugin for invalid plugins test
MOCK_PLUGIN_VALID = '''
class Plugin:
    PLUGIN_NAME = "test_mock_valid"
    PLUGIN_VERSION = "1.0.0"
'''

# Plugin that allows access (returns True)
MOCK_PLUGIN_AUTHZ_ALLOW = '''
class Plugin:
    PLUGIN_NAME = "test_mock_authz_allow"
    PLUGIN_VERSION = "1.0.0"

    async def check_action_authorization(self, action, folder, media_file, session):
        # Always allow for testing
        if action == "test_action_allow":
            return True
        return None
'''

# Plugin that denies access (returns False)
MOCK_PLUGIN_AUTHZ_DENY = '''
class Plugin:
    PLUGIN_NAME = "test_mock_authz_deny"
    PLUGIN_VERSION = "1.0.0"

    async def check_action_authorization(self, action, folder, media_file, session):
        # Always deny for testing
        if action == "test_action_deny":
            return False
        return None
'''

# Plugin that defers to default (returns None)
MOCK_PLUGIN_AUTHZ_DEFER = '''
class Plugin:
    PLUGIN_NAME = "test_mock_authz_defer"
    PLUGIN_VERSION = "1.0.0"

    async def check_action_authorization(self, action, folder, media_file, session):
        # Always defer to defaults
        return None
'''

# Plugin that checks folder owner
MOCK_PLUGIN_AUTHZ_FOLDER_OWNER = '''
class Plugin:
    PLUGIN_NAME = "test_mock_authz_folder_owner"
    PLUGIN_VERSION = "1.0.0"

    async def check_action_authorization(self, action, folder, media_file, session):
        if action == "rename_folder" and folder:
            # Allow only if user is the owner
            if folder.user_id == "allowed_user":
                return True
        return None
'''


# ============================= Metaplugin Test Functions ==============================
# These functions are imported into testing_methods.py for test discovery and execution


async def org_test__metaplugin_loader_basic(oi):
    """
    Test that metaplugin loader can discover and load plugins from a directory.
    """
    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_basic.py"
        plugin_file.write_text(MOCK_PLUGIN_BASIC)

        # Import the loader
        from organizer.metaplugin import MetaPluginLoader

        # Create loader and load plugins
        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Verify plugin was loaded
        assert len(loader.loaded_plugins) == 1, f"Expected 1 plugin, got {len(loader.loaded_plugins)}"
        plugin = loader.loaded_plugins[0]

        assert hasattr(plugin, "PLUGIN_NAME"), "Plugin missing PLUGIN_NAME"
        assert plugin.PLUGIN_NAME == "test_mock_basic", f"Wrong PLUGIN_NAME: {plugin.PLUGIN_NAME}"
        assert hasattr(plugin, "PLUGIN_VERSION"), "Plugin missing PLUGIN_VERSION"
        assert plugin.PLUGIN_VERSION == "1.0.0", f"Wrong PLUGIN_VERSION: {plugin.PLUGIN_VERSION}"

        print(f"✓ Successfully loaded plugin: {plugin.PLUGIN_NAME} v{plugin.PLUGIN_VERSION}")


async def org_test__metaplugin_loader_missing_dir(oi):
    """
    Test that metaplugin loader handles missing plugins directory gracefully.
    """
    from organizer.metaplugin import MetaPluginLoader

    # Create loader with non-existent directory
    loader = MetaPluginLoader("/nonexistent/path/to/plugins", oi.log)

    # Should not raise, just log debug message
    loader.load_plugins()

    assert len(loader.loaded_plugins) == 0, "No plugins should be loaded"
    print("✓ Loader handles missing directory gracefully")


async def org_test__metaplugin_loader_empty_dir(oi):
    """
    Test that metaplugin loader handles empty plugins directory gracefully.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        assert len(loader.loaded_plugins) == 0, "No plugins should be loaded from empty dir"
        print("✓ Loader handles empty directory gracefully")


async def org_test__metaplugin_on_init_hook(oi):
    """
    Test that on_init() hook is called with correct OrganizerContext.
    """
    from organizer.metaplugin import MetaPluginLoader
    from organizer.metaplugin import OrganizerContext

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_on_init.py"
        plugin_file.write_text(MOCK_PLUGIN_ON_INIT)

        # Load plugin and call on_init
        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        assert len(loader.loaded_plugins) == 1
        plugin = loader.loaded_plugins[0]

        # Create OrganizerContext
        ctx = OrganizerContext(
            db_session=oi.db_new_session,
            srv=oi.srv,
            log=oi.log,
            folders_helper=oi.folders_helper,
            pages_helper=oi.pages_helper,
        )

        # Call on_init hook
        await loader.call_on_init_hooks(ctx)

        # Verify hook was called
        assert plugin.init_called, "on_init hook was not called"
        assert plugin.context_received is not None, "Context was not passed"

        # Verify logger is either the original or a LoggerAdapter wrapping it
        from logging import LoggerAdapter
        received_logger = plugin.context_received.log
        if isinstance(received_logger, LoggerAdapter):
            assert received_logger.logger == oi.log, "LoggerAdapter should wrap the organizer logger"
        else:
            assert received_logger == oi.log, "Logger should be the organizer logger"

        print("✓ on_init() hook called with correct context")


async def org_test__metaplugin_extend_actions(oi):
    """
    Test that extend_actions() hook can add custom actions.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_extend_actions.py"
        plugin_file.write_text(MOCK_PLUGIN_EXTEND_ACTIONS)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Call extend_actions hook
        initial_actions = {}
        result_actions = loader.call_extend_actions_hooks(initial_actions)

        # Verify action was added
        assert "test_custom_action" in result_actions, "Custom action was not added"
        assert result_actions["test_custom_action"]["marker"] == "mock_plugin_was_here", \
            f"Action has wrong value: {result_actions['test_custom_action']}"

        print("✓ extend_actions() hook successfully adds custom action")


async def org_test__metaplugin_augment_folder_listing(oi):
    """
    Test that augment_folder_listing() hook can modify listing items.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_augment_listing.py"
        plugin_file.write_text(MOCK_PLUGIN_AUGMENT_LISTING)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Create test data
        test_items = [
            {"id": "item1", "name": "Item 1"},
            {"id": "item2", "name": "Item 2"},
        ]

        # Call augment_folder_listing hook
        result_items = await loader.call_augment_folder_listing_hooks(
            test_items,
            folder_context={},
            session=None
        )

        # Verify items were augmented
        assert len(result_items) == 2, "Wrong number of items returned"
        for item in result_items:
            assert "_test_marker" in item, f"Marker not added to item: {item}"
            assert item["_test_marker"] == "augmented_by_mock", f"Wrong marker value in item: {item}"

        print("✓ augment_folder_listing() hook successfully modifies items")


async def org_test__metaplugin_augment_listing_data(oi):
    """
    Test that augment_listing_data() hook can add custom data.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_augment_data.py"
        plugin_file.write_text(MOCK_PLUGIN_AUGMENT_DATA)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Create test data
        test_data = {"existing_key": "existing_value"}

        # Call augment_listing_data hook
        result_data = await loader.call_augment_listing_data_hooks(
            test_data,
            folder_context={},
            session=None
        )

        # Verify data was augmented
        assert "test_custom_key" in result_data, "Custom key not added"
        assert result_data["test_custom_key"] == "test_custom_value", "Wrong custom value"
        assert "plugin_active" in result_data, "plugin_active not added"
        assert result_data["plugin_active"] == "true", "Wrong plugin_active value"
        assert result_data["existing_key"] == "existing_value", "Existing key was lost"

        print("✓ augment_listing_data() hook successfully adds custom data")


async def org_test__metaplugin_exception_handling(oi):
    """
    Test that plugin loader handles exceptions in hooks gracefully.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_exception.py"
        plugin_file.write_text(MOCK_PLUGIN_EXCEPTION)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Call extend_actions - should not raise, just log error
        result_actions = loader.call_extend_actions_hooks({})

        # Should return the original dict unchanged
        assert result_actions == {}, "Actions dict should be unchanged after exception"

        print("✓ Plugin loader handles exceptions gracefully")


async def org_test__metaplugin_multiple_plugins(oi):
    """
    Test that loader can handle multiple plugins and call them in order.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write first plugin
        Path(temp_plugins_dir, "mock_first.py").write_text(MOCK_PLUGIN_FIRST)
        # Write second plugin (filename comes after first alphabetically)
        Path(temp_plugins_dir, "mock_second.py").write_text(MOCK_PLUGIN_SECOND)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        assert len(loader.loaded_plugins) == 2, f"Expected 2 plugins, got {len(loader.loaded_plugins)}"

        # Call extend_actions - should call both in order
        result_actions = loader.call_extend_actions_hooks({})

        # Verify both were called in alphabetical order (file order)
        assert "_call_order" in result_actions, "Call order not tracked"
        assert result_actions["_call_order"] == ["first", "second"], \
            f"Wrong call order: {result_actions['_call_order']}"

        print("✓ Multiple plugins loaded and called in correct order")


async def org_test__metaplugin_invalid_plugin(oi):
    """
    Test that loader handles invalid plugins gracefully.
    """
    from organizer.metaplugin import MetaPluginLoader

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write invalid plugin (missing Plugin class)
        Path(temp_plugins_dir, "invalid1.py").write_text(MOCK_PLUGIN_INVALID_MISSING_CLASS)

        # Write invalid plugin (missing PLUGIN_NAME)
        Path(temp_plugins_dir, "invalid2.py").write_text(MOCK_PLUGIN_INVALID_MISSING_NAME)

        # Write valid plugin
        Path(temp_plugins_dir, "valid.py").write_text(MOCK_PLUGIN_VALID)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Only valid plugin should be loaded
        assert len(loader.loaded_plugins) == 1, f"Expected 1 valid plugin, got {len(loader.loaded_plugins)}"
        assert loader.loaded_plugins[0].PLUGIN_NAME == "test_mock_valid"

        print("✓ Loader skips invalid plugins and continues loading valid ones")


async def org_test__metaplugin_sha256_demo_user_only(oi):
    """
    E2E test for the example SHA256 calculation metaplugin.

    Tests:
    - Plugin loads from example_metaplugins directory
    - Action only shows for 'demo' user (not other users)
    - User filtering logic works correctly
    """
    from pathlib import Path
    from organizer.metaplugin import MetaPluginLoader, OrganizerContext
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org
    from organizer.database.operations import db_get_or_create_user_root_folder
    from organizer.metaplugin.types import FolderContext

    # Get path to example_metaplugins directory
    organizer_dir = Path(__file__).parent.parent
    example_plugins_dir = organizer_dir / "example_metaplugins"

    if not example_plugins_dir.exists():
        print(f"⚠ Skipping test: example_metaplugins directory not found at {example_plugins_dir}")
        return

    sha256_plugin_file = example_plugins_dir / "calculate_sha256.py"
    if not sha256_plugin_file.exists():
        print(f"⚠ Skipping test: calculate_sha256.py not found at {sha256_plugin_file}")
        return

    # Load the plugin
    loader = MetaPluginLoader(str(example_plugins_dir), oi.log)
    loader.load_plugins()

    assert len(loader.loaded_plugins) > 0, "Should have loaded at least one plugin"

    # Find the SHA256 plugin
    sha256_plugin = None
    for plugin in loader.loaded_plugins:
        if plugin.PLUGIN_NAME == "calc_sha256":
            sha256_plugin = plugin
            break

    assert sha256_plugin is not None, "SHA256 plugin should be loaded"
    print(f"✓ Loaded plugin: {sha256_plugin.PLUGIN_NAME} v{sha256_plugin.PLUGIN_VERSION}")

    # Initialize the plugin with context
    ctx = OrganizerContext(
        db_session=oi.db_new_session,
        srv=oi.srv,
        log=oi.log,
        folders_helper=oi.folders_helper,
        pages_helper=oi.pages_helper,
    )
    await loader.call_on_init_hooks(ctx)

    # Test 1: Verify action is registered
    actions = {}
    actions = loader.call_extend_actions_hooks(actions)
    assert "calc_sha256" in actions, "calc_sha256 action should be registered"
    print("✓ Action registered")

    # Get a test media file
    media_files = await oi.srv.db_get_media_files(org.DbGetMediaFilesRequest(all=clap.Empty()))
    if len(media_files.items) == 0:
        print("⚠ No media files in test database, skipping file-based tests")
        return

    test_file = media_files.items[0]

    # Test 2: Verify action only shows for 'docker' user (the TARGET_USER_ID in the plugin)
    docker_session = org.UserSessionData(
        sid="test_docker",
        user=clap.UserInfo(id="docker", name="Docker User"),
        is_admin=False,
        cookies={}
    )

    other_session = org.UserSessionData(
        sid="test_other",
        user=clap.UserInfo(id="other_user", name="Other User"),
        is_admin=False,
        cookies={}
    )

    # Create users and get root folders for testing
    from organizer.database.models import DbUser

    with oi.db_new_session() as dbs:
        # Create the docker and other_user if they don't exist
        dbs.add(DbUser(id="docker", name="Docker User"))
        dbs.add(DbUser(id="other_user", name="Other User"))
        dbs.commit()

    with oi.db_new_session() as dbs:
        docker_root = await db_get_or_create_user_root_folder(
            dbs,
            clap.UserInfo(id="docker", name="Docker User"),
            oi.srv,
            oi.log
        )
        other_root = await db_get_or_create_user_root_folder(
            dbs,
            clap.UserInfo(id="other_user", name="Other User"),
            oi.srv,
            oi.log
        )
        dbs.commit()

    # Create mock listing items
    docker_items = [
        clap.PageItemFolderListingItem(
            media_file=test_file,  # test_file is already a MediaFile protobuf
            popup_actions=[]
        )
    ]

    other_items = [
        clap.PageItemFolderListingItem(
            media_file=test_file,
            popup_actions=[]
        )
    ]

    docker_folder_ctx = FolderContext(folder=docker_root, parent=None)
    other_folder_ctx = FolderContext(folder=other_root, parent=None)

    # Augment listings
    docker_items = await loader.call_augment_folder_listing_hooks(
        docker_items, docker_folder_ctx, docker_session
    )
    other_items = await loader.call_augment_folder_listing_hooks(
        other_items, other_folder_ctx, other_session
    )

    # Verify action only added for docker user
    assert "calc_sha256" in docker_items[0].popup_actions, \
        "SHA256 action should appear for 'docker' user"
    assert "calc_sha256" not in other_items[0].popup_actions, \
        "SHA256 action should NOT appear for non-docker users"

    print("✓ Action correctly filtered to 'docker' user only")

    # Test 3: Test command handling (non-docker user should be rejected)
    try:
        _handled = await loader.call_handle_custom_command_hooks(
            "calc_sha256",
            {"media_file_id": test_file.id},
            other_session,
            oi
        )
        assert False, "Non-docker user should have raised PermissionError"
    except PermissionError as e:
        assert "only available for user" in str(e).lower()
        print("✓ Non-docker user correctly rejected with PermissionError")

    # Note: We cannot easily test actual SHA256 calculation in this unit test without
    # setting up the full file system structure, but we've verified:
    # - Plugin loads correctly
    # - Action registration works
    # - User filtering works as expected
    # - Command routing works

    print("✓ All SHA256 metaplugin tests passed")


async def org_test__authz_plugin_allow(oi):
    """
    Test that metaplugin can grant access (returns True).
    """
    from organizer.metaplugin import MetaPluginLoader
    from organizer.database.models import DbFolder
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_authz_allow.py"
        plugin_file.write_text(MOCK_PLUGIN_AUTHZ_ALLOW)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        assert len(loader.loaded_plugins) == 1
        _plugin = loader.loaded_plugins[0]

        # Create a test folder and session
        test_folder = DbFolder(user_id="admin", title="Test")
        test_session = org.UserSessionData(
            sid="test",
            user=clap.UserInfo(id='testuser', name='Test User'),
            is_admin=False,
            cookies={}
        )

        # Call the authorization hook
        result = await loader.call_check_action_authorization_hooks(
            "test_action_allow",
            folder=test_folder,
            session=test_session
        )

        assert result is True, "Plugin should allow the action"
        print("✓ Plugin correctly returns True to allow action")


async def org_test__authz_plugin_deny(oi):
    """
    Test that metaplugin can deny access (returns False).
    """
    from organizer.metaplugin import MetaPluginLoader
    from organizer.database.models import DbFolder
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_authz_deny.py"
        plugin_file.write_text(MOCK_PLUGIN_AUTHZ_DENY)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        assert len(loader.loaded_plugins) == 1

        # Create a test folder and session
        test_folder = DbFolder(user_id="admin", title="Test")
        test_session = org.UserSessionData(
            sid="test",
            user=clap.UserInfo(id='testuser', name='Test User'),
            is_admin=False,
            cookies={}
        )

        # Call the authorization hook
        result = await loader.call_check_action_authorization_hooks(
            "test_action_deny",
            folder=test_folder,
            session=test_session
        )

        assert result is False, "Plugin should deny the action"
        print("✓ Plugin correctly returns False to deny action")


async def org_test__authz_plugin_defer(oi):
    """
    Test that metaplugin can defer to default checks (returns None).
    """
    from organizer.metaplugin import MetaPluginLoader
    from organizer.database.models import DbFolder
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin to temp directory
        plugin_file = Path(temp_plugins_dir) / "mock_authz_defer.py"
        plugin_file.write_text(MOCK_PLUGIN_AUTHZ_DEFER)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        assert len(loader.loaded_plugins) == 1

        # Create a test folder and session
        test_folder = DbFolder(user_id="admin", title="Test")
        test_session = org.UserSessionData(
            sid="test",
            user=clap.UserInfo(id='testuser', name='Test User'),
            is_admin=False,
            cookies={}
        )

        # Call the authorization hook
        result = await loader.call_check_action_authorization_hooks(
            "any_action",
            folder=test_folder,
            session=test_session
        )

        assert result is None, "Plugin should defer to default checks"
        print("✓ Plugin correctly returns None to defer to defaults")


async def org_test__authz_default_deny_non_owner(oi):
    """
    Test that default authorization check denies non-owner access.
    """
    from organizer.authz_methods import _check_action_authorization_default
    from organizer.database.models import DbFolder
    from grpclib import GRPCError
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    # Create a test folder owned by "admin"
    test_folder = DbFolder(user_id="admin", title="Test")

    # Create a session for a different user
    test_session = org.UserSessionData(
        sid="test",
        user=clap.UserInfo(id='regular_user', name='Regular User'),
        is_admin=False,
        cookies={}
    )

    # Attempt to rename - should be denied
    try:
        _check_action_authorization_default("rename_folder", test_folder, None, test_session)
        assert False, "Should have raised GRPCError"
    except GRPCError as e:
        assert "PERMISSION_DENIED" in str(e)
        print("✓ Default check correctly denies non-owner access")


async def org_test__authz_default_allow_owner(oi):
    """
    Test that default authorization check allows owner access.
    """
    from organizer.authz_methods import _check_action_authorization_default
    from organizer.database.models import DbFolder
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    # Create a test folder owned by "testuser"
    test_folder = DbFolder(user_id="testuser", title="Test")

    # Create a session for the same user
    test_session = org.UserSessionData(
        sid="test",
        user=clap.UserInfo(id='testuser', name='Test User'),
        is_admin=False,
        cookies={}
    )

    # Attempt to rename - should succeed
    _check_action_authorization_default("rename_folder", test_folder, None, test_session)
    print("✓ Default check correctly allows owner access")


async def org_test__authz_default_allow_admin(oi):
    """
    Test that default authorization check allows admin access to any folder.
    """
    from organizer.authz_methods import _check_action_authorization_default
    from organizer.database.models import DbFolder
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    # Create a test folder owned by someone else
    test_folder = DbFolder(user_id="other_user", title="Test")

    # Create a session for an admin user
    test_session = org.UserSessionData(
        sid="test",
        user=clap.UserInfo(id='admin_user', name='Admin User'),
        is_admin=True,
        cookies={}
    )

    # Attempt to rename - should succeed because user is admin
    _check_action_authorization_default("rename_folder", test_folder, None, test_session)
    print("✓ Default check correctly allows admin access")


async def org_test__authz_plugin_override_default(oi):
    """
    Test that plugin authorization can override default checks.
    """
    from organizer.metaplugin import MetaPluginLoader
    from organizer.database.models import DbFolder
    import clapshot_grpc.proto.clapshot as clap
    import clapshot_grpc.proto.clapshot.organizer as org

    with tempfile.TemporaryDirectory() as temp_plugins_dir:
        # Write mock plugin that allows specific users to rename any folder
        plugin_file = Path(temp_plugins_dir) / "mock_authz_folder_owner.py"
        plugin_file.write_text(MOCK_PLUGIN_AUTHZ_FOLDER_OWNER)

        loader = MetaPluginLoader(temp_plugins_dir, oi.log)
        loader.load_plugins()

        # Create a folder owned by "allowed_user" (the plugin will allow this)
        test_folder = DbFolder(user_id="allowed_user", title="Test")

        # Create a session for "someone_else"
        test_session = org.UserSessionData(
            sid="test",
            user=clap.UserInfo(id='someone_else', name='Someone Else'),
            is_admin=False,
            cookies={}
        )

        # Call the authorization hook
        result = await loader.call_check_action_authorization_hooks(
            "rename_folder",
            folder=test_folder,
            session=test_session
        )

        # The plugin checks if folder is owned by "allowed_user" and allows it
        # (normally someone_else wouldn't be allowed to rename allowed_user's folder)
        assert result is True, "Plugin should allow rename when folder is owned by allowed_user"
        print("✓ Plugin correctly overrides default authorization rules")
