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

  await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });

  // Click "Check Invoice" tab
  await page.waitForSelector('.tab-btn');
  const buttons = await page.$$('.tab-btn');
  for (const btn of buttons) {
    const text = await page.evaluate((el) => el.textContent, btn);
    if (text.includes('Check Invoice')) {
      await btn.click();
      break;
    }
  }

  await sleep(1000);

  // Click "Check Invoice" validation button
  await page.waitForSelector('.check-invoice-container .btn-primary');
  await page.click('.check-invoice-container .btn-primary');

  // Wait for validation report to appear
  await page.waitForSelector('.report-card');
  await sleep(1000);

  const outDir = path.resolve(__dirname, '..', '..', 'docs');
  const screenshotPath = path.join(outDir, 'check_invoice_ui.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });

  const brainDir = 'C:\\Users\\Yeoh Ming Zhe\\.gemini\antigravity\\brain\\6b128c30-8ed5-463f-acc1-f0b39060c9c8';
  if (fs.existsSync(brainDir)) {
    fs.copyFileSync(screenshotPath, path.join(brainDir, 'check_invoice_ui.png'));
  }

  await browser.close();
  console.log('Check invoice screenshot saved!');
}

run().catch(console.error);

