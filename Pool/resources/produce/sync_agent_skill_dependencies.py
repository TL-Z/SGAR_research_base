"""Synchronize non-exclusive Agent Card recommendations with the active Skill pool."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = PROJECT_ROOT / "Pool" / "resources" / "agents"
SKILLS_JSON = PROJECT_ROOT / "Pool" / "resources" / "json" / "skills.json"


AGENT_SKILLS: Dict[str, List[str]] = {
    "backend_architecture_expert.md": [
        "skill.superpowers.brainstorming.v1",
        "skill.superpowers.writing-plans.v1",
        "skill.trailofbits.spec-to-code-compliance.v1",
    ],
    "test_driven_qa.md": [
        "skill.superpowers.test-driven-development.v1",
        "skill.neolabhq.design-testing-strategy.v1",
        "skill.neolabhq.fix-tests.v1",
        "skill.anthropic.webapp-testing.v1",
    ],
    "business_requirements_analyst.md": [
        "skill.trailofbits.ask-questions-if-underspecified.v1",
        "skill.superpowers.brainstorming.v1",
        "skill.superpowers.writing-plans.v1",
    ],
    "code_reviewer.md": [
        "skill.superpowers.requesting-code-review.v1",
        "skill.superpowers.receiving-code-review.v1",
        "skill.trailofbits.differential-review.v1",
    ],
    "root_cause_debugger.md": [
        "skill.superpowers.systematic-debugging.v1",
        "skill.neolabhq.fix-tests.v1",
        "skill.trailofbits.entry-point-analyzer.v1",
    ],
    "documentation_engineer.md": [
        "skill.neolabhq.write-concisely.v1",
        "skill.openai_plugins.writing-skills.v1",
        "skill.anthropic.brand-guidelines.v1",
    ],
    "data_analyst.md": [
        "skill.openai_plugins.dashboard-expert.v1",
        "skill.trailofbits.dimensional-analysis.v1",
        "skill.neolabhq.write-concisely.v1",
    ],
    "api_designer.md": [
        "skill.superpowers.brainstorming.v1",
        "skill.superpowers.writing-plans.v1",
        "skill.trailofbits.spec-to-code-compliance.v1",
    ],
    "frontend_developer.md": [
        "skill.anthropic.frontend-design.v1",
        "skill.anthropic.webapp-testing.v1",
        "skill.superpowers.verification-before-completion.v1",
    ],
    "fullstack_implementation_engineer.md": [
        "skill.superpowers.executing-plans.v1",
        "skill.superpowers.test-driven-development.v1",
        "skill.superpowers.verification-before-completion.v1",
    ],
    "database_optimizer.md": [
        "skill.openai_plugins.supabase-postgres-best-practices-be9a8dce.v1",
        "skill.trailofbits.dimensional-analysis.v1",
        "skill.superpowers.systematic-debugging.v1",
    ],
    "performance_engineer.md": [
        "skill.trailofbits.dimensional-analysis.v1",
        "skill.trailofbits.code-maturity-assessor.v1",
        "skill.trailofbits.modern-python.v1",
    ],
    "data_engineer.md": [
        "skill.trailofbits.modern-python.v1",
        "skill.openai_plugins.dashboard-expert.v1",
        "skill.superpowers.writing-plans.v1",
    ],
    "accessibility_tester.md": [
        "skill.anthropic.frontend-design.v1",
        "skill.anthropic.webapp-testing.v1",
        "skill.superpowers.verification-before-completion.v1",
    ],
    "refactoring_specialist.md": [
        "skill.trailofbits.differential-review.v1",
        "skill.superpowers.test-driven-development.v1",
        "skill.superpowers.verification-before-completion.v1",
    ],
    "product_manager.md": [
        "skill.trailofbits.ask-questions-if-underspecified.v1",
        "skill.superpowers.brainstorming.v1",
        "skill.superpowers.writing-plans.v1",
    ],
    "devops_incident_responder.md": [
        "skill.superpowers.finishing-a-development-branch.v1",
        "skill.superpowers.using-git-worktrees.v1",
        "skill.trailofbits.secure-workflow-guide.v1",
    ],
    "research_analyst.md": [
        "skill.trailofbits.audit-context-building.v1",
        "skill.trailofbits.ask-questions-if-underspecified.v1",
        "skill.neolabhq.write-concisely.v1",
    ],
    "search_specialist.md": [
        "skill.trailofbits.slicing-code-context.v1",
        "skill.trailofbits.entry-point-analyzer.v1",
        "skill.trailofbits.audit-context-building.v1",
    ],
    "security_auditor.md": [
        "skill.trailofbits.insecure-defaults.v1",
        "skill.trailofbits.property-based-testing.v1",
        "skill.trailofbits.supply-chain-risk-auditor.v1",
        "skill.trailofbits.secure-workflow-guide.v1",
    ],
}


SECTION_PATTERN = re.compile(
    r"(?ms)^## Recommended Dependencies \(Non-Exclusive\)\s*\n"
    r"(.*?)(?=^## |\Z)"
)
DEPENDENCY_PATTERN = re.compile(r"(?m)^- `([^`]+)`\s*$")


def sync() -> None:
    skills = json.loads(SKILLS_JSON.read_text(encoding="utf-8-sig"))
    active_ids = {
        item["resource_id"]
        for item in skills
        if item.get("status") == "active"
    }
    missing = sorted(
        {
            skill_id
            for skill_ids in AGENT_SKILLS.values()
            for skill_id in skill_ids
            if skill_id not in active_ids
        }
    )
    if missing:
        raise ValueError(f"Agent recommendations reference inactive Skills: {missing}")

    actual_cards = {path.name for path in AGENT_DIR.glob("*.md")}
    if actual_cards != set(AGENT_SKILLS):
        raise ValueError(
            "Agent recommendation mapping does not cover exactly the Agent Cards: "
            f"missing={sorted(actual_cards - set(AGENT_SKILLS))}, "
            f"unknown={sorted(set(AGENT_SKILLS) - actual_cards)}"
        )

    for filename, skill_ids in AGENT_SKILLS.items():
        path = AGENT_DIR / filename
        text = path.read_text(encoding="utf-8")
        match = SECTION_PATTERN.search(text)
        if match is None:
            raise ValueError(f"Missing non-exclusive dependency section: {path}")
        existing_ids = DEPENDENCY_PATTERN.findall(match.group(1))
        non_skill_ids = [
            resource_id
            for resource_id in existing_ids
            if not resource_id.startswith("skill")
        ]
        dependencies = [*non_skill_ids, *skill_ids]
        body = (
            "These dependencies are routing hints, not a hard allowlist. The agent "
            "may use any valid Model, Tool, Skill, or Resource explicitly selected "
            "and bound by the ResourceApplicationPlan.\n\n"
            + "\n".join(f"- `{resource_id}`" for resource_id in dependencies)
            + "\n\n"
        )
        updated = text[: match.start(1)] + body + text[match.end(1) :]
        path.write_text(updated, encoding="utf-8")
    print(f"Synchronized Skill recommendations for {len(AGENT_SKILLS)} Agent Cards.")


if __name__ == "__main__":
    sync()
