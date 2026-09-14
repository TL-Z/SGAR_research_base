from _mcplib import run, a

run("mcp.fs_edit_file", "mcp-server-filesystem", ["/app"], "edit_file", {"path":a(1), "edits":[{"oldText":a(2),"newText":a(3)}]})
