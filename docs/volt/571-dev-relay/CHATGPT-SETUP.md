# Connecting your ChatGPT to VOLT development

**Who this is for:** VOLT's two authorized development people. That is D'Angelo (simmonsddk@gmail.com) and Mike.

**What you need**
- Your own ChatGPT account on a **paid plan** (Plus, Pro or Business), used on **chatgpt.com in a browser**.
- Your own VOLT / EvoSys login (the one you use for VOLT Operations).

**What you don't need:** a GitHub account, a Claude account, or anyone else's login. You never copy or paste a key.

> **Why it's a plugin and not a custom GPT.** OpenAI stops running custom GPTs on **Dec 11, 2026**. A GPT Action set up today would stop working then. A plugin built from a custom MCP server is ChatGPT's supported way to connect to an outside service, and it signs each person in as themselves.
>
> Only this plugin sends anything to VOLT. Ordinary chats and shared ChatGPT projects never do.

## One-time setup (about 3 minutes)

1. Open **https://chatgpt.com/plugins** and click **Add** (top right), then **Add custom MCP server**.
   - If you don't see that option, your plan or a workspace setting doesn't allow custom MCP servers. Tell Mike.
2. Fill in the form:
   - **Name:** `VOLT Development`
   - **Server URL:** `https://api.voltmarketing.live/volt/dev/mcp`
   - **Authentication:** `OAuth`. ChatGPT fills in the rest by itself (Dynamic Client Registration, scope `volt.dev`).
3. Tick **I understand and want to continue**, then click **Create as a plugin**.
4. Click **Connect VOLT Development**. A VOLT page opens:
   - Sign in with **your own** VOLT login.
   - Check that it shows your name, then click **Allow**.
   - You land back in ChatGPT.
5. If ChatGPT asks, **install** the plugin. It is under Plugins → your plugins.

## Using it

Start a new chat, type `@VOLT Development`, then ask. For example:

- *"Submit a VOLT change: on the ACA Healthcare Outreach page, add a line saying outreach hours are 9 to 5 Central."*
- *"What's the status of DEV-3?"*
- *"Show me the changes and test results for DEV-3."*
- *"Retry DEV-3."*
- *"Release DEV-3."* ChatGPT shows you the job and its **tested commit** and asks you to confirm. Nothing reaches the live site unless you approve that exact commit.

## What each status means

| Status | What it proves |
|---|---|
| Submitted | VOLT saved your request. |
| Dispatched | GitHub accepted the start request. This does **not** prove the work started. |
| Running – Claude working | The runner, proven by its GitHub identity, picked up the job. |
| Running – testing | Claude finished writing; the tests and the build are running. |
| PR ready – tests passed | The change is on its own branch with passing tests, ready for your review. |
| Approved – merging | A principal approved one exact tested commit. |
| Merged – deploying | That commit is on main; Render is deploying it. |
| Deployed – verified running | VOLT checked production and found the commit actually running. |

## Disconnecting

There are two ways:
- **VOLT Operations → Development → ChatGPT access → Disconnect.** This takes effect immediately.
- **In ChatGPT:** Plugins → VOLT Development → disconnect.

Your access also ends by itself if you stop being an authorized VOLT development person.
