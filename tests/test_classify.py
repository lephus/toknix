"""P1-T2: program_of and classify (A2.3)."""
import unittest

import support  # noqa: F401  (puts src/ on sys.path)

from toknix.filters import classify, program_of, statements

# (cmd, statements and pipe stages): P11-T2, split outside quotes; the & of 2>&1, >&2 and &> is not a separator
STATEMENT_CASES = [
    ("npm test", ["npm test"]),
    ("ls a && cat b", ["ls a", "cat b"]),
    ("a || b; c | d |& e", ["a", "b", "c", "d", "e"]),
    ("npm test\ngit status", ["npm test", "git status"]),
    ("npm test 2>&1 | tail -5", ["npm test 2>&1", "tail -5"]),
    ("make &> log & echo started", ["make &> log", "echo started"]),
    ("cmd >&2", ["cmd >&2"]),
    ("git log --format='%h|%s' -5", ["git log --format='%h|%s' -5"]),
    ('echo "a && b; c"', ['echo "a && b; c"']),
    ('echo "say \\" && x"', ['echo "say \\" && x"']),
    ("echo a\\;b && ls", ["echo a\\;b", "ls"]),
    ("ls;", ["ls"]),
    ("", []),
]

# (cmd, expected program, expected args)
PROGRAM_CASES = [
    ("pytest -q", "pytest", ["-q"]),
    ("VAR=x pytest", "pytest", []),
    ("CI=1 NODE_ENV=test npx jest --ci", "jest", ["--ci"]),
    ("env FOO=1 pytest -x", "pytest", ["-x"]),
    ("time pytest", "pytest", []),
    ("sudo docker build .", "docker", ["build", "."]),
    ("uv run pytest tests/", "pytest", ["tests/"]),
    ("poetry run mypy src", "mypy", ["src"]),
    ("python -m pytest -x", "pytest", ["-x"]),
    ("python3.11 -m pip install requests", "pip", ["install", "requests"]),
    (".venv/bin/python -m pytest -x", "pytest", ["-x"]),  # python by path (P1-T4)
    ("/usr/bin/python3.12 -m pytest", "pytest", []),
    ("C:/Python39/python.exe -m pytest", "pytest", []),
    ("python.exe -m pip install x", "pip", ["install", "x"]),
    ("/usr/bin/find . -name '*.py'", "find", [".", "-name", "*.py"]),
    ("./gradlew test", "./gradlew", ["test"]),
    ("npm test | tail -5", "npm", ["test"]),
    ("cd sub && npm test", "cd", ["sub"]),
    ("pytest -k 'unbalanced", "pytest", ["-k", "'unbalanced"]),
    ("", "", []),
    ("FOO=1", "", []),
]

# (cmd, expected kind)
CLASSIFY_CASES = [
    # pytest
    ("pytest -q", "pytest"),
    ("py.test tests", "pytest"),
    ("python -m pytest -x", "pytest"),
    ("uv run pytest tests/", "pytest"),
    ("VAR=x pytest", "pytest"),
    ("pytest; echo done", "pytest"),
    # jest
    ("npx jest", "jest"),
    ("npx vitest run", "jest"),
    ("mocha", "jest"),
    ("npm test", "jest"),
    ("npm t", "jest"),
    ("npm run test:unit", "jest"),
    ("pnpm run test", "jest"),
    ("yarn test", "jest"),
    ("bun test", "jest"),
    ("CI=1 npx jest --ci", "jest"),
    # install
    ("yarn", "install"),
    ("npm install", "install"),
    ("npm ci", "install"),
    ("pnpm add lodash", "install"),
    ("pip install -r requirements.txt", "install"),
    ("python -m pip install requests", "install"),
    ("uv pip install requests", "install"),
    ("uv sync", "install"),
    ("poetry install", "install"),
    ("composer update", "install"),
    ("bundle install", "install"),
    # go / cargo
    ("go test ./...", "gotest"),
    ("cargo test", "cargotest"),
    ("cargo nextest run", "cargotest"),
    ("cargo clippy", "compile"),
    # lint
    ("tsc --noEmit", "lint"),
    ("npx eslint .", "lint"),
    ("ruff check .", "lint"),
    ("poetry run mypy src", "lint"),
    # git
    ("git status", "gitstatus"),
    ("git log --oneline", "gitlog"),
    ("git diff HEAD", "gitdiff"),
    ("git show abc123", "gitdiff"),
    ("git push", "gitnet"),
    ("git -C dir status", "gitstatus"),
    ("git -C sub log -5", "gitlog"),
    ("git -c color.ui=never diff", "gitdiff"),
    ("git --no-pager log", "gitlog"),
    ("git --git-dir=.git --work-tree=. status", "gitstatus"),
    ("git --git-dir .git fetch", "gitnet"),
    # listing
    ("ls -la", "listing"),
    ("/usr/bin/find . -name x", "listing"),
    ("tree", "listing"),
    ("du -sh .", "listing"),
    # build
    ("sudo docker build .", "build"),
    ("./gradlew test", "build"),
    ("make -j4", "build"),
    ("mvn package", "build"),
    ("terraform plan", "build"),
    # compound (P11-T2): every statement counts; echo, printf, true, false are neutral
    ("ls src && cat src/a.ts", "generic"),
    ("git status && git diff", "generic"),
    ("git status && echo --- && git status -s", "gitstatus"),
    ("echo start && npm test", "jest"),
    ("npm test 2>&1 | tail -30", "generic"),
    ("ls a; ls b", "listing"),
    ("git log --format='%h|%s'", "gitlog"),
    # generic
    ("echo hi", "generic"),
    ("", "generic"),
    ("cd x && npm test", "generic"),
    ("npm run build", "generic"),
    ("yarn build", "generic"),
    ("go build ./...", "generic"),
    ("git branch", "generic"),
    ("git -C dir", "generic"),
    ("echo 'oops", "generic"),
]


class TestProgramOf(unittest.TestCase):
    def test_table(self):
        for cmd, prog, args in PROGRAM_CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(program_of(cmd), (prog, args))


class TestStatements(unittest.TestCase):
    def test_table(self):
        for cmd, expected in STATEMENT_CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(statements(cmd), expected)


class TestClassify(unittest.TestCase):
    def test_table(self):
        self.assertGreaterEqual(len(CLASSIFY_CASES), 25)
        for cmd, kind in CLASSIFY_CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(classify(cmd), kind)


if __name__ == "__main__":
    unittest.main()
