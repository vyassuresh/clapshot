# Clapshot Feature Overview

Clapshot is a self-hosted video/media review and annotation platform designed for collaborative content review workflows. This feature listing provides end-users and system administrators with an overview of Clapshot's capabilities.

## Web Client UX

### **Video Player**
Video player with playback controls and navigation features.
  - **Loop Region Control**: Set custom loop in/out points with 'i'/'o' keyboard shortcuts and visual timeline indicators
  - **Frame-by-Frame Navigation**: Frame stepping with arrow keys
  - **Keyboard Shortcuts**: Control system (spacebar play/pause, 'l' toggle loop, 'z'/'y' undo/redo)
  - **SMPTE Timecode Display**: Timecode format with editable timecode fields for seeking
  - **Audio Waveform Visualization**: Waveform display for audio files with click-to-seek
  - **Volume Control**: Volume settings saved to local storage with slider control
- *References: [src1](client/src/lib/player_view/VideoPlayer.svelte), [src2](client/src/lib/player_view/CommentTimelinePin.svelte), [src3](client/src/lib/asset_browser/ScrubbableVideoThumb.svelte)*

### **Comment System**
Time-coded commenting with threaded discussions and timeline integration.
  - **Threaded Conversations**: Reply system with visual hierarchy and indentation
  - **Inline Editing**: Comment editing with keyboard shortcuts (Enter to save, Escape to cancel)
  - **Admin Moderation**: Admin users can edit/delete any comments; users can only modify their own
  - **Timeline Integration**: Comments appear as clickable pins on video timeline with color-coded user identification
  - **Auto-Loop Comments**: Loop setting between consecutive comment timestamps
  - **Comment Navigation**: Scroll-to and highlighting when navigating between comments
- *References: [src1](client/src/lib/player_view/CommentCard.svelte), [src2](client/src/lib/player_view/CommentInput.svelte), [src3](client/src/lib/player_view/CommentTimelinePin.svelte)*

### **Drawing Annotations**
Drawing system with collaborative support and basic editing tools.
  - **7-Color Palette**: Color selection (red, green, blue, cyan, yellow, black, white) with visual picker
  - **Undo/Redo System**: Drawing operation history with keyboard shortcuts (Ctrl+Z/Ctrl+Y)
  - **Auto-Pause Drawing**: Video pause when entering drawing mode
  - **Real-Time Sync**: Drawing operations synchronized across collaborative session participants
  - **Storage**: Drawings saved as WebP images and linked to timestamps
- *References: [src1](client/src/lib/player_view/VideoPlayer.svelte), [src2](client/src/lib/player_view/VideoFrame.ts), [src3](client/src/lib/player_view/CommentInput.svelte)*

