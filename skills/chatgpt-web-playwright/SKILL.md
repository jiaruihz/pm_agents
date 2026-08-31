---
name: chatgpt-web-playwright
description: Scaffold and maintain a local Node.js + Playwright bot that opens the ChatGPT website, reuses a persistent login session, sends prompts, waits for stable answers, and saves text, JSON, and screenshots. Use when the user wants ChatGPT web automation, persistent browser profiles, Playwright-based login reuse, or a local script Codex can call as a tool.
---

# ChatGPT Web Playwright

Use this skill when the user wants a local browser automation flow for ChatGPT web instead of direct API usage.

Typical requests:

- "Make a script that opens ChatGPT web and asks questions automatically"
- "Reuse my ChatGPT login state with Playwright"
- "Save ChatGPT web answers to local files"
- "Wrap ChatGPT web automation so Codex can call it"

## What this skill creates

The default scaffold is a minimal Node.js project with:

1. `scripts/login.js`
2. `scripts/ask.js`
3. `data/profile-chatgpt/`
4. `data/records/`
5. `package.json`

`login.js` is for the one-time manual login.
`ask.js` reuses the saved browser profile, submits a prompt, waits until the last assistant message becomes stable, then saves:

1. Markdown
2. JSON
3. Screenshot

## Workflow

### 1. Scaffold the bot project

Run:

```bash
python3 skills/chatgpt-web-playwright/scripts/scaffold_chatgpt_web_bot.py \
  --target-dir /path/to/chatgpt-web-bot
```

This writes the minimal project files but does not install dependencies.

### 2. Install dependencies

Inside the generated project:

```bash
npm install
npx playwright install chromium
```

Use `chromium` only unless the user explicitly needs other browsers.

### 3. Manual login

Run:

```bash
node scripts/login.js
```

The browser opens with a persistent profile under `data/profile-chatgpt/`.
The user logs in manually once, then returns to the terminal and presses Enter.
The scaffold ignores this profile and `data/records/`; never commit either login
state or captured conversations.

### 4. Ask questions

Run:

```bash
node scripts/ask.js "your question"
```

Outputs are saved under `data/records/`.

## Rules

1. Prefer persistent context over storage-state hacks for this workflow.
2. Keep selector definitions grouped in one place so future UI fixes are localized.
3. Do not rely on `networkidle` to decide that an answer is finished.
4. Prefer message-text stabilization logic: poll the last assistant message until it stops changing.
5. If ChatGPT UI selectors drift, use Playwright codegen to refresh them.
6. Require an explicit target directory. Keep the persistent profile and generated
   records untracked even when the scaffold lives inside another repository.

## Locator maintenance

When selectors break, run:

```bash
npx playwright codegen https://chatgpt.com
```

Prefer these locator families in order:

1. `getByRole`
2. `getByLabel`
3. `getByText`
4. stable `data-testid`

Only fall back to broad CSS selectors when the page gives no better hook.

## Codex integration pattern

Once the generated project is stable, Codex should call the script instead of driving the DOM directly.

Preferred pattern:

1. Codex runs `node scripts/ask.js "..."`.
2. Codex reads the latest file from `data/records/`.
3. Codex summarizes or post-processes the saved result.

That keeps browser fragility inside the local bot project and keeps Codex logic simple.

## Notes

1. This skill is for local, user-controlled browser automation.
2. Do not turn it into high-frequency or large-scale scraping.
3. Expect periodic selector maintenance because ChatGPT web UI changes over time.
