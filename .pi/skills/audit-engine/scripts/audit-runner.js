/**
 * Audit Engine — Playwright 自动化验收脚本 v1.0
 * 
 * 用法:
 *   node audit-runner.js --industry=power --sample=30
 *   node audit-runner.js --url=/zh/industries/power/solar-farm/solar-farm-grounding-lightning
 *   node audit-runner.js --mode=healthcheck
 * 
 * 输出: JSON 格式审计原始数据 → stdout
 * 由 Audit Engine SKILL.md 中的 R8 规则驱动
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.AUDIT_BASE_URL || 'https://www.lightingmetal.com';
const LANG = process.env.AUDIT_LANG || 'zh';
const I18N_ROOT = process.env.AUDIT_I18N_ROOT || path.resolve(__dirname, '../../../../customer-portal/i18n');

// Auto-detect chromium: env var > Playwright managed > system installed
function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  // Check Playwright's managed chromium
  const pwChromium = '/root/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
  if (fs.existsSync(pwChromium)) return pwChromium;
  const pwChromiumShell = '/root/.cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell';
  if (fs.existsSync(pwChromiumShell)) return pwChromiumShell;
  // Fallback to system chromium
  return '/usr/bin/chromium-browser';
}

// ===================== Command Line Parsing =====================
const args = process.argv.slice(2).reduce((acc, a) => {
  const [k, v] = a.replace('--', '').split('=');
  acc[k] = v || true;
  return acc;
}, {});

// ===================== Helpers =====================
function readJSON(fp) {
  try { return JSON.parse(fs.readFileSync(fp, 'utf-8')); } catch { return null; }
}

function discoverPages(industry, sampleRate) {
  const root = path.join(I18N_ROOT, LANG, 'industries', industry);
  if (!fs.existsSync(root)) return { l2: [], l3: [], l4: [] };

  const l2Pages = [];
  const l3Pages = [];
  const l4Pages = [];

  for (const entry of fs.readdirSync(root)) {
    const ep = path.join(root, entry);
    if (entry.endsWith('.json')) {
      const data = readJSON(ep);
      if (!data) continue;
      const k = Object.keys(data)[0];
      if (data[k].cat1Name) {
        l2Pages.push({ name: data[k].heroTitle || entry.replace('.json', ''), l2: entry.replace('.json', '') });
      }
    } else if (fs.statSync(ep).isDirectory()) {
      for (const l3Entry of fs.readdirSync(ep)) {
        const l3p = path.join(ep, l3Entry);
        if (l3Entry.endsWith('.json')) {
          const data = readJSON(l3p);
          if (!data) continue;
          const k = Object.keys(data)[0];
          l3Pages.push({
            l2: entry,
            l3: l3Entry.replace('.json', ''),
            name: data[k].heroTitle || l3Entry.replace('.json', ''),
            url: `/industries/${industry}/${entry}/${l3Entry.replace('.json', '')}`
          });
        } else if (fs.statSync(l3p).isDirectory()) {
          for (const l4Entry of fs.readdirSync(l3p)) {
            if (l4Entry.endsWith('.json')) {
              const data = readJSON(path.join(l3p, l4Entry));
              const l4name = l4Entry.replace('.json', '');
              l4Pages.push({
                l2: entry,
                l3: l3Entry,
                l4: l4name,
                name: data ? (data[Object.keys(data)[0]].heroTitle || l4name) : l4name,
                url: `/industries/${industry}/${entry}/${l3Entry}/${l4name}`
              });
            }
          }
        }
      }
    }
  }
  return { l2: l2Pages, l3: l3Pages, l4: l4Pages };
}

function sample(arr, rate) {
  if (arr.length === 0) return [];
  const count = Math.max(1, Math.ceil(arr.length * rate / 100));
  const step = Math.max(1, Math.floor(arr.length / count));
  const result = [];
  for (let i = 0; i < arr.length && result.length < count; i += step) {
    result.push(arr[i]);
  }
  return result;
}

// ===================== Page Setup =====================
async function setupPage(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url.includes('googletagmanager') || url.includes('google-analytics') || url.includes('fonts.googleapis')) {
      route.abort();
    } else {
      route.continue();
    }
  });
  return { context, page };
}

// ===================== Page Checks =====================
async function checkPage(page, url, label) {
  const result = { url, label, status: 'unknown', issues: [] };
  const fullUrl = url.startsWith('http') ? url : `${BASE}${url}`;

  try {
    const resp = await page.goto(fullUrl, { waitUntil: 'commit', timeout: 20000 });
    result.status = resp.status();

    if (resp.status() >= 400) {
      result.issues.push({ type: 'HTTP_ERROR', severity: 'P0', detail: `HTTP ${resp.status()}` });
      return result;
    }

    // Wait for content
    await page.waitForSelector('h1, main', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);

    // H1 check
    const h1 = await page.locator('h1').first().textContent().catch(() => '');
    result.h1 = h1?.slice(0, 80);
    if (!h1 || h1.length < 2) {
      result.issues.push({ type: 'MISSING_H1', severity: 'P1' });
    }

    // Content length check
    const body = await page.locator('main').textContent().catch(() => '');
    result.contentLength = body.length;
    if (body.length < 100) {
      result.issues.push({ type: 'BLANK_PAGE', severity: 'P0', detail: `content=${body.length}chars` });
    }

    // SEO check
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href').catch(() => '');
    const alternates = await page.locator('link[rel="alternate"]').count();
    result.seo = { canonical: !!canonical, hreflangCount: alternates };
    if (!canonical) result.issues.push({ type: 'MISSING_CANONICAL', severity: 'P1' });

    // Breadcrumb check
    const breadcrumbLinks = await page.locator('nav[aria-label*="readcrumb"] a, [class*="breadcrumb"] a, ol a').count();
    result.breadcrumb = breadcrumbLinks;
    if (breadcrumbLinks < 2 && !url.includes('/industries/')) {
      // Non-industry pages may not have breadcrumbs
    } else if (breadcrumbLinks < 2 && url.includes('/industries/')) {
      result.issues.push({ type: 'BROKEN_BREADCRUMB', severity: 'P1', detail: `only ${breadcrumbLinks} levels` });
    }

    // 8-module check (for L4 pages)
    if (url.split('/').length >= 8) { // deep enough to be L4
      const modules = {
        pain: !!(body.match(/解决|方案|痛点/)),
        spec: !!(body.match(/规格|参数|标准|技术指标/)),
        faq: !!(body.match(/常见问题|技术问答|FAQ/)),
        cta: !!(body.match(/报价|联系我们|获取|询价|获取报价/)),
      };
      result.modules = modules;
      result.moduleCount = Object.values(modules).filter(Boolean).length;
    }

  } catch (e) {
    result.status = 'error';
    result.issues.push({ type: 'RUNTIME_ERROR', severity: 'P0', detail: e.message.slice(0, 150) });
  }

  // Small delay between pages to avoid rate limiting
  try { await page.waitForTimeout(1500); } catch { /* page may have closed */ }

  return result;
}

