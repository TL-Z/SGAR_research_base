from _mcplib import run, a

repo=a(1,"/app")
run("mcp.git_add", "python", ["-m","mcp_server_git","--repository",repo], "git_add", {"repo_path":a(1), "files":[x for x in a(2,"").split(",") if x]})
