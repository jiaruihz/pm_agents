const { chromium } = require('playwright');

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
