# Northstar Workspace demo knowledge pack

A fictional, detailed company knowledge base for testing ResolveAI. The source contains 26 sections and 16 product entries. No real customer data, working company contact details, credentials, or live commercial information are included.

Upload **one** company knowledge file to a ResolveAI workspace:

- [PDF company knowledge base](Northstar_Workspace_Company_Knowledge_Base.pdf)
- [DOCX company knowledge base](Northstar_Workspace_Company_Knowledge_Base.docx)
- [Editable Markdown company knowledge base](Northstar_Workspace_Company_Knowledge_Base.md)

Keep the evaluation guide separate; do **not** upload it:

- [Test guide and expected answers](Northstar_Workspace_Test_Guide.md)
- [PDF test guide](Northstar_Workspace_Test_Guide.pdf)

The test guide contains 60 exercises, instructions for Local demo and Gemini modes, matching built-in synthetic order fixtures, and explicit limits of the current mock execution workflow. Local mode needs no provider keys; Gemini mode uses your configured Gemini account and their quotas or charges.

The PDF and DOCX files are generated from the Markdown source. The pack fits the app's 20 MB upload, 80-page PDF, and 500,000-character extracted-text limits. Uploading a policy does not create live orders, send messages, process payments, or implement all of that policy as business logic.

The knowledge PDF has 25 pages and approximately 11,000 words. The separate test-guide PDF has 13 pages. The company corpus is accepted by the application parser; the unified dashboard browser check uploads its actual PDF, waits for the Redis worker, and verifies cited answers and conversation history. The 60 exercises are a manual learning pack, not a claimed live-model quality benchmark.

With `dev` checked out locally, get the pack using:

```powershell
git pull --ff-only origin dev
```

The files are in `docs\demo`. Uploading these documents requires no Docker rebuild or application changes.

To re-export the edited test guide, install the optional documentation dependency `reportlab` and run `python docs/demo/export_test_guide.py`. It is not needed to run ResolveAI or upload the already generated documents.
