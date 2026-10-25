"""A documentation site: no runtime manifest, only a dev script and a GitHub Pages workflow."""

FILES = {
    "package.json": '{\n  "name": "docs",\n  "scripts": {\n    "dev": "vitepress dev docs",\n    "build": "vitepress build docs"\n  }\n}\n',
    "docs/index.md": "# Guide\n\nThe api talks to the worker.\n",
    ".github/workflows/deploy.yml": (
        "name: deploy\non: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n      - run: npm ci && npm run build\n"
        "      - uses: actions/upload-pages-artifact@v3\n      - uses: actions/deploy-pages@v4\n"
    ),
    "README.md": "# Docs\n\nRun `npm run dev`.\n",
}

REPO = {"owner": "acme", "repo": "docs", "meta": {"default_branch": "main"}, "files": FILES}
URL = "https://github.com/acme/docs"
