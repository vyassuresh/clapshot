# "Basic Folders" Organizer plugin for Clapshot

Extends Clapshot Server's simple media file listing into a folder-based organization system with sharing capabilities.
Also serves as an example implementation and a test bench for the Clapshot Organizer API.

Written in Python (due to popularity), but Organizers can be implemented in any language that supports gRPC.

## Core Features

### Personal Media Organization
- **Hierarchical folder structure**: Create nested folders to organize media files
- **Auto-created home folders**: Each user gets a personal "Home" folder automatically
- **Mixed content support**: Folders can contain videos, audio, images, and subfolders
- **Drag-and-drop interface**: Intuitive moving of files and folders between locations
- **Visual navigation**: Breadcrumb trails and thumbnail previews
- **Custom sorting**: Rearrange folder contents in your preferred order
- **Orphan media management**: Unorganized files are automatically placed in your home folder

### Folder Sharing
- **Secure token-based sharing**: 32-byte cryptographically secure random tokens for shared access
- **Authentication required**: Shared folders still require user authentication (does NOT bypass authentication for anonymous access)
- **Share revocation**: Ability to revoke shared folder access at any time
- **Recursive sharing**: Sharing includes all subfolders and content within the shared folder
- **Owner controls**: Only folder owners can create or revoke sharing access
- **Visual indicators**: Shared folders display with 🔗 link icons in breadcrumbs and folder listings
- **Cookie-based sessions**: Persistent access tracking for shared folder sessions
- **Automatic cleanup**: Share tokens automatically cleaned up when folders are deleted

### Administrative Tools
- **Admin folder view**: Special interface showing all user home folders with management capabilities
- **User cleanup system**: Batch detection and removal of empty users with safety checks
- **Cross-user operations**: Move content between any users' folders with admin navigation
- **Ownership transfer**: Content ownership transfers automatically when moved between users
- **User lifecycle management**: Safe user deletion that preserves comments via database triggers
- **Database integrity checks**: Automatic detection and repair of circular folder references and orphaned content

### Interactive User Interface
- **Context menus**: Right-click popup menus for folder and file operations
- **Multi-select support**: Shift-click to select multiple items for batch operations
- **Upload integration**: Upload files directly into specific folders
- **Responsive design**: Works seamlessly across different screen sizes

### Extensibility with Metaplugins
- **Custom actions**: Add new popup menu actions for specific workflows without modifying core code
- **Conditional UI**: Show/hide actions based on user, folder, or file properties
- **Authorization overrides**: Implement custom permission checks (LDAP, service accounts, external APIs)
- **External integrations**: Connect to external APIs, databases, or services
- **Custom workflows**: Implement organization-specific business logic (approval flows, archiving, etc.)
- **Visual customization**: Modify item appearance (colors, icons) based on metadata or state
- **See [METAPLUGINS.md](METAPLUGINS.md)** for full documentation and examples

## Technical Implementation

The plugin extends Clapshot with three additional database tables:

- **`bf_folders`**: Stores folder metadata (ID, title, owner, creation time)
- **`bf_folder_items`**: Junction table linking folders to contents (media files or subfolders)
- **`bf_shared_folders`**: Manages folder sharing (tokens, permissions, creation info)

All tables use foreign keys to maintain referential integrity with Clapshot Server's `videos` and `users` tables.

### Integration Features
- **gRPC communication**: Bidirectional communication with Clapshot Server
- **Client-side scripting**: Injects JavaScript for interactive UI behaviors
- **Database sharing**: Uses server's SQLite database with additional plugin tables
- **Session management**: Integrates with server's user authentication system
- **Callback system**: Registers custom actions for client-side folder operations
- **Metaplugin system**: Extension hooks for loading custom Python modules at runtime
- **Authorization framework**: Pluggable permission checks allowing custom rules via metaplugins

### Security & Robustness
- **Permission validation**: All operations verify user ownership and access rights
- **Transaction safety**: Database transactions ensure data consistency
- **Error recovery**: Graceful handling of database inconsistencies with automatic repair
- **Path traversal protection**: Prevents unauthorized access to folders outside shared trees
- **Loop detection**: Prevents infinite folder loops that could break navigation

This demonstrates how you can create arbitrary UI folder hierarchies, inject custom HTML + JS code to the Client's navigation window, and how to extend the database schema to store custom plugin data.

## License (MIT)

Released under the **MIT license**, unlike the main server (which is GPLv2) so you can use this as a basis for custom, proprietary extensions.
