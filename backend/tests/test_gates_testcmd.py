import pytest

from app.gates.testcmd import looks_like_test


@pytest.mark.parametrize("cmd", [
    "pytest", "pytest -q tests/", "python -m pytest", "python3 -m pytest -x", "py.test", "npm test",
    "npm run test:unit", "yarn test", "pnpm test", "npx vitest run", "npx jest", "npx playwright test",
    "vitest", "jest --ci", "mocha", "node --test", "go test ./...", "CI=1 NODE_ENV=test npm test",
])
def test_true(cmd):
    assert looks_like_test(cmd)


@pytest.mark.parametrize("cmd", [
    "cat pytest.ini", "echo npm test", 'git commit -m "go test"', "", "   ", "FOO=1", "ls", "npm install",
    "python script.py", 'echo "unterminated pytest', "npx playwright install",
])
def test_false(cmd):
    assert not looks_like_test(cmd)
