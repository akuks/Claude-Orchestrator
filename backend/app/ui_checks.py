"""Phase 7f: the UI checks a project can add (visual, accessibility, Lighthouse) and the Brand guide template.
The file bodies are adapted from JSPARK Forge's templates (MIT)."""

VISUAL_PATH = "tests/ui/visual.spec.ts"
A11Y_PATH = "tests/a11y/a11y.spec.ts"
LIGHTHOUSE_PATH = "lighthouserc.json"

VISUAL_SPEC = "// Adapted from JSPARK Forge (MIT).\nimport { test, expect } from '@playwright/test';\n\n// Fill SCREENS from this project's routes (one entry per screen and state).\nconst SCREENS = [\n  { name: 'home', path: '/' },\n  // { name: 'settings-empty', path: '/settings?state=empty' },\n];\n\nfor (const screen of SCREENS) {\n  for (const width of [390, 1280]) {\n    test(`${screen.name} looks as approved at ${width}px`, async ({ page }) => {\n      await page.setViewportSize({ width, height: 900 });\n      await page.goto(screen.path);\n      await expect(page).toHaveScreenshot(`${screen.name}-${width}.png`, { fullPage: true, maxDiffPixelRatio: 0.01 });\n    });\n  }\n}\n"

A11Y_SPEC = "// Adapted from JSPARK Forge (MIT).\nimport { test, expect } from '@playwright/test';\nimport AxeBuilder from '@axe-core/playwright';\n\n// Fill SCREENS from this project's routes (one entry per screen and state).\nconst SCREENS = [\n  { name: 'home', path: '/' },\n  // { name: 'checkout-error', path: '/checkout?state=error' },\n];\nconst VIEWPORTS = [{ width: 390, height: 844 }, { width: 1280, height: 800 }];\n\nfor (const screen of SCREENS) {\n  for (const viewport of VIEWPORTS) {\n    for (const colorScheme of ['light', 'dark'] as const) {\n      test(`${screen.name} has no detectable accessibility violations (${viewport.width}px, ${colorScheme})`, async ({ page }) => {\n        await page.setViewportSize(viewport);\n        await page.emulateMedia({ colorScheme });\n        await page.goto(screen.path);\n        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();\n        expect(results.violations.map(v => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);\n      });\n    }\n  }\n}\n"

LIGHTHOUSERC = '{\n  "ci": {\n    "collect": {\n      "url": ["http://localhost:3000/"],\n      "startServerCommand": "npm run start",\n      "numberOfRuns": 3\n    },\n    "assert": {\n      "assertions": {\n        "categories:accessibility": ["error", { "minScore": 0.95 }],\n        "categories:best-practices": ["error", { "minScore": 0.9 }],\n        "categories:performance": ["warn", { "minScore": 0.8 }],\n        "cumulative-layout-shift": ["error", { "maxNumericValue": 0.1 }]\n      }\n    },\n    "upload": { "target": "filesystem", "outputDir": ".lighthouseci" }\n  }\n}\n'

BRAND_PATH = "brand/brand.md"
BRAND_TITLE = "Brand guide"
BRAND_ABSTRACT = ("Brand rules: tokens, colours, type, spacing, components, accessibility, states, voice. "
                  "Read before changing anything a user sees.")
