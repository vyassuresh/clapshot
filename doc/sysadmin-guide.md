# Clapshot Sysadmin Guide

> **New to Clapshot?** Start with the [Quick Start Reference](quick-start-reference.md) for common deployment scenarios.
> **Having connection issues?** See the [Connection Troubleshooting Guide](connection-troubleshooting.md) for help with common deployment and connectivity problems.

### Building

I recommend building Clapshot using Docker for a clean environment:

1. Install and start Docker.
2. Run `make debian-docker` at the project root to build Debian packages.

For more manual approaches, see [[Development Setup]].

### Running unit and integration tests

Execute `make test` at the project root to run all tests in a Docker container. For server-specific tests, use `make test-local` within the `server` directory.

### How it operates

The server starts with command `clapshot-server`. It stays in foreground, and should therefore be started by a process manager like *systemd*.

Preferred deployment and upgrade method is to install server and client as Debian packages. Whereas `clapshot-server` is a foreground binary that is configured with command line options,
the Debian package contains a systemd service file that demonizes it, and config file `/etc/clapshot-server.conf` that is translated into the appropriate CLI options automatically.

Server should be put behind a reverse proxy in production, but
can be developed and tested without one. The client .deb package contains an example Nginx config file (`/usr/share/doc/clapshot-client/examples/`) that

 1. reverse proxies the server API (websocket),
 2. serves out frontend files (.html .js .css),
 3. serves uploaded video files from `videos/` directory, and
 4. contains examples on how to add HTTPS and authentication

While the server uses mostly Websocket, there's a `/api/health` endpoint that can be used for monitoring. It returns 200 OK if the server is running.

### Database upgrades

Running a new version of Clapshot Server (and/or Organizer) will often upgrade database schemas on first start.
See [upgrading.md](upgrading.md) for details about upgrading.

### Advanced Authentication

Clapshot server itself contains no authentication code. Instead, it trusts
HTTP server (reverse proxy) to take care of that and to pass authenticated user ID
and username in request headers. This is exactly what the basic auth / htadmin demo
above does, too:

 - `X-Remote-User-Id` / `X_Remote_User_Id` / `HTTP_X_REMOTE_USER_ID` – Authenticated user's ID (e.g. "alice.brown")
 - `X-Remote-User-Name` / `X_Remote_User_Name` / `HTTP_X_REMOTE_USER_NAME` – Display name for user (e.g. "Alice Brown")
 - `X-Remote-User-Is-Admin` / `X_Remote_User_Is_Admin` / `HTTP_X_REMOTE_USER_IS_ADMIN` – If set to "1" or "true", user is a Clapshot admin

Most modern real-world deployments will likely use some more advanced authentication mechanism, such as OAuth, Kerberos etc, but htadmin is a good starting point.

See [clapshot+htadmin.nginx.conf](client/debian/additional_files/clapshot+htadmin.nginx.conf) (Nginx config example) and [Dockerfile.demo](Dockerfile.demo) +
[docker-entry_htadmin.sh](test/docker-entry_htadmin.sh) for details on how the integration works.

Authorization is also supposed to be handled on web server, at least for now.
See for example https://github.com/elonen/ldap_authz_proxy on how to authorize users against Active Directory/LDAP groups using Nginx. I wrote it to complement Nginx spnego authn, which uses Kerberos and thus doesn't really have a concept of groups.
If you want to use Kerberos, you may also want to check out https://github.com/elonen/debian-nginx-spnego
for .deb packages.

There are currently no demos for any of these more advanced auths (`vouch-proxy` example for Okta, Google etc. would be especially welcome, if you want to contribute!).

### Monitored Folder Ingestion

Clapshot automatically processes media files dropped into the monitored incoming folder. This system enables batch uploads and integration with external tools or workflows.

#### Configuration

The incoming folder monitoring is configured via command-line options:

- `--data-dir <path>` - Base directory containing the `incoming/` subdirectory (default: current directory)
- `--poll <seconds>` - Polling interval for checking new files (default: 3.0 seconds)
- `--workers <count>` - Number of parallel workers for media processing (default: CPU core count)
- `--bitrate <mbps>` - Target maximum bitrate for transcoding (default: 2.5 Mbps)

#### Directory Structure

```
<data_dir>/
├── incoming/          # Drop files here for automatic processing
├── videos/           # Processed media storage (organized by media ID)
├── rejected/         # Files that failed processing
└── upload/           # Temporary storage for web uploads
```

#### How It Works

1. **File Detection**: The system continuously polls `<data_dir>/incoming/` for new files
2. **Write Completion**: Waits for file size to stabilize between polls to ensure upload completion
3. **Owner Resolution**: Uses OS-level file ownership to determine the Clapshot user
4. **User Assignment**: Files are assigned to users based on their OS username (e.g., file owned by `alice` becomes owned by Clapshot user `alice`)
5. **Processing**: Files are moved to permanent storage, transcoded if needed, and added to the user's media library

#### Important Notes

- **OS Username Mapping**: The system directly maps OS file owners to Clapshot user IDs with no translation layer. Note that for web uploads, Clapshot server trusts the reverse proxy's HTTP headers for both username and display name, so any user mappings should happen at the proxy level.
- **User Auto-Creation**: If a user doesn't exist in the database, they are automatically created when their first file is processed
- **No Subdirectories**: Only files in the top-level `incoming/` directory are processed (subdirectories are ignored)
- **Error Handling**: Files that fail processing are moved to the `rejected/` directory with error details
- **Duplicate Prevention**: The system prevents re-processing of identical files using content-based hashing

#### Security Considerations

Since user assignment is based on file system ownership, ensure that:
- File system permissions align with your desired user access model
- OS usernames match your intended Clapshot user identifiers
- The incoming directory has appropriate write permissions for authorized users
- Consider using group ownership and umask settings for shared environments
