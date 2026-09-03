const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const executablePath = fs.existsSync(CHROME_PATH) ? CHROME_PATH : EDGE_PATH;

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function capture() {
  const browser = await puppeteer.launch({
    executablePath,
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });

  const outDir = path.resolve(__dirname, '..', '..', 'docs');
  const brainDir = 'C:\\Users\\Yeoh Ming Zhe\\.gemini\\antigravity\\brain\\6b128c30-8ed5-463f-acc1-f0b39060c9c8';

  // 1. Mobile 390px - Two-turn chat
  console.log('Capturing mobile 390px two-turn chat...');
  const pageMobile = await browser.newPage();
  await pageMobile.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await pageMobile.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });
  await pageMobile.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 30000 });

  // Turn 1
  await pageMobile.type('textarea.chat-textarea', 'My business started in 2024 with RM2M turnover. When must I implement e-Invoice?');
  await pageMobile.click('button[type="submit"].btn-primary');
  await pageMobile.waitForSelector('.msg-row.assistant .answer-body', { timeout: 60000 });
  await sleep(1500);

  // Turn 2
  await pageMobile.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 30000 });
  await pageMobile.type('textarea.chat-textarea', 'It started in 2024, and no, I have no corporate shareholder, holding company or related company of any size.');
  await pageMobile.click('button[type="submit"].btn-primary');
  await pageMobile.waitForFunction(
    () => document.querySelectorAll('.msg-row.assistant .answer-body').length >= 2 && !document.querySelector('.loading-text'),
    { timeout: 90000 }
  );
  await sleep(2000);

  const mobPath = path.join(outDir, 'after_mobile_390.png');
  await pageMobile.screenshot({ path: mobPath, fullPage: true });
  if (fs.existsSync(brainDir)) fs.copyFileSync(mobPath, path.join(brainDir, 'after_mobile_390.png'));
  console.log(`Saved ${mobPath}`);

  // 2. Mobile 390px - Check Invoice
  console.log('Capturing mobile 390px Check Invoice...');
  const tabButtons = await pageMobile.$$('.tab-btn');
  for (const btn of tabButtons) {
    const text = await pageMobile.evaluate((el) => el.textContent, btn);
    if (text.includes('Check Invoice')) {
      await btn.click();
      break;
    }
  }
  await sleep(800);
  await pageMobile.click('.check-invoice-container .btn-primary');
  await pageMobile.waitForSelector('.report-card');
  await sleep(1000);

  const mobCheckPath = path.join(outDir, 'after_check_invoice_mobile_390.png');
  await pageMobile.screenshot({ path: mobCheckPath, fullPage: true });
  if (fs.existsSync(brainDir)) fs.copyFileSync(mobCheckPath, path.join(brainDir, 'after_check_invoice_mobile_390.png'));
  console.log(`Saved ${mobCheckPath}`);
  await pageMobile.close();

  // 3. Desktop 1280px - Chat & Check Invoice
  console.log('Capturing desktop 1280px...');
  const pageDesktop = await browser.newPage();
  await pageDesktop.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
  await pageDesktop.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });
  await pageDesktop.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 30000 });

  // Turn 1
  await pageDesktop.type('textarea.chat-textarea', 'My business started in 2024 with RM2M turnover. When must I implement e-Invoice?');
  await pageDesktop.click('button[type="submit"].btn-primary');
  await pageDesktop.waitForSelector('.msg-row.assistant .answer-body', { timeout: 60000 });
  await sleep(1500);

  // Turn 2
  await pageDesktop.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 30000 });
  await pageDesktop.type('textarea.chat-textarea', 'It started in 2024, and no, I have no corporate shareholder, holding company or related company of any size.');
  await pageDesktop.click('button[type="submit"].btn-primary');
  await pageDesktop.waitForFunction(
    () => document.querySelectorAll('.msg-row.assistant .answer-body').length >= 2 && !document.querySelector('.loading-text'),
    { timeout: 90000 }
  );
  await sleep(2000);

  const deskPath = path.join(outDir, 'after_desktop.png');
  await pageDesktop.screenshot({ path: deskPath, fullPage: true });
  if (fs.existsSync(brainDir)) fs.copyFileSync(deskPath, path.join(brainDir, 'after_desktop.png'));
  console.log(`Saved ${deskPath}`);

  // Desktop Check Invoice
  const deskTabs = await pageDesktop.$$('.tab-btn');
  for (const btn of deskTabs) {
    const text = await pageDesktop.evaluate((el) => el.textContent, btn);
    if (text.includes('Check Invoice')) {
      await btn.click();
      break;
    }
  }
  await sleep(800);
  await pageDesktop.click('.check-invoice-container .btn-primary');
  await pageDesktop.waitForSelector('.report-card');
  await sleep(1000);

  const deskCheckPath = path.join(outDir, 'after_check_invoice_desktop.png');
  await pageDesktop.screenshot({ path: deskCheckPath, fullPage: true });
  if (fs.existsSync(brainDir)) fs.copyFileSync(deskCheckPath, path.join(brainDir, 'after_check_invoice_desktop.png'));
  console.log(`Saved ${deskCheckPath}`);

  await browser.close();
  console.log('All screenshots captured successfully!');
}

capture().catch((err) => {
  console.error('Error during capture:', err);
  process.exit(1);
});