BRAND_BODY = 'Follow these brand rules for <product name> in everything a user sees. If a request conflicts with them, say so and ask.\n\n0. Tokens. Input: the screen or file being changed. Action: take every colour, size, space, radius, shadow and motion\n   value from the design tokens in <e.g. app.css, Tailwind v4 @theme block>, including the dark-mode values; never\n   write a raw value that is not a token. Output: the changed file.\n1. Colours. Input: the screen or file being changed. Action: use only these colours: <primary #......> for the one\n   main action or accent per screen, <text #......>, <background #......>, <muted #......>, status colours\n   <success #......> / <warning #......> / <error #......> only for status. Never other shades, never the accent as a\n   background wash. Output: the changed file.\n2. Typography. Input: the same file. Action: one typeface, <font name>, weights <regular> and <bold> only; sizes from\n   this scale only: <e.g. 12, 14, 16, 20, 24, 32, 48>. Output: the changed file.\n3. Spacing and layout. Input: the same file. Action: spacing from this scale only: <e.g. 4, 8, 12, 16, 24, 32, 48>;\n   <corner radius>; <max content width>; it must work at phone width without sideways scrolling. Output: the changed\n   file.\n4. Components. Input: the same file. Action: use the project\'s existing components first (<component library or\n   folder>, built on <primitive layer, e.g. shadcn/ui on Radix or Base UI>); never hand-roll a modal, menu, select,\n   tabs or tooltip. A new component matches their look. Output: the changed file.\n4a. Accessibility. Input: the same file. Action: text contrast at least WCAG AA (4.5:1, 3:1 for large text); a visible\n   focus ring on everything clickable; touch targets at least 44 by 44 px; every input has a label; everything works\n   with the keyboard alone; motion respects prefers-reduced-motion. Output: the changed file.\n4b. States. Input: the screen. Action: design and build every state a user can meet: loading, empty, error, partial\n   data, long text, slow network, signed out. Output: the changed file.\n4c. Avoid. Input: the screen. Action: none of the generic AI look: <e.g. purple gradients on white, glassmorphism,\n   emoji in headings, centred everything, five accent colours, stock 3D blobs>. Output: the changed file.\n5. Voice. Input: any text a user reads. Action: <tone in one line, e.g. "direct, specific, no hype">; never use: <words\n   to avoid>; numbers as <numerals / units rule>. Output: the changed text.\n6. Verify: the Pre-ship check. Input: the finished change. Action: check each rule above against the running screen\n   (open it with Playwright MCP and take a screenshot; Chrome DevTools MCP for console errors and layout shift) or the\n   text, then run the project\'s checks: `npm run test:ui`, `npm run test:a11y`, `npm run test:lighthouse`. Evidence: the screenshot path, the three\n   commands\' results, and "all rules met" or the rules broken with where.\n'

SCRIPTS = {
    "test:ui": "playwright test tests/ui --reporter=list",
    "test:a11y": "playwright test tests/a11y --reporter=list",
    "test:lighthouse": "lhci autorun",
    "test:ui:update": "playwright test tests/ui --update-snapshots",
}
GATE_COMMANDS = ["npm run test:ui", "npm run test:a11y", "npm run test:lighthouse"]
INSTALL = "npm i -D @playwright/test @axe-core/playwright @lhci/cli"

SETUP_TITLE = "Set up UI checks"


def _fence(lang: str, text: str) -> str:
    return f"```{lang}\n{text.rstrip()}\n```"


def setup_prompt() -> str:
    scripts = "\n".join(f'- `{k}` = `{v}`' for k, v in SCRIPTS.items())
    return f"""Set up three browser checks in this project: screenshots at two widths, an accessibility scan, and Lighthouse budgets. Use the file contents below exactly, apart from filling in SCREENS.

Rules:
1. Create each check file only if no file exists at that path. Never overwrite or edit an existing file; if one exists, leave it and report it in your final answer.
2. Add `playwright.config.ts` only if the project has no Playwright config. It must set `baseURL` to where this project's app runs locally (for example http://localhost:3000).
3. In package.json add these scripts (do not change scripts that already exist; report any that clash):
{scripts}
4. Install the dev dependencies with exactly this one command: `{INSTALL}`. Do not run anything else with npx or npm beyond that install, and do not install browsers or run the checks.
5. Fill SCREENS in both spec files from this project's routes: one entry per real screen, plus its important states.
6. Commit your changes on the current branch. Do not push.
7. Finish with a short list of what you added, what you left alone, and what a person must do next: run `npx playwright install` once, then record the screenshot baselines once after approving the look (`npm run test:ui:update`), on the same operating system that runs the checks.

File 1: `{VISUAL_PATH}`
{_fence("ts", VISUAL_SPEC)}

File 2: `{A11Y_PATH}`
{_fence("ts", A11Y_SPEC)}

File 3: `{LIGHTHOUSE_PATH}`
{_fence("json", LIGHTHOUSERC)}
"""
