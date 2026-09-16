const { chromium } = require('playwright');
const fs = require('fs/promises');
const path = require('path');
const { launchPersistentContextWithFallback } = require('./browser');

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

  const { context, profileName } = await launchPersistentContextWithFallback(chromium, USER_DATA_DIR);

  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  console.log(`Browser mode: ${profileName}`);

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
  ].join('\n');

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
