# Python Metaplugins for Basic Folders Organizer

"Metaplugins" is a Basic Folder Organizer's internal mechanism that loads your custom .py files from a local directory and calls them at certain hook points.

This allows you to utilize the Basic Folders organizer, but add some specific custom functionalities in Python, without making a full Organizer or having to deal with gRPC calls from Clapshot Server.

This makes it relatively easy to implement simple customizations without too much boilerplate:

- **Add custom popup menu actions** to folders and media files
- **Modify folder listings** before they're shown to users (add/remove actions, change appearance)
- **Handle custom commands** from the client
- **Inject custom data** into the UI context
- **React to lifecycle events** (initialization, shutdown)

## Example Metaplugin

See `example_metaplugins/calculate_sha256.py` for a working example that demonstrates user-specific actions, file system access, and automated comment creation. This example is used by automated tests and included in the demo Docker image (via `make run-docker`).

## Development Setup

To develop metaplugins with full IDE type checking and `mypy` support:

1. **Clone the Basic Folders organizer repository**
   ```bash
   git clone <repo-url> clapshot-organizer-basic-folders
   cd clapshot-organizer-basic-folders
   ```

2. **Create a Python venv and install packages in editable mode**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows

   # Install the organizer and its dependencies in editable mode
   pip install -e .

   # Install the clapshot protobuf library (for type definitions)
   pip install -e /path/to/clapshot/protobuf

   # Optional: Install mypy for type checking
   pip install mypy
   ```

3. **Now your IDE should have type support**
   In your metaplugin, you can import and use the typed interfaces:
   ```python
   from organizer.metaplugin import OrganizerContext, FolderContext, MetaPluginInterface
   ```

   Your IDE will provide autocomplete and type checking for all metaplugin hooks.

## Configuration

Metaplugins are automatically discovered and loaded from a configurable directory when Basic Folders starts.
If `CLAPSHOT_METAPLUGINS_DIR` env variable is not set, it defaults to `/opt/clapshot-org-bf-metaplugins`.

All `.py` files in this directory will be automatically discovered and loaded at organizer startup. The organizer will continue running even if individual plugins fail to load.

## Plugin Structure

Each metaplugin is a Python file that must define a `Plugin` class with the following required attributes and optional hook methods:

**Required attributes:**
- `PLUGIN_NAME` (str): Unique plugin identifier (e.g., "my_plugin")
- `PLUGIN_VERSION` (str): Plugin version (e.g., "1.0.0")

**Optional hooks:**
All hooks are optional. Implement only the ones your plugin needs. Quick reference:

- `on_init(context)` - Initialization hook
- `on_shutdown()` - Cleanup hook
- `extend_actions(actions)` - Add popup menu actions
- `handle_custom_command(cmd, args, session, organizer)` - Handle custom commands
- `augment_folder_listing(items, folder_context, session)` - Modify folder listings
- `augment_listing_data(data, folder_context, session)` - Add custom data to UI
- `check_action_authorization(action, folder, media_file, session)` - Override permission checks

Refer to the complete interface definition for full type hints and documentation of `MetaPluginInterface`: [`organizer/metaplugin/types.py`](organizer/metaplugin/types.py). The [`calculate_sha256.py`](example_metaplugins/calculate_sha256.py) example metaplugin demonstrates further how to use the hooks.

## Extension Points (Hooks)

### 1. `on_init(context: OrganizerContext)`

Called once during organizer startup, after the server handshake is complete.

**Parameters:**
- `context` (OrganizerContext): Strongly typed context with organizer utilities (db_session, srv, log, folders_helper, pages_helper). See [`organizer/metaplugin/types.py`](organizer/metaplugin/types.py) for complete field documentation.

**Use cases:**
- Store references to organizer utilities
- Initialize database connections or external API clients
- Validate configuration

### 2. `on_shutdown()`

Called when the organizer is shutting down.

**Use cases:**
- Clean up resources
- Close connections
- Flush buffered data

### 3. `extend_actions(actions: dict) -> dict`

Called during user session startup to define available actions.

**Parameters:**
- `actions` (dict): Map of action names to `ActionDef` protobuf objects

**Returns:**
- Modified `actions` dictionary

**Use cases:**
- Add new popup menu actions
- Modify or remove existing actions

**Important: JavaScript Naming Conventions**

When writing JavaScript code in actions, protobuf fields use **camelCase**, not snake_case:
- ✅ `item.mediaFile.id` (correct)
- ❌ `item.media_file.id` (wrong - will be undefined)

For complete implementation details, see the `extend_actions` hook in [`example_metaplugins/calculate_sha256.py`](example_metaplugins/calculate_sha256.py).

### 4. `handle_custom_command(cmd, args, session, organizer) -> bool`

Called when the client sends an organizer command. Plugins are tried in order before built-in commands.

**Parameters:**
- `cmd` (str): Command name (e.g., "my_custom_command")
- `args` (dict): Parsed JSON arguments from client
- `session`: UserSessionData protobuf with `user`, `is_admin`, `cookies`, etc.
- `organizer`: The OrganizerInbound instance

**Returns:**
- `True` if the command was handled by this plugin
- `False` to pass through to other plugins or built-in handlers

**Use cases:**
- Implement custom command logic
- Perform database operations
- Call external APIs
- Refresh the UI after operations

For a working implementation example, see the `handle_custom_command` hook in [`example_metaplugins/calculate_sha256.py`](example_metaplugins/calculate_sha256.py).

### 5. `augment_folder_listing(listing_items, folder_context: FolderContext, session) -> list`

Called when constructing a folder listing page, after items are prepared but before sending to the client.

**Parameters:**
- `listing_items` (list): List of folder listing items, each with either `folder` or `media_file` property
- `folder_context` (FolderContext): Contains `folder` (current DbFolder) and `parent` (parent DbFolder or None). See [`organizer/metaplugin/types.py`](organizer/metaplugin/types.py) for details.
- `session`: UserSessionData with user info and admin flag

**Returns:**
- Modified `listing_items` list

**Use cases:**
- Add custom actions to specific items based on conditions
- Change item appearance (colors, icons)
- Remove actions based on permissions
- Modify behavior based on folder or user context

For a working implementation example, see the `augment_folder_listing` hook in [`example_metaplugins/calculate_sha256.py`](example_metaplugins/calculate_sha256.py).

### 6. `augment_listing_data(listing_data, folder_context: FolderContext, session) -> dict`

Called when constructing a folder listing, to add custom data that will be passed to JavaScript actions.

**Parameters:**
- `listing_data` (dict): String-keyed dictionary passed to client JavaScript
- `folder_context` (FolderContext): Contains `folder` (current DbFolder) and `parent` (parent DbFolder or None)
- `session`: UserSessionData

**Returns:**
- Modified `listing_data` dictionary

**Use cases:**
- Pass configuration to client-side JavaScript
- Include computed values for action scripts
- Store plugin state for callbacks

### 7. `check_action_authorization(action, folder, media_file, session) -> bool | None`

Override authorization checks for folder/file operations (rename, trash, move, etc.).

Called before operations like `rename_folder`, `trash_folder`, `move_to_folder`, etc.
Allows metaplugins to implement custom authorization rules (LDAP, service accounts, external APIs, etc.).

**Parameters:**
- `action` (str): Operation name (e.g., "rename_folder", "trash_folder", "move_item")
- `folder` (DbFolder | None): The folder being acted upon (for folder operations)
- `media_file` (DbMediaFile | None): The media file being acted upon (for file operations)
- `session` (UserSessionData): User info, is_admin flag, etc.

**Returns:**
- `True` to allow the operation
- `False` to deny the operation (raises PERMISSION_DENIED to client)
- `None` to use the default permission check (backward compatible)

**Default behavior (when all plugins return `None`):**
- Allows owner or admin access
- Denies non-owner access to other users' folders/files

**Use cases:**
- Allow LDAP group members to access folders they don't own
- Grant special permissions for service accounts
- Implement organization-specific authorization rules
- Query external APIs (e.g., LDAP) to make authorization decisions

**Pattern:**
- Return `True` to explicitly allow
- Return `False` to explicitly deny (raises PERMISSION_DENIED)
- Return `None` to defer to default checks

This hook is useful for external authorization systems where you want to override the built-in ownership-based rules.

## Complete Working Example

See `example_metaplugins/calculate_sha256.py` for a fully-featured, tested metaplugin example that demonstrates:

- **Multiple hooks**: `on_init`, `extend_actions`, `augment_folder_listing`, `handle_custom_command`
- **User-specific filtering**: Actions only shown for specific users
- **Background processing**: Asynchronous operations with thread pools
- **File system access**: Reading original media files from disk
- **gRPC callbacks**: Adding comments via server calls
- **Progress reporting**: Real-time push notifications to clients
- **Error handling**: Proper exception handling and logging

The example is used by:
- **Automated tests**: Verified in the test suite (`org_test__metaplugin_sha256_demo_user_only`)
- **Demo Docker image**: Automatically included when running `make run-docker`

To use it, the example directory is automatically mounted into the Docker container. For local development, set:
```bash
export CLAPSHOT_METAPLUGINS_DIR="$(pwd)/organizer/basic_folders/example_metaplugins"
```

## Error Handling

- Plugin loading errors are logged but don't prevent organizer startup
- Plugin hook exceptions are caught and logged
- If a plugin raises an exception in `handle_custom_command`, it propagates to the client
- If a plugin raises an exception in other hooks, it's logged and other plugins continue

## Best Practices

1. **Minimal interface**: Only implement hooks you need (others can be omitted)
2. **Error handling**: Catch and log exceptions in your plugin code
3. **Performance**: Keep hook implementations fast; they run on the request path
4. **Database transactions**: See the working example in `calculate_sha256.py` for correct database session patterns. Keep transactions short to avoid DB lock contention.
5. **Logging**: Use `self.log` for debugging and troubleshooting

## Debugging

Enable debug logging to see plugin activity:

```bash
python -m organizer.main --debug /tmp/organizer.sock
```

Plugin loading and hook calls will be logged with details about successes and failures.

## Migration and Compatibility

Metaplugins are a new feature and the interface may evolve. When upgrading Basic Folders Organizer, test that your Metaplugins still work (and perhaps run `mypy` on them).

## License

Metaplugins you create are your own code and can use any license. The Basic Folders organizer is MIT licensed to enable this flexibility.
