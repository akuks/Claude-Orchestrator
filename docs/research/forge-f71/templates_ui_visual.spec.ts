// JSPARK Forge UI template: visual gate. Copy to tests/ui/<screen>.spec.ts.
// The first run after the user approves the look records the reference screenshots:
//   npx playwright test tests/ui --update-snapshots
// Afterwards every run compares against them and fails (exit code 1) when a screen changed beyond the tolerance.
// Record and check on the same operating system: fonts render differently on Windows, macOS and Linux.
import { test, expect } from '@playwright/test';

const SCREENS = [
  { name: 'home', path: '/' },
  // { name: 'settings-empty', path: '/settings?state=empty' },
];

for (const screen of SCREENS) {
  for (const width of [390, 1280]) {
    test(`${screen.name} looks as approved at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(screen.path);
      await expect(page).toHaveScreenshot(`${screen.name}-${width}.png`, { fullPage: true, maxDiffPixelRatio: 0.01 });
    });
  }
}
