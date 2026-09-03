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

  // 1. Mobile 390px
  const pageMobile = await browser.newPage();
  await pageMobile.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await pageMobile.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });
  await sleep(1500);

  const mobPath = path.join(outDir, 'before_mobile_390.png');
  await pageMobile.screenshot({ path: mobPath, fullPage: true });
  if (fs.existsSync(brainDir)) fs.copyFileSync(mobPath, path.join(brainDir, 'before_mobile_390.png'));
  console.log(`Saved ${mobPath}`);

  // 2. Desktop 1280px
  const pageDesktop = await browser.newPage();
  await pageDesktop.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
  await pageDesktop.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });
  await sleep(1500);

  const deskPath = path.join(outDir, 'before_desktop.png');
  await pageDesktop.screenshot({ path: deskPath, fullPage: true });
  if (fs.existsSync(brainDir)) fs.copyFileSync(deskPath, path.join(brainDir, 'before_desktop.png'));
  console.log(`Saved ${deskPath}`);

  await browser.close();
}

capture().catch(console.error);

