// JSPARK Forge UI template: accessibility gate. Copy to tests/a11y/<screen>.spec.ts and list your screens and states.
// Needs: npm i -D @playwright/test @axe-core/playwright, and `npx playwright install` once.
// Fails (exit code 1) on any WCAG 2.1 A/AA violation that axe can detect. axe finds part of all accessibility problems;
// keyboard paths and screen-reader wording still need a person.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const SCREENS = [
  { name: 'home', path: '/' },
  // { name: 'checkout-error', path: '/checkout?state=error' },
];
const VIEWPORTS = [{ width: 390, height: 844 }, { width: 1280, height: 800 }];

for (const screen of SCREENS) {
  for (const viewport of VIEWPORTS) {
    for (const colorScheme of ['light', 'dark'] as const) {
      test(`${screen.name} has no detectable accessibility violations (${viewport.width}px, ${colorScheme})`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await page.emulateMedia({ colorScheme });
        await page.goto(screen.path);
        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
        expect(results.violations.map(v => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
      });
    }
  }
}
