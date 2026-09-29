"""Knowledge engine — loads Ford practices from version-controlled files.

Not RAG. Direct context injection. At Ford's knowledge scale (dozens of docs),
this is simpler, more predictable, and more maintainable than vector search.

Also provides helpers for MCP resource exposure so any MCP client can
read the same tribal knowledge the ADK agent gets injected automatically.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge"

_CATEGORY_EXTENSIONS = {
    "practices": (".yaml", ".yml"),
    "guardrails": (".yaml", ".yml"),
    "runbooks": (".md",),
}


def list_knowledge_files(knowledge_dir: str | Path | None = None) -> str:
    """Return a JSON index of all available knowledge files by category."""
    kdir = Path(knowledge_dir) if knowledge_dir else KNOWLEDGE_DIR
    index: dict[str, list[str]] = {}
    for category, exts in _CATEGORY_EXTENSIONS.items():
        cat_dir = kdir / category
        if cat_dir.is_dir():
            index[category] = [
                f.stem for f in sorted(cat_dir.iterdir()) if f.suffix in exts
            ]
        else:
            index[category] = []
    return json.dumps(index, indent=2)


def read_knowledge_file(
    category: str,
    name: str,
    knowledge_dir: str | Path | None = None,
) -> str:
    """Read a single knowledge file by category and name."""
    kdir = Path(knowledge_dir) if knowledge_dir else KNOWLEDGE_DIR
    exts = _CATEGORY_EXTENSIONS.get(category, (".yaml", ".yml", ".md"))
    for ext in exts:
        filepath = kdir / category / f"{name}{ext}"
        if filepath.is_file():
            return filepath.read_text()
    return f"Knowledge file '{category}/{name}' not found."


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
    # Replace curly braces — ADK's instruction processor treats {name} as session state variables
    knowledge_text = knowledge_text.replace("{", "<").replace("}", ">")
    return (
        "\n\n# Ford Platform Knowledge Base\n"
        "The following are Ford's documented practices, guardrails, and runbooks. "
        "Always follow these when performing actions.\n\n"
        f"{knowledge_text}"
    )
