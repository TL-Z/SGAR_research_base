from _mcplib import run, a

run("mcp.fs_read_file", "mcp-server-filesystem", ["/app"], "read_text_file", {"path":a(1)})
