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

  // Helper to ask a question in a clean page
  async function runQuestion(title, questions, outFile) {
    console.log(`Running ${title}...`);
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });
    await page.waitForSelector('textarea.chat-textarea:not([disabled])', { timeout: 30000 });

    for (let i = 0; i < questions.length; i++) {
      const q = questions[i];
      await page.type('textarea.chat-textarea', q);
      await page.click('button[type="submit"].btn-primary');

      // Wait for assistant response
      await page.waitForFunction(
        (targetCount) => document.querySelectorAll('.msg-row.assistant .answer-body').length >= targetCount && !document.querySelector('.loading-text'),
        { timeout: 90000 },
        i + 1
      );
      await sleep(1500);
    }

    const outPath = path.join(outDir, outFile);
    await page.screenshot({ path: outPath, fullPage: true });
    if (fs.existsSync(brainDir)) fs.copyFileSync(outPath, path.join(brainDir, outFile));
    console.log(`Saved ${outPath}`);
    await page.close();
  }

  // 1. Q01
  await runQuestion(
    'Q01 - Exemption threshold',
    ['What is the exemption threshold for e-Invoice implementation?'],
    'golden_q01_typography.png'
  );

  // 2. Q04
  await runQuestion(
    'Q04 - Consolidated sale RM12,000',
    ['Can I issue a consolidated e-Invoice for a RM12,000 sale?'],
    'golden_q04_typography.png'
  );

  // 3. Q03 (Two-turn Q2)
  await runQuestion(
    'Q03 - Two-turn profile collection',
    [
      'My business started in 2024 with RM2M turnover. When must I implement e-Invoice?',
      'It started in 2024, and no, I have no corporate shareholder, holding company or related company of any size.',
    ],
    'golden_q03_typography.png'
  );

  await browser.close();
  console.log('All golden question screenshots captured!');
}

capture().catch((err) => {
  console.error('Error capturing golden typography screenshots:', err);
  process.exit(1);
});
