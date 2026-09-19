"""Knowledge engine — loads Ford practices from version-controlled files.

Not RAG. Direct context injection. At Ford's knowledge scale (dozens of docs),
this is simpler, more predictable, and more maintainable than vector search.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_knowledge(knowledge_dir: str | Path = "knowledge") -> str:
    """Load all knowledge files and format them for agent instruction context.

    Reads YAML practices and guardrails, plus markdown runbooks,
    and returns a formatted string for injection into the agent's system prompt.
    """
    knowledge_dir = Path(knowledge_dir)
    if not knowledge_dir.is_dir():
        return ""

    sections: list[str] = []

    for category in ["practices", "guardrails", "runbooks"]:
        cat_dir = knowledge_dir / category
        if not cat_dir.is_dir():
            continue

        entries: list[str] = []
        for filepath in sorted(cat_dir.iterdir()):
            if filepath.suffix in (".yaml", ".yml"):
                with open(filepath) as f:
                    content = yaml.safe_load(f)
                    if content:
                        dumped = yaml.dump(content, default_flow_style=False)
                        entries.append(f"### {filepath.stem}\n{dumped}")
            elif filepath.suffix == ".md":
                text = filepath.read_text().strip()
                if text and not text.startswith("# Placeholder"):
                    entries.append(f"### {filepath.stem}\n{text}")

        if entries:
            sections.append(f"## {category.title()}\n\n" + "\n\n".join(entries))

    return "\n\n---\n\n".join(sections)


def get_knowledge_instruction(knowledge_dir: str | Path = "knowledge") -> str:
    """Build the knowledge portion of the agent's system instruction."""
    knowledge_text = load_knowledge(knowledge_dir)
    if not knowledge_text:
        return ""
    return (
        "\n\n# Ford Platform Knowledge Base\n"
        "The following are Ford's documented practices, guardrails, and runbooks. "
        "Always follow these when performing actions.\n\n"
        f"{knowledge_text}"
    )
