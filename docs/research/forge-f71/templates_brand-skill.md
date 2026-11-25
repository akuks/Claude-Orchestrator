---
name: brand
description: Use when creating or changing anything a user of this product will see (screens, components, emails, documents, copy), so it follows the product's own brand rules.
---
<!--
JSPARK Forge brand skill template (spec 5.14). Copy to skills/brand/SKILL.md in your project and fill in every <...>.
Keep it short: rules Claude can follow and check, not a style guide essay. Record the rules that must never break as
decisions too (/jspark-forge:decide, Touches: your UI folders), so Claude sees them just before it edits those files.
-->
Follow these brand rules for <product name> in everything a user sees. If a request conflicts with them, say so and ask.

0. Tokens. Input: the screen or file being changed. Action: take every colour, size, space, radius, shadow and motion
   value from the design tokens in <e.g. app.css, Tailwind v4 @theme block>, including the dark-mode values; never
   write a raw value that is not a token. Output: the changed file.
1. Colours. Input: the screen or file being changed. Action: use only these colours: <primary #......> for the one
   main action or accent per screen, <text #......>, <background #......>, <muted #......>, status colours
   <success #......> / <warning #......> / <error #......> only for status. Never other shades, never the accent as a
   background wash. Output: the changed file.
2. Typography. Input: the same file. Action: one typeface, <font name>, weights <regular> and <bold> only; sizes from
   this scale only: <e.g. 12, 14, 16, 20, 24, 32, 48>. Output: the changed file.
3. Spacing and layout. Input: the same file. Action: spacing from this scale only: <e.g. 4, 8, 12, 16, 24, 32, 48>;
   <corner radius>; <max content width>; it must work at phone width without sideways scrolling. Output: the changed
   file.
4. Components. Input: the same file. Action: use the project's existing components first (<component library or
   folder>, built on <primitive layer, e.g. shadcn/ui on Radix or Base UI>); never hand-roll a modal, menu, select,
   tabs or tooltip. A new component matches their look. Output: the changed file.
4a. Accessibility. Input: the same file. Action: text contrast at least WCAG AA (4.5:1, 3:1 for large text); a visible
   focus ring on everything clickable; touch targets at least 44 by 44 px; every input has a label; everything works
   with the keyboard alone; motion respects prefers-reduced-motion. Output: the changed file.
4b. States. Input: the screen. Action: design and build every state a user can meet: loading, empty, error, partial
   data, long text, slow network, signed out. Output: the changed file.
4c. Avoid. Input: the screen. Action: none of the generic AI look: <e.g. purple gradients on white, glassmorphism,
   emoji in headings, centred everything, five accent colours, stock 3D blobs>. Output: the changed file.
5. Voice. Input: any text a user reads. Action: <tone in one line, e.g. "direct, specific, no hype">; never use: <words
   to avoid>; numbers as <numerals / units rule>. Output: the changed text.
6. Verify: the Pre-ship check. Input: the finished change. Action: check each rule above against the running screen
   (open it with Playwright MCP and take a screenshot; Chrome DevTools MCP for console errors and layout shift) or the
   text, then run the project's checks: `npx playwright test tests/ui`, `npx playwright test tests/a11y`,
   `npx @lhci/cli autorun` (templates in JSPARK Forge's templates/ui/). Evidence: the screenshot path, the three
   commands' results, and "all rules met" or the rules broken with where.
