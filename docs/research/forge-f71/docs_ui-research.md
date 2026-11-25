# UI/UX research: best-in-class open-source tools (2026-11-20)

Research for spec 5.14 at the owner's request ("best in class open-source git repo for ui ux"). Stars and dates from the
GitHub API on 2026-11-20; Windows support inferred from the tooling, not tested. JSPARK Forge recommends these; it
bundles and installs none of them.

## What JSPARK Forge uses

| Use | Pick | Licence | Where in JSPARK Forge |
|---|---|---|---|
| Design direction | Anthropic `frontend-design` (claude-plugins-official) | Apache-2.0 | named in the UI tip |
| Look at the running page | Playwright MCP (microsoft/playwright-mcp, 39.9k) | Apache-2.0 | named in the UI tip |
| Console, performance, layout shift on the live page | Chrome DevTools MCP (ChromeDevTools/chrome-devtools-mcp, 58k; in claude-plugins-official) | Apache-2.0 | named in the UI tip |
| Accessible components, code in your repo | shadcn/ui (128.6k) + its MCP, on Radix or Base UI primitives | MIT | named in the UI tip (React) |
| Visual check | Playwright test `toHaveScreenshot` (microsoft/playwright, 100.6k) | Apache-2.0 | `ui` graph gate; `templates/ui/visual.spec.ts` |
| Accessibility check | axe-core + `@axe-core/playwright` (dequelabs, 7.9k) | MPL-2.0 (file-level copyleft; fine as a dev dependency) | `ui` graph gate; `templates/ui/a11y.spec.ts` |
| Quality scores | Lighthouse CI (GoogleChrome/lighthouse-ci, 7.2k) | Apache-2.0 | `ui` graph gate; `templates/ui/lighthouserc.json` |
| Design tokens | Tailwind CSS v4 `@theme` (98.7k) | MIT | brand-skill template, rule 0 |

## Considered, not adopted (yet)

- **Base UI** (MIT), **React Aria** (Apache-2.0), **Ark UI** (MIT), **Mantine** (MIT): good primitive layers; the brand
  skill names whichever the project uses. Radix's push activity slowed in 2026-10 (not verified as a trend).
- **Style Dictionary** (Apache-2.0): only when tokens are shared across platforms. **Open Props** (MIT), **Tokens
  Studio** (MIT, Figma users only).
- **eslint-plugin-jsx-a11y** (MIT): useful static lint for React; quiet since 2026-01, flat-config support unverified.
- **Storybook MCP / addon-mcp** (MIT): only for projects that use Storybook.
- **Figma MCP**: hosted, not open source; free plans are limited (about 6 calls a month per its guide).
- **Community design skills** (ibelick/ui-skills, nextlevelbuilder/ui-ux-pro-max-skill, Owl-Listener/designer-skills,
  vercel-labs/web-interface-guidelines; all MIT): content quality not verified; revisit after reading them.
- **modern-web-guidance** (Google, in claude-plugins-official): content and licence not inspected yet.
- **BackstopJS** (MIT), **pa11y** (LGPL-3.0): redundant with Playwright and axe. **Lost Pixel**: archived, avoid.
- Open design systems (GOV.UK, Primer, Carbon): reference reading only.

## Not verified

Windows behaviour of each tool; Radix's trend; the community skills' content; jsx-a11y with ESLint 9/10; the Storybook
plugin's install syntax; the Figma quota; star inflation on the largest skill repo.
