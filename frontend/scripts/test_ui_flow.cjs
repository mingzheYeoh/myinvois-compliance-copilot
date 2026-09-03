const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const executablePath = fs.existsSync(CHROME_PATH) ? CHROME_PATH : EDGE_PATH;

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function run() {
  console.log(`Using browser: ${executablePath}`);
  const browser = await puppeteer.launch({
    executablePath,
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });

  const page = await browser.newPage();
  await page.setViewport({
    width: 412,
    height: 915,
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });

  console.log('Navigating to http://127.0.0.1:8000...');
  await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0', timeout: 30000 });

  console.log('Waiting for backend health check and chat readiness...');
  // Wait until textarea is enabled (meaning health is ok)
  await page.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 45000 });
  console.log('Chat assistant is ready!');

  // Turn 1
  const turn1 = 'My business started in 2024 with RM2M turnover. When must I implement e-Invoice?';
  console.log(`Submitting Turn 1: "${turn1}"`);
  await page.type('textarea.chat-textarea', turn1);
  await page.click('button[type="submit"].btn-primary');

  // Wait for Turn 1 response (assistant card appears)
  console.log('Waiting for Turn 1 assistant response...');
  await page.waitForSelector('.msg-row.assistant .assistant-card', { timeout: 60000 });
  await sleep(2000); // Allow text to settle
  console.log('Turn 1 response received!');

  // Turn 2
  const turn2 = 'It started in 2024, and no, I have no corporate shareholder, holding company or related company of any size.';
  console.log(`Submitting Turn 2: "${turn2}"`);
  await page.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 30000 });
  await page.type('textarea.chat-textarea', turn2);
  await page.click('button[type="submit"].btn-primary');

  // Wait for Turn 2 response (2nd assistant answer-body and no loading indicator)
  console.log('Waiting for Turn 2 assistant response...');
  await page.waitForFunction(
    () =>
      document.querySelectorAll('.msg-row.assistant .answer-body').length >= 2 &&
      !document.querySelector('.loading-dot'),
    { timeout: 90000 }
  );
  await sleep(2000);
  console.log('Turn 2 response received!');

  // Capture screenshot
  const outDir = path.resolve(__dirname, '..', '..', 'docs');
  if (!fs.existsSync(outDir)) {
    fs.mkdirSync(outDir, { recursive: true });
  }
  const screenshotPath = path.join(outDir, 'two_turn_q2_ui.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(`Screenshot saved to: ${screenshotPath}`);

  // Also copy to brain artifact directory
  const brainDir = 'C:\\Users\\Yeoh Ming Zhe\\.gemini\\antigravity\\brain\\6b128c30-8ed5-463f-acc1-f0b39060c9c8';
  if (fs.existsSync(brainDir)) {
    const brainScreenshot = path.join(brainDir, 'two_turn_q2_ui.png');
    fs.copyFileSync(screenshotPath, brainScreenshot);
    console.log(`Screenshot copied to artifact dir: ${brainScreenshot}`);
  }

  await browser.close();
  console.log('Done!');
}

run().catch((err) => {
  console.error('Error during test execution:', err);
  process.exit(1);
});
