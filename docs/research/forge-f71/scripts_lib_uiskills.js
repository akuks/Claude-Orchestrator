'use strict';
// v0.5 "use the UI/UX skills" (owner 2026-11-20). Installing frontend-design is not enough: in another project it was
// loaded once, the builder helpers loaded no design skill and the owner's brand skill was never applied. So in your
// own project with a front end, every session start (and every helper that builds, plans or reviews) is told to load
// the design skill and the brand skill before changing anything a user sees.
// The brand skill is named with "- brand-skill: <name>" under ## Settings in rules.md (your own
// ~/.claude/jspark-forge/rules.md for every project; a project's rules.md for that project), or is the project's own
// brand skill file (templates/brand-skill.md copied into the project). Skills that live in the user's Claude account
// cannot be seen from disk, so with none named Claude is told to look in its own skills list.
// Off: "- ui-skills: off". Own projects only. Every read fails silently.
const fs = require('fs');
const path = require('path');
const paths = require('./paths');

const FD_SKILL = 'frontend-design:frontend-design';
const BRAND_FILES = [['.claude', 'skills', 'brand', 'SKILL.md'], ['skills', 'brand', 'SKILL.md']];
// Helpers that never design or build: no rule for them.
const NO_RULE = /(^|:)(checker|explore|claude-code-guide|statusline-setup|web-fetch)$/i;

const settingsOf = (dir, home) => require('./rules').loadRules(dir, home ? { claudeHome: home } : {}).settings;

// -> the brand skill's name, or null when none is named and the project has no brand skill file.
function brand(dir, home) {
  try {
    const named = settingsOf(dir, home)['brand-skill'];
    if (named) return named;
    if (BRAND_FILES.some(p => fs.existsSync(path.join(dir, ...p)))) return 'brand';
  } catch { /* fail open */ }
  return null;
}

// -> { on, frontendDesign, brand } for an own project with a front end, else null. on: false = switched off.
function facts(dir, home = paths.claudeHome()) {
  try {
    if (!paths.isOwn(dir) || !require('./uitip').hasFrontEnd(dir)) return null;
    const on = settingsOf(dir, home)['ui-skills'] !== 'off';
    return { on, frontendDesign: require('./companions').installed(home, 'frontend-design'), brand: brand(dir, home) };
  } catch { return null; }
}

function text(f, forHelper) {
  const fd = f.frontendDesign ? `the frontend-design skill (${FD_SKILL})` : `the frontend-design skill if you have it (${FD_SKILL})`;
  const br = f.brand
    ? `the brand skill ${f.brand}`
    : 'a brand skill if your skills list has one (a skill with "brand" in its name)';
  const none = f.brand ? '' : ' If there is no brand skill, ask the user which brand rules apply before you choose colours or a typeface yourself.';
  const helpers = forHelper ? '' : ' When you brief a helper for such work, tell it to load the same skills, by name.';
  return 'JSPARK Forge UI rule: this project has a front end. Before you design, build or change anything a user sees ' +
    `(screens, components, styles, copy), load ${fd} and ${br} with the Skill tool, and follow them.${none} ` +
    `Check the result on the real page (a screenshot) before you say it is done.${helpers} ` +
    'The user can switch this rule off with "- ui-skills: off" under ## Settings in rules.md.';
}

// The line for Claude at session start, or null.
function line(dir, home) {
  const f = facts(dir, home);
  return f && f.on ? text(f, false) : null;
}
// The line for a helper when it starts, or null (helpers that only check or search get none).
function helperLine(dir, agentType, home) {
  if (NO_RULE.test(String(agentType || ''))) return null;
  const f = facts(dir, home);
  return f && f.on ? text(f, true) : null;
}

module.exports = { line, helperLine, facts, brand, FD_SKILL };