### **File Upload and Management**
File handling with progress tracking and thumbnail generation.
  - **Progress Tracking**: Upload progress with percentage indicators and chunked transfer support
  - **Drag-and-Drop Upload**: Visual feedback for drag-active state with multi-file support
  - **Thumbnail Previews**: Hover-to-scrub functionality for fast previews
  - **Format Validation**: Client-side validation for video/*, image/*, audio/* file types
  - **Media Type Indicators**: FontAwesome icons for video, audio, image, and unknown types
- *References: [src1](client/src/lib/asset_browser/FileUpload.svelte), [src2](client/src/lib/asset_browser/ScrubbableVideoThumb.svelte)*

### **Action System**
Context-sensitive action framework with popup menus and scripting capabilities.
  - **Popup Menus**: Context-aware actions based on item type, permissions, and sharing status
  - **JavaScript Action Scripting**: Client-side scripting capabilities for custom workflows
  - **Action Visibility**: Actions appear/disappear based on folder context
  - **Keyboard Shortcuts**: Action definitions include keyboard shortcut support
  - **Icons and Styling**: FontAwesome integration with custom colors
  - **Permission-Aware Actions**: Actions filtered based on user permissions and ownership
- *References: [src1](client/src/lib/asset_browser/PopupMenu.svelte), [src2](organizer/basic_folders/organizer/helpers/actiondefs.py)*

### **Navigation and Breadcrumbs**
Navigation system with cross-user capabilities and visual context indicators.
  - **Cross-User Navigation**: Admin users can navigate between different users' folders
  - **Shared Folder Indicators**: Visual cues (🔗 icons) showing when viewing shared content
  - **Ownership Context**: Breadcrumb behavior when crossing ownership boundaries
  - **Home Folder Detection**: "Home" labeling for user root folders
  - **Secure Navigation**: XSS-safe clickable breadcrumb navigation with HTML escaping
- *References: [src1](organizer/basic_folders/organizer/helpers/pages.py), [src2](client/src/lib/NavBar.svelte)*

### **Real-Time Collaboration**
Shared viewing sessions with synchronized playback, seeking, and annotation across multiple users. Generate shareable links for collaborative review sessions.
- *References: [src1](client/src/lib/player_view/VideoPlayer.svelte), [src2](server/src/api_server/ws_handers.rs)*

### **Subtitle Track Management**
Upload, and synchronize multiple subtitle files.
- *References: [src1](client/src/lib/player_view/SubtitleCard.svelte), [src2](server/migrations/2024-06-02-173200_add_subtitles/)*

### **EDL Import**
Import Edit Decision Lists as time-coded comments for professional video editing workflows.
- *References: [src1](client/src/lib/tools/EDLImport.svelte), [src2](server/src/tests/assets/red-lettuce.edl)*

## Media Processing

### **Media Upload and Ingestion**
Upload videos, audio files, and images through the web interface or folder monitoring.
  - **Web Upload**: Browser-based file uploads with progress tracking and chunked transfer
  - **Monitored Folder**: Processing of files dropped into the incoming directory
    - Files are assigned to users based on OS file ownership
    - Polling with write-completion detection
    - User creation based on file ownership
- *References: [README.md](README.md), [doc/sysadmin-guide.md](doc/sysadmin-guide.md), [src1](server/src/video_pipeline/incoming_monitor.rs), [src2](client/src/lib/asset_browser/FileUpload.svelte)*

### **Media Processing**
FFmpeg-based transcoding for browser compatibility with thumbnail generation and metadata extraction.
- *References: [src1](server/src/video_pipeline/ffmpeg_processor.rs), [src2](server/src/video_pipeline/metadata_reader.rs)*

### **Multi-Format Support**
Support for video, audio, and image files with automatic format detection and conversion.
- *References: [README.md](README.md), [src1](server/src/tests/assets/)*

### **Multi-Threaded Processing**
Rust-based server with concurrent processing for media operations.
- *References: [src1](server/src/video_pipeline/), [src2](server/Cargo.toml)*

### **Configurable Transcoding**
Bitrate transcoding with customizable quality settings.
- *References: [src1](server/src/video_pipeline/ffmpeg_processor.rs)*

### **File Storage**
Organized file system layout with cleanup and storage management.
- *References: [src1](server/src/video_pipeline/cleanup_rejected.rs)*

## Organizer Plugin(s)

### **Folder Management System**
File organization with hierarchical folders and basic administrative controls.
  - **Hierarchical Organization**: Create nested folder structures with visual previews
  - **Multi-Select Operations**: Selection with Shift (range) and Ctrl/Cmd (individual) support
  - **Keyboard Navigation**: Keyboard support with Enter to open, Space for operations
  - **Folder Previews**: Thumbnails showing up to 4 preview items per folder
  - **Drag-and-Drop**: Multi-item drag with visual feedback and nested folder support
  - **Loop Detection**: Detection and repair of circular folder references
  - **Orphaned Content Recovery**: Cleanup and re-assignment of dangling media references
- *References: [organizer/basic_folders/README.md](organizer/basic_folders/README.md), [src1](client/src/lib/asset_browser/FolderListing.svelte), [src2](organizer/basic_folders/organizer/database/operations.py)*

### **Folder Sharing**
Sharing system with security controls and access management.
  - **Token Generation**: 32-byte random tokens for shared access (Note: does NOT bypass authentication for anonymous access)
  - **Share Revocation**: Ability to revoke shared folder access
  - **Visual Share Indicators**: 🔗 icons in breadcrumbs and folder listings show shared status
  - **Access Control**: Verification of shared folder subtree permissions
  - **Cookie-Based Sessions**: Access tracking for shared folder sessions
  - **Cleanup**: Share tokens cleaned up when folders are deleted
- *References: [src1](organizer/basic_folders/organizer/folder_op_methods.py), [src2](organizer/basic_folders/organizer/helpers/folders.py)*

### **Drag-and-Drop Interface**
File management with drag-and-drop operations for organizing content.
- *References: [src1](client/src/lib/asset_browser/FolderListing.svelte), [src2](client/src/__tests__/lib/asset_browser/FolderListingDragDrop.unit.test.ts)*

### **Plugin Architecture ("Organizer")**
Extensible organizer plugin system using gRPC for custom workflows and integrations (custom development required for LDAP, project management systems, and external databases).
- *References: [doc/organizer-plugins.md](doc/organizer-plugins.md), [protobuf/proto/organizer.proto](protobuf/proto/organizer.proto)*

### **Multi-Language gRPC Libraries for Organizers**
Support for plugins in Python, Rust, and TypeScript with gRPC bindings.
- *References: [protobuf/libs/](protobuf/libs/), [organizer/basic_folders/](organizer/basic_folders/)*

### **Custom UI Integration**
Plugin system supports custom HTML and JavaScript for tailored user interfaces.
- *References: [doc/organizer-plugins.md](doc/organizer-plugins.md), [src1](organizer/basic_folders/organizer/helpers/pages.py)*

## Administration

### **User Management and Access Control**
User administration with permission management and lifecycle controls.
  - **Admin Folder View**: Admin interface showing all user home folders with management capabilities
  - **User Cleanup System**: Detection and removal of empty users with batch operations
  - **Ownership Transfer**: User ownership change when moving content between user folders
  - **Permission Boundaries**: Prevention of unauthorized cross-user operations
  - **User Lifecycle Management**: User deletion that preserves comments via database triggers
  - **Cross-User Navigation**: Admin users can navigate and manage any user's content
  - **User Indicators**: User icons and styling in admin folder views
- *References: [src1](server/migrations/2024-05-13-093800_add_users_table/), [src2](organizer/basic_folders/organizer/user_session_methods.py), [src3](organizer/basic_folders/organizer/helpers/pages.py), [src4](organizer/basic_folders/organizer/folder_op_methods.py)*

### **Authentication-Agnostic Design**
Works with authentication systems through reverse proxy integration (requires proxy configuration for OAuth, LDAP, Kerberos, SAML, etc.).
- *References: [doc/sysadmin-guide.md](doc/sysadmin-guide.md), [clapshot+htadmin.nginx.conf](client/debian/additional_files/clapshot+htadmin.nginx.conf)*

### **Secure File Sharing**
Token-based sharing system for controlled access to media files and folders.
- *References: [src1](organizer/basic_folders/organizer/folder_op_methods.py)*

### **Docker Deployment**
Pre-configured Docker images for easy deployment with multiple authentication options.
- *References: [README.md](README.md), [Dockerfile.demo](Dockerfile.demo), [test/run-cloudflare.sh](test/run-cloudflare.sh)*

### **Debian Package Installation**
Native Debian packages for production deployment with systemd integration.
- *References: [doc/sysadmin-guide.md](doc/sysadmin-guide.md), [Makefile](Makefile), [server/debian/](server/debian/)*

### **Nginx Reverse Proxy Examples**
Complete Nginx configuration examples for HTTPS, authentication, and static file serving.
- *References: [doc/sysadmin-guide.md](doc/sysadmin-guide.md), [clapshot.nginx.conf](client/debian/additional_files/clapshot.nginx.conf)*

### **Database Management**
SQLite-based storage with integrity monitoring and maintenance capabilities.
  - **Migrations**: Dependency-aware migration system with version-based ordering
  - **Integrity Monitoring**: Loop detection and repair of circular folder references
  - **Orphaned Reference Cleanup**: Removal of dangling folder items and media references
  - **Foreign Key Constraints**: Constraint handling during schema evolution
  - **Post-Migration Verification**: Verification of database schema mappings and data consistency
  - **Root Folder Management**: Creation and maintenance of user root folders with orphan adoption
- *References: [src1](server/src/database/migration_solver.rs), [src2](server/src/database/db_backup.rs), [src3](organizer/basic_folders/organizer/database/operations.py), [src4](organizer/basic_folders/organizer/database/migrations.py)*

### **Health Monitoring**
Built-in health check endpoint and comprehensive logging for system monitoring.
- *References: [doc/sysadmin-guide.md](doc/sysadmin-guide.md), [src1](server/src/api_server/mod.rs)*

## Development

### **Test Suite**
Test coverage with testing infrastructure across components.
  - **Client-Side Testing**: Unit tests, integration tests, and end-to-end testing with Vitest framework
  - **Server-Side Testing**: Rust-based unit and integration tests with asset-based testing
  - **Plugin Testing Framework**: Organizer plugin testing with test discovery
  - **Test Execution**: Output capture and error handling for individual test components
  - **gRPC Method Testing**: Testing of organizer protocol methods and integration points
  - **Permission Testing**: Verification of access control and authorization mechanisms
- *References: [doc/TESTING_GUIDE.md](doc/TESTING_GUIDE.md), [src1](client/src/__tests__/), [src2](server/src/tests/), [src3](organizer/basic_folders/organizer/testing_methods.py)*

### **Development Environment**
Complete development setup with hot reloading and debugging support.
- *References: [doc/development-setup.md](doc/development-setup.md), [src1](client/vite.config.ts)*

### **API Documentation**
Comprehensive protocol buffer definitions and API documentation for extension development.
- *References: [protobuf/README.md](protobuf/README.md), [protobuf/proto/](protobuf/proto/)*

---

*For detailed installation and configuration instructions, see the [Sysadmin Guide](doc/sysadmin-guide.md) and [README.md](README.md). For development information, consult the [Development Setup Guide](doc/development-setup.md).*