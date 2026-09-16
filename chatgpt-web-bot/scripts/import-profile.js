const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const { resolveChromeUserDataDir } = require('./browser');

const TARGET_USER_DATA_DIR = path.resolve('data/profile-chatgpt');
const TARGET_PROFILE_DIR = path.join(TARGET_USER_DATA_DIR, 'Default');

function parseArgs(argv) {
  const args = {
    sourceUserDataDir: '',
    sourceProfileDir: 'Default',
    overwrite: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--source-user-data-dir') {
      args.sourceUserDataDir = argv[i + 1] || '';
      i += 1;
      continue;
    }
    if (arg === '--source-profile-dir') {
      args.sourceProfileDir = argv[i + 1] || 'Default';
      i += 1;
      continue;
    }
    if (arg === '--overwrite') {
      args.overwrite = true;
    }
  }

  return args;
}

async function copyIfExists(src, dest) {
  if (!fs.existsSync(src)) return;
  await fsp.cp(src, dest, { recursive: true, force: true });
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const sourceUserDataDir = path.resolve(args.sourceUserDataDir || resolveChromeUserDataDir() || '');

  if (!sourceUserDataDir || !fs.existsSync(sourceUserDataDir)) {
    console.error('Could not find a Chrome user data directory.');
    console.error('Pass one explicitly with --source-user-data-dir "/path/to/Chrome/User Data"');
    process.exit(1);
  }

  const sourceProfileDir = path.join(sourceUserDataDir, args.sourceProfileDir);
  if (!fs.existsSync(sourceProfileDir)) {
    console.error(`Source profile directory not found: ${sourceProfileDir}`);
    process.exit(1);
  }

  if (fs.existsSync(TARGET_PROFILE_DIR)) {
    if (!args.overwrite) {
      console.error(`Target profile already exists: ${TARGET_PROFILE_DIR}`);
      console.error('Re-run with --overwrite if you want to replace it.');
      process.exit(1);
    }
    await fsp.rm(TARGET_PROFILE_DIR, { recursive: true, force: true });
  }

  await fsp.mkdir(TARGET_USER_DATA_DIR, { recursive: true });
  await fsp.cp(sourceProfileDir, TARGET_PROFILE_DIR, { recursive: true, force: true });
  await copyIfExists(path.join(sourceUserDataDir, 'Local State'), path.join(TARGET_USER_DATA_DIR, 'Local State'));
  await copyIfExists(path.join(sourceUserDataDir, 'Last Version'), path.join(TARGET_USER_DATA_DIR, 'Last Version'));
  await copyIfExists(path.join(sourceUserDataDir, 'First Run'), path.join(TARGET_USER_DATA_DIR, 'First Run'));

  console.log(`Imported Chrome profile from: ${sourceProfileDir}`);
  console.log(`Saved reusable profile under: ${TARGET_USER_DATA_DIR}`);
  console.log('Close all Chrome windows before importing to reduce cookie/session corruption.');
})();

