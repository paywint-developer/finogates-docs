# Mintlify Starter Kit

Click on `Use this template` to copy the Mintlify starter kit. The starter kit contains examples including

- Guide pages
- Navigation
- Customizations
- API Reference pages
- Use of popular components

### Development

Install the [Mintlify CLI](https://www.npmjs.com/package/mintlify) to preview the documentation changes locally. To install, use the following command

```
npm i -g mintlify
```

Run the following command at the root of your documentation (where docs.json is)

```
mintlify dev
```

### Publishing Changes

Install our Github App to auto propagate changes from your repo to your deployment. Changes will be deployed to production automatically after pushing to the default branch. Find the link to install on your dashboard. 

#### Troubleshooting

- Mintlify dev isn't running - Run `mintlify install` it'll re-install dependencies.
- Page loads as a 404 - Make sure you are running in a folder with `docs.json`

### Keeping the API reference in sync

`scripts/api_docs_audit.py` compares an OpenAPI spec with the pages under
`api-reference/` and writes the missing stubs:

```
python scripts/api_docs_audit.py audit    --source sandbox --brief   # what is missing
python scripts/api_docs_audit.py scaffold --source sandbox --tags payments
python scripts/api_docs_audit.py check    --source sandbox           # CI, exit 1 on gaps
```

Sources: `sandbox`, `local`, a URL, a file, or `app@<git-ref>` to build the
spec from the backend checkout at that ref (what production runs). Private
endpoints go in `scripts/api_docs_ignore.txt`. With Claude Code, `/api-docs-sync`
drives the same script and picks readable titles.

