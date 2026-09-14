from _mcplib import run, a

run("mcp.fs_write_file", "mcp-server-filesystem", ["/app"], "write_file", {"path":a(1), "content":a(2)})
