---
name: api-docs-sync
description: Find Platform API endpoints missing from the Mintlify docs and add stub pages for the chosen ones. Use when asked to sync, audit or update the API reference against sandbox, local, production or a git ref.
---

# API docs sync

All heavy work is done by `scripts/api_docs_audit.py`. Never open the OpenAPI
spec, `docs.json`, `open-api-collection-test.json` or the pages under
`api-reference/` yourself — the script reads and writes them.

Endpoint page content (description, fields, examples) comes from the backend
route code, not from this repo. A stub is three lines of frontmatter; do not
write body text into it.

## Steps

1. Pick the source. Default `sandbox`. Others: `local`, a URL, a file,
   `app` (sibling `python-finogates.com` checkout), `app@<git-ref>`
   (e.g. `app@origin/staging`, `app@v1.42.0` for what production runs).

2. Run and show the user the output as-is:
   `python scripts/api_docs_audit.py audit --source <src> --brief`
   Add `--tags a,b` when the user already named an area.

3. Ask which endpoints to document (by tag or by line) unless told. Endpoints
   that should stay private go in `scripts/api_docs_ignore.txt`.

4. Write a plan to the scratchpad as JSON, one entry per chosen endpoint:
   `{"op": "POST /v1/...", "title": "...", "file": "kebab-name", "before": "api-reference/<folder>/<page>"?}`
   - `title`: short, Title Case, verb first, like the existing sidebar
     ("Create User", "List Payees"). Rewrite the spec summary; do not copy it.
   - `file`: short kebab-case from the title.
   - `before`: only when order matters (e.g. a request → create flow goes
     ahead of the legacy per-flow endpoints).

5. `python scripts/api_docs_audit.py scaffold --source <src> --plan <plan.json>`
   Use `--dry-run` first if the user wants to review. The script creates the
   `.mdx` stubs and the `docs.json` entries, and creates a new sidebar group
   when the tag has none.

6. If a new group was created, offer a short overview page for it
   (`api-reference/<folder>/overview.mdx`, listed first in the group) that
   explains the flow across its endpoints — that is the one thing the spec
   cannot say. Keep it under 30 lines.

7. Finish: `python scripts/api_docs_audit.py snapshot --source <src>` to
   refresh the committed copy, then tell the user to preview with
   `mintlify dev` and commit. Do not commit unless asked.

## CI

`python scripts/api_docs_audit.py check --source sandbox` exits 1 on any gap.
