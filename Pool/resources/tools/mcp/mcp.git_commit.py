from _mcplib import run, a

repo=a(1,"/app")
run("mcp.git_commit", "python", ["-m","mcp_server_git","--repository",repo], "git_commit", {"repo_path":a(1), "message":a(2)})