// ===================== Main =====================
(async () => {
  const report = {
    auditId: `AUDIT-${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 12)}`,
    timestamp: new Date().toISOString(),
    baseUrl: BASE,
    lang: LANG,
    results: [],
  };

  const browser = await chromium.launch({
    headless: true,
    executablePath: findChromium(),
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  // Helper: each page gets a fresh context to isolate crashes
  async function auditPage(url, label) {
    const { page, context } = await setupPage(browser);
    try {
      return await checkPage(page, url, label);
    } finally {
      try { await context.close(); } catch {}
    }
  }

  try {
    if (args.mode === 'healthcheck') {
      const industries = ['power', 'mining', 'agriculture', 'industrial', 'municipal'];
      for (const ind of industries) {
        report.results.push(await auditPage(`/${LANG}/industries/${ind}`, `L1-${ind}`));
      }
      for (const ind of ['power']) {
        const { l2, l4 } = discoverPages(ind, 100);
        for (const p of l2) {
          report.results.push(await auditPage(`/${LANG}/industries/${ind}/${p.l2}`, `L2-${p.name}`));
        }
        const sampled = sample(l4, 10);
        for (const p of sampled) {
          report.results.push(await auditPage(`/${LANG}${p.url}`, `L4-${p.name}`));
        }
      }
    } else if (args.url) {
      report.results.push(await auditPage(args.url, 'single'));
    } else {
      const industry = args.industry || 'power';
      const rate = parseInt(args.sample || '30');
      const { l2, l3, l4 } = discoverPages(industry, rate);

      report.results.push(await auditPage(`/${LANG}/industries/${industry}`, 'L1'));
      
      for (const p of l2) {
        report.results.push(await auditPage(`/${LANG}/industries/${industry}/${p.l2}`, `L2-${p.name}`));
      }
      
      const sampledL3 = sample(l3, rate);
      for (const p of sampledL3) {
        report.results.push(await auditPage(`/${LANG}${p.url}`, `L3-${p.name}`));
      }
      
      const sampledL4 = sample(l4, rate);
      for (const p of sampledL4) {
        report.results.push(await auditPage(`/${LANG}${p.url}`, `L4-${p.name}`));
      }

      report.metadata = {
        industry,
        sampleRate: `${rate}%`,
        totalL2: l2.length,
        totalL3: l3.length,
        totalL4: l4.length,
        auditedL3: sampledL3.length,
        auditedL4: sampledL4.length,
      };
    }
  } finally {
    await browser.close();
  }

  // Compute summary
  const total = report.results.length;
  const httpErrors = report.results.filter(r => r.status >= 400 || r.status === 'error').length;
  const blankPages = report.results.filter(r => r.issues.some(i => i.type === 'BLANK_PAGE')).length;
  const totalIssues = report.results.reduce((sum, r) => sum + r.issues.length, 0);
  const p0Count = report.results.reduce((sum, r) => sum + r.issues.filter(i => i.severity === 'P0').length, 0);
  const p1Count = report.results.reduce((sum, r) => sum + r.issues.filter(i => i.severity === 'P1').length, 0);

  report.summary = {
    pagesChecked: total,
    httpErrors,
    blankPages,
    totalIssues,
    p0Blocking: p0Count,
    p1Important: p1Count,
  };

  // Module coverage (L4 only)
  const l4Results = report.results.filter(r => r.modules);
  if (l4Results.length > 0) {
    const totalModules = l4Results.length * 4;
    const coveredModules = l4Results.reduce((s, r) => s + r.moduleCount, 0);
    report.summary.moduleCoverage = `${coveredModules}/${totalModules} (${Math.round(coveredModules / totalModules * 100)}%)`;
  }

  console.log(JSON.stringify(report, null, 2));
})();
