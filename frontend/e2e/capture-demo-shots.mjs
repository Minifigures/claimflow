// Automated demo screenshot capture: logs into each portal against a running
// stack (make demo / docker compose up) and saves the money shots for README
// and presentations. Usage:
//   cd frontend && npm i -D playwright && npx playwright install chromium
//   node e2e/capture-demo-shots.mjs
// Env: DEMO_BASE_URL (default http://localhost:3000), SHOT_DIR (default ../docs/screenshots)

import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.DEMO_BASE_URL ?? "http://localhost:3000";
const OUT = process.env.SHOT_DIR ?? "../docs/screenshots";
const PASSWORD = "demo1234";

// Seed order is fixed, so claim ids are stable: CLM-DEMO-000N has id N.
const SHOTS = [
  { email: "imaging@demo.ca", path: "/imaging", name: "imaging-queue", full: false },
  { email: "imaging@demo.ca", path: "/imaging/cases/2", name: "imaging-tampered-case", full: true },
  { email: "claimant@demo.ca", path: "/claimant", name: "claimant-portal", full: false },
  { email: "claimant@demo.ca", path: "/claimant/claims/7", name: "claimant-timeline-approved", full: true },
  { email: "specialist@demo.ca", path: "/specialist/cases/3", name: "specialist-recommendation", full: true },
  { email: "agent@demo.ca", path: "/agent/cases/4", name: "agent-dossier", full: true },
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();

const byRole = SHOTS.reduce((acc, s) => ((acc[s.email] ??= []).push(s), acc), {});
for (const [email, shots] of Object.entries(byRole)) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes("login"), { timeout: 15000 });

  for (const shot of shots) {
    await page.goto(`${BASE}${shot.path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(800); // let badges/async panels settle
    await page.screenshot({ path: `${OUT}/${shot.name}.png`, fullPage: shot.full });
    console.log(`captured ${shot.name}.png (${email} ${shot.path})`);
  }
  await context.close();
}

await browser.close();
console.log(`done: ${SHOTS.length} screenshots in ${OUT}`);
