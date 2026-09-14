from _mcplib import run, a

run("mcp.fs_move_file", "mcp-server-filesystem", ["/app"], "move_file", {"source":a(1), "destination":a(2)})
