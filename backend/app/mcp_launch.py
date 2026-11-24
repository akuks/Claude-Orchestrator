"""Start a connected tool (stdio MCP server) from a private, empty folder.

Run as ``<server python> -I mcp_launch.py <neutral_dir> -- <command> [args...]``.
It changes into ``neutral_dir``, sets ``PWD`` to it, drops ``INIT_CWD`` and
``OLDPWD`` (which would still name the project folder), and replaces itself
with ``command`` (looked up on PATH as usual). Nothing else in the environment
changes, and it never opens anything in the project.

Stdlib only, and run with ``-I``, so nothing from the working folder, the user
site or PYTHON* variables is imported. On any failure it prints one plain line
to stderr and exits 127.
"""

import os
import sys

_USAGE = "mcp_launch: usage: mcp_launch.py <folder> -- <command> [args...]"


def _fail(message: str):
    try:
        sys.stderr.write(message.replace("\n", " ") + "\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - never a traceback
        pass
    os._exit(127)


def main(argv):
    """``argv`` is everything after the script name. Never returns."""
    argv = list(argv)
    if len(argv) < 3 or argv[1] != "--" or not argv[0] or not argv[2]:
        _fail(_USAGE)
    folder, command, args = argv[0], argv[2], argv[3:]
    try:
        os.chdir(folder)  # fails when the folder is missing or not a directory
    except OSError as e:
        _fail(f"mcp_launch: cannot start in {folder}: {e.strerror or e}")
    os.environ["PWD"] = folder
    os.environ.pop("INIT_CWD", None)
    os.environ.pop("OLDPWD", None)
    try:
        os.execvp(command, [command, *args])
    except OSError as e:
        _fail(f"mcp_launch: cannot run {command}: {e.strerror or e}")
    _fail(f"mcp_launch: cannot run {command}")  # execvp does not return


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException:  # noqa: BLE001 - one line and 127, never a traceback
        _fail("mcp_launch: failed to start the tool")
