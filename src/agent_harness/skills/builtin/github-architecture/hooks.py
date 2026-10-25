"""Hooks for github-architecture (trusted built-in skill)."""

_NOISE_DIRS = ("node_modules/", "vendor/", ".git/", "dist/", "build/", "__pycache__/", ".venv/")


def post_tool(tool: str, content: str) -> str:
    """Hide vendored/generated directories from tree listings so the model spends its budget on real code."""
    if tool != "github_list_tree":
        return content
    lines = content.splitlines()
    kept = [line for line in lines if not any(noise in line for noise in _NOISE_DIRS)]
    hidden = len(lines) - len(kept)
    if hidden:
        kept.append(f"({hidden} entries in vendored/generated directories hidden)")
    return "\n".join(kept)
