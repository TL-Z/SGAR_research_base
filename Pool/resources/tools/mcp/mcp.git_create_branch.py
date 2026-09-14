from _mcplib import run, a

repo=a(1,"/app")
run("mcp.git_create_branch", "python", ["-m","mcp_server_git","--repository",repo], "git_create_branch", {"repo_path":a(1), "branch_name":a(2)})
