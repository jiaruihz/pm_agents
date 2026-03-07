#!/usr/bin/env python3
"""Scaffold a minimal ChatGPT web automation project using Playwright."""

from __future__ import annotations

import argparse
from pathlib import Path


PACKAGE_JSON = """{
  "name": "chatgpt-web-bot",
  "version": "0.1.0",
  "private": true,
  "description": "Local Playwright bot for ChatGPT web",
  "scripts": {
    "login": "node scripts/login.js",
    "ask": "node scripts/ask.js"
  },
  "devDependencies": {
    "playwright": "^1.52.0"
  }
}
"""


LOGIN_JS = """const { chromium } = require('playwright');

(async () => {
  const USER_DATA_DIR = 'data/profile-chatgpt';

  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: false,
    viewport: { width: 1440, height: 960 }
  });

  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });

  console.log('Please log into ChatGPT in the opened browser window.');
  console.log('When login is complete, return to the terminal and press Enter.');

  await new Promise(resolve => process.stdin.once('data', resolve));

  await context.close();
  console.log('Saved login state to data/profile-chatgpt');
})();
"""


ASK_JS = """const { chromium } = require('playwright');
const fs = require('fs/promises');
const path = require('path');

const USER_DATA_DIR = 'data/profile-chatgpt';
const RECORDS_DIR = 'data/records';

const SELECTORS = {
  composer: 'textarea, [contenteditable="true"]',
  assistantMessage: '[data-message-author-role="assistant"]',
  sendButton: 'button[aria-label*="Send"], button[data-testid*="send"]'
};

function nowTs() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

async function waitForStableAssistantText(page, timeoutMs = 180000) {
  const start = Date.now();
  let lastText = '';
  let stableCount = 0;

  while (Date.now() - start < timeoutMs) {
    const locator = page.locator(SELECTORS.assistantMessage).last();
    const count = await page.locator(SELECTORS.assistantMessage).count();

    if (count === 0) {
      await page.waitForTimeout(1500);
      continue;
    }

    let text = '';
    try {
      text = (await locator.innerText()).trim();
    } catch {
      await page.waitForTimeout(1500);
      continue;
    }

    if (!text) {
      await page.waitForTimeout(1500);
      continue;
    }

    if (text === lastText) {
      stableCount += 1;
    } else {
      stableCount = 0;
      lastText = text;
    }

    if (stableCount >= 3) {
      return text;
    }

    await page.waitForTimeout(2000);
  }

  throw new Error('Timed out waiting for a stable assistant answer');
}

(async () => {
  const prompt = process.argv.slice(2).join(' ').trim();
  if (!prompt) {
    console.error('Usage: node scripts/ask.js "your prompt"');
    process.exit(1);
  }

  await fs.mkdir(RECORDS_DIR, { recursive: true });

  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: false,
    viewport: { width: 1440, height: 960 }
  });

  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);

  const composer = page.locator(SELECTORS.composer).last();
  await composer.waitFor({ timeout: 30000 });
  await composer.fill(prompt);

  const sendBtn = page.locator(SELECTORS.sendButton).last();
  if (await sendBtn.count()) {
    await sendBtn.click();
  } else {
    await composer.press('Enter');
  }

  const answer = await waitForStableAssistantText(page);
  const ts = nowTs();
  const base = path.join(RECORDS_DIR, ts);

  await page.screenshot({ path: `${base}.png`, fullPage: true });

  const md = [
    '# ChatGPT Web Result',
    '',
    `- Time: ${new Date().toISOString()}`,
    `- Prompt: ${prompt}`,
    '',
    '## Answer',
    '',
    answer,
    ''
  ].join('\\n');

  await fs.writeFile(`${base}.md`, md, 'utf8');
  await fs.writeFile(
    `${base}.json`,
    JSON.stringify(
      {
        time: new Date().toISOString(),
        prompt,
        answer
      },
      null,
      2
    ),
    'utf8'
  );

  console.log(`Saved:
- ${base}.md
- ${base}.json
- ${base}.png`);

  await context.close();
})();
"""


GITIGNORE = """node_modules/
playwright-report/
test-results/
"""


def write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def scaffold(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scripts").mkdir(exist_ok=True)
    (target_dir / "data" / "profile-chatgpt").mkdir(parents=True, exist_ok=True)
    (target_dir / "data" / "records").mkdir(parents=True, exist_ok=True)

    write_file(target_dir / "package.json", PACKAGE_JSON)
    write_file(target_dir / ".gitignore", GITIGNORE)
    write_file(target_dir / "scripts" / "login.js", LOGIN_JS, executable=True)
    write_file(target_dir / "scripts" / "ask.js", ASK_JS, executable=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold a local Playwright ChatGPT web bot")
    parser.add_argument("--target-dir", required=True, help="Target directory for the generated project")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).expanduser().resolve()
    scaffold(target_dir)
    print(f"Scaffolded ChatGPT web bot at {target_dir}")


if __name__ == "__main__":
    main()

