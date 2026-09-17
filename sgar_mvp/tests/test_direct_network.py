from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sgar_mvp.src.direct_network import (
    direct_container_environment_args,
    load_tool_proxy_config,
    tool_proxy_audit,
    tool_proxy_environment,
)


class DirectNetworkTests(unittest.TestCase):
    def test_project_proxy_is_scoped_to_declared_network_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "SGAR_TOOL_HTTP_PROXY=http://user:secret@example.test:8080\n"
                "SGAR_TOOL_HTTPS_PROXY=https://proxy.example.test:8443\n"
                "SGAR_TOOL_NO_PROXY=localhost,127.0.0.1\n",
                encoding="utf-8",
            )
            config = load_tool_proxy_config(root)
            args = direct_container_environment_args(
                proxy_config=config, network_required=True, network_policy_mode="declared"
            )
            joined = " ".join(args)
            self.assertIn("HTTP_PROXY", joined)
            self.assertNotIn("example.test", joined)
            child_env = tool_proxy_environment(config, network_required=True, network_policy_mode="declared")
            self.assertEqual(child_env["HTTP_PROXY"], "http://user:secret@example.test:8080")
            self.assertEqual(child_env["NO_PROXY"], "localhost,127.0.0.1")
            audit = tool_proxy_audit(config)
            self.assertTrue(audit["proxy_enabled"])
            self.assertNotIn("secret", str(audit))
            self.assertNotIn("example.test", str(audit))

            disabled = direct_container_environment_args(
                proxy_config=config, network_required=False, network_policy_mode="declared"
            )
            self.assertNotIn("example.test", " ".join(disabled))

    def test_unset_proxy_keeps_direct_defaults(self) -> None:
        args = direct_container_environment_args(
            proxy_config={}, network_required=True, network_policy_mode="declared"
        )
        joined = " ".join(args)
        self.assertIn("HTTP_PROXY", joined)
        self.assertEqual(tool_proxy_environment({}, network_required=True, network_policy_mode="declared")["NO_PROXY"], "*")


if __name__ == "__main__":
    unittest.main()
