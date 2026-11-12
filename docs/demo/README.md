# Northstar Workspace demo knowledge pack

A fictional, detailed company knowledge base for testing ResolveAI. The source contains 26 sections and 16 product entries. No real customer data, working company contact details, credentials, or live commercial information are included.

Upload **one** company knowledge file to a ResolveAI workspace:

- [PDF company knowledge base](Northstar_Workspace_Company_Knowledge_Base.pdf)
- [DOCX company knowledge base](Northstar_Workspace_Company_Knowledge_Base.docx)
- [Editable Markdown company knowledge base](Northstar_Workspace_Company_Knowledge_Base.md)

Keep the evaluation guide separate; do **not** upload it:

- [Test guide and expected answers](Northstar_Workspace_Test_Guide.md)
- [PDF test guide](Northstar_Workspace_Test_Guide.pdf)

The test guide contains 60 exercises, instructions for Local demo and Gemini modes, matching built-in synthetic order fixtures, and explicit limits of the current mock execution workflow. Local mode needs no provider keys; Gemini mode uses your configured Gemini/Pinecone accounts and their quotas or charges.

The PDF and DOCX files are generated from the Markdown source. The pack fits the app's 8 MB upload, 80-page PDF, and 100,000-character extracted-text limits. Uploading a policy does not create live orders, send messages, process payments, or implement all of that policy as business logic.

The knowledge PDF has 25 pages and approximately 11,000 words. Its extracted text is 76,868 characters and the file is about 129 KB. The separate test-guide PDF has 13 pages. All three knowledge formats were checked with the app's actual parser; twelve representative questions per format found the expected evidence through the existing local retrieval function. Those 36 checks used an in-memory adapter for ready chunks, with no database writes or provider requests. They are evidence-retrieval checks, not a live Gemini evaluation.

With `dev` checked out locally, get the pack using:

```powershell
git pull --ff-only origin dev
```

The files are in `docs\demo`. Uploading these documents requires no Docker rebuild or application changes.
