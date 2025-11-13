# Example Metaplugins

Example metaplugins in this directory are used by:
- **Demo Docker image**: When running `make run-docker` from the project root, this directory is mounted into the container at `/opt/clapshot-org-bf-metaplugins/`
- **Automated tests**: Test framework imports and tests these plugins via `organizer/test_metaplubings.py`

For metaplugin documentation, see [METAPLUGINS.md](../METAPLUGINS.md). The example plugin code should demonstrate many practical usage patterns though.
