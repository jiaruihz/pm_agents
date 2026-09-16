const fs = require('fs');
const os = require('os');
const path = require('path');

function fileExists(filePath) {
  if (!filePath) return false;
  try {
    return fs.existsSync(filePath);
  } catch {
    return false;
  }
}

function chromeCandidates() {
  const home = os.homedir();
  switch (process.platform) {
    case 'darwin':
      return [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        path.join(home, 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
        '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
      ];
    case 'win32':
      return [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
      ];
    default:
      return [
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/snap/bin/chromium',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
      ];
  }
}

function chromeUserDataCandidates() {
  const home = os.homedir();
  switch (process.platform) {
    case 'darwin':
      return [
        path.join(home, 'Library/Application Support/Google/Chrome'),
        path.join(home, 'Library/Application Support/Google/Chrome Canary'),
      ];
    case 'win32':
      return [
        path.join(process.env.LOCALAPPDATA || '', 'Google/Chrome/User Data'),
      ];
    default:
      return [
        path.join(home, '.config/google-chrome'),
        path.join(home, '.config/google-chrome-beta'),
        path.join(home, '.config/chromium'),
      ];
  }
}

function resolveChromeExecutable() {
  const envPath = process.env.CHROME_EXECUTABLE_PATH || process.env.GOOGLE_CHROME_BIN || '';
  if (fileExists(envPath)) {
    return envPath;
  }
  return chromeCandidates().find(fileExists) || null;
}

function resolveChromeUserDataDir() {
  const envPath = process.env.CHROME_USER_DATA_DIR || '';
  if (fileExists(envPath)) {
    return envPath;
  }
  return chromeUserDataCandidates().find(fileExists) || null;
}

function launchProfiles() {
  const executablePath = resolveChromeExecutable();
  const shared = {
    headless: false,
    viewport: { width: 1440, height: 960 },
  };

  const profiles = [];
  if (executablePath) {
    profiles.push({
      name: `system-chrome (${executablePath})`,
      options: { ...shared, executablePath },
    });
  }

  profiles.push({
    name: 'chrome-channel',
    options: { ...shared, channel: 'chrome' },
  });

  profiles.push({
    name: 'playwright-chromium',
    options: shared,
  });

  return profiles;
}

async function launchPersistentContextWithFallback(chromium, userDataDir) {
  const attempts = [];

  for (const profile of launchProfiles()) {
    try {
      console.log(`Launching browser profile: ${profile.name}`);
      const context = await chromium.launchPersistentContext(userDataDir, profile.options);
      return { context, profileName: profile.name };
    } catch (error) {
      attempts.push(`${profile.name}: ${error.message}`);
    }
  }

  throw new Error(`Failed to launch a usable browser. Attempts: ${attempts.join(' | ')}`);
}

module.exports = {
  launchPersistentContextWithFallback,
  resolveChromeExecutable,
  resolveChromeUserDataDir,
};
