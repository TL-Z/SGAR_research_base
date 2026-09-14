from _mcplib import run, a

run("mcp.fs_create_directory", "mcp-server-filesystem", ["/app"], "create_directory", {"path":a(1)})
