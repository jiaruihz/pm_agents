const { chromium } = require('playwright');
const { launchPersistentContextWithFallback, resolveChromeExecutable } = require('./browser');

(async () => {
  const USER_DATA_DIR = 'data/profile-chatgpt';
  const chromePath = resolveChromeExecutable();
  const { context, profileName } = await launchPersistentContextWithFallback(chromium, USER_DATA_DIR);

  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });

  console.log(`Browser mode: ${profileName}`);
  if (chromePath) {
    console.log(`Detected Chrome executable: ${chromePath}`);
  }
  console.log('If ChatGPT keeps asking for robot verification, prefer importing an already logged-in Chrome profile instead of logging in here.');
  console.log('Run: npm run import-profile -- --source-profile-dir Default --overwrite');
  console.log('Please log into ChatGPT in the opened browser window.');
  console.log('When login is complete, return to the terminal and press Enter.');
  console.log('This window is the one whose login state will be saved and reused later.');

  await new Promise(resolve => process.stdin.once('data', resolve));

  await context.close();
  console.log('Saved login state to data/profile-chatgpt');
})();
