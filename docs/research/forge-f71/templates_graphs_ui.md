# Graph: ui
- max_parallel: 1

<!--
A screen or feature with a user interface, built so it is designed, approved, built, checked in a browser and reviewed.
Before running it, install the companions JSPARK Forge names in projects with a front end (frontend-design, Playwright
MCP, and shadcn for React), and fill in the product's brand skill (templates/brand-skill.md).
The three checks come ready to copy from JSPARK Forge's templates/ui/ (research 2026-11-20, docs/ui-research.md):
- tests/ui/*.spec.ts (visual.spec.ts): Playwright compares each screen with the approved look (toHaveScreenshot);
- tests/a11y/*.spec.ts (a11y.spec.ts): axe checks WCAG 2.1 A/AA at phone and desktop width, light and dark;
- lighthouserc.json: Lighthouse CI scores (accessibility 0.95, best practices 0.9, layout shift 0.1; performance warns).
Once: npm i -D @playwright/test @axe-core/playwright @lhci/cli, then npx playwright install.
After approve-look, record the reference screenshots once: npx playwright test tests/ui --update-snapshots.
Builders commit their step's work on the current branch (no merge, no push): the review step needs those commits.
-->
<!-- Optional: "- lock: <name>" here lets this graph run beside graphs that carry another lock name. With no lock line it runs alone: its helper steps and checks never overlap another graph's (use that when test runs share a database). -->

## direction (agent)
- helper: planner
- brief: .jspark-forge/briefs/ui-direction.md
- gives: direction_path

## approve-look (human)
- after: direction
- say: Open the design direction (and any mockup it links), check it against your brand, and reply DONE approve-look when the look is right; ask for changes first if not.

## build (agent)
- helper: builder
- after: approve-look
- needs: direction_path
- brief: .jspark-forge/briefs/ui-build.md
- gives: ui_commit

<!-- Set this to the project's own test command (pnpm test, yarn test, pytest, go test ./..., …) before you trust the graph. -->
## screens (gate)
- after: build
- run: npx playwright test tests/ui
- run: npx playwright test tests/a11y
- run: npx @lhci/cli autorun

## design-review (agent)
- helper: reviewer
- after: screens
- needs: direction_path, ui_commit
- brief: .jspark-forge/briefs/ui-review.md
- gives: review_path

## approve-ui (human)
- after: design-review
- say: Read the design review, open the screens yourself, and reply DONE approve-ui to accept the UI.
