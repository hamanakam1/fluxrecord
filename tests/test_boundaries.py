"""The boundary is a rule, not a promise.

fluxrecord's value rests on three claims about the core ledger: it opens no
socket, it runs no subprocess, and it does not know the adapters that sit
around it. Those claims are only worth something if a machine checks them,
so this module checks them.

The rules, in order of how badly they would hurt to break:

  1. The core may not import anything that can reach the network or start a
     process. The core ledger is a file, a lock, and a set of rules; the
     moment it can make a call, "local-first" stops being a property and
     becomes a promise.
  2. The core may not import an adapter. The dependency arrow points one
     way: adapters know about the ledger, the ledger knows nothing about
     them. A core that imports an adapter can no longer be used *underneath*
     one.
  3. The core may not use a dynamic import or an exec to slip past the two
     rules above.
  4. The core may not write to stdout. Stdout is the CLI's data channel and
     it carries JSON; the ledger is a library, and a library returns values.

A rule that is only written down decays. These four are written down here
*and* checked here, and the check is written so that adding a forbidden
import makes it fail — see `CheckerTest` at the bottom, which proves the
checker still works rather than trusting that it does.

Allowing a new package module in the core is a deliberate act: it means
editing `CORE_ALLOWED` in this file, where the reason has to be written next
to it. The default is no.
"""

from __future__ import annotations

import ast
import tempfile
import tomllib
import unittest
from pathlib import Path

PACKAGE = "fluxrecord"
ROOT = Path(__file__).resolve().parent.parent / PACKAGE
CORE = f"{PACKAGE}.core"

#: Standard-library modules that can reach the network or start a process.
#: The check is on the root package, so "urllib.request" is caught by
#: "urllib". The third-party names are here so that adding a dependency
#: cannot quietly become a boundary violation.
FORBIDDEN_STDLIB = frozenset(
    {
        # network
        "socket",
        "ssl",
        "http",
        "urllib",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "nntplib",
        "telnetlib",
        "xmlrpc",
        "webbrowser",
        "asyncio",
        # process execution
        "subprocess",
        "multiprocessing",
        "pty",
        "ctypes",
        "runpy",
        # dynamic import: an escape hatch around every rule above
        "importlib",
        # third party
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "paramiko",
        "boto3",
    }
)

#: Package modules the core ledger may import. The package's own
#: `__init__` is always allowed, because Python executes it whenever the
#: core is imported.
CORE_ALLOWED = frozenset({CORE})

#: Builtins the core may not call. `__import__`, `eval` and `exec` would
#: let a module reach anything the rules above forbid; `print` and
#: `breakpoint` would write to the CLI's data channel.
FORBIDDEN_BUILTINS = frozenset(
    {"__import__", "eval", "exec", "compile", "print", "globals", "locals", "vars", "breakpoint"}
)

#: Attribute paths the core may not call.
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "os.system",
        "os.popen",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.execl",
        "os.execle",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
    }
)

#: Attribute paths the core may not touch, matched as a prefix.
FORBIDDEN_PREFIXES = ("sys.stdout", "sys.stderr", "sys.__stdout__", "sys.__stderr__")


# --------------------------------------------------------------------------- #
# the import graph
# --------------------------------------------------------------------------- #


def module_name(path: Path, root: Path) -> str:
    """The dotted name of a module, from its path."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(path: Path, root: Path) -> str:
    """The package a module lives in, which is what a relative import is
    resolved against."""
    parts = list(path.relative_to(root).parent.parts)
    if parts and parts[-1] == PACKAGE:
        return PACKAGE
    return ".".join(parts)


def resolve_relative(package: str, level: int, module: str | None) -> str:
    """Resolve a relative import to an absolute module name."""
    base = package
    for _ in range(level - 1):
        base = base.rpartition(".")[0]
    if module:
        return f"{base}.{module}" if base else module
    return base


def imports_of(path: Path, package: str) -> set[str]:
    """Every module name the file imports, absolute and relative alike."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = resolve_relative(package, node.level, node.module)
                if node.module:
                    found.add(base)
                else:
                    for alias in node.names:
                        found.add(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                found.add(node.module)
    return found


def dotted(node: ast.AST) -> str:
    """The dotted path of an attribute expression, as a string."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def escape_hatches(path: Path) -> list[str]:
    """The calls in a file that would slip past the import rules."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in FORBIDDEN_BUILTINS:
                found.append(f"{func.id}()")
        elif isinstance(func, ast.Attribute):
            name = dotted(func)
            if name in FORBIDDEN_ATTRIBUTES:
                found.append(f"{name}()")
            elif name.startswith(FORBIDDEN_PREFIXES):
                found.append(f"{name}()")
    return found


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #


def check(files: dict[str, Path], root: Path, start: str) -> list[str]:
    """The boundary violations in a package, starting from one module.

    `files` maps a dotted module name to its file. `start` is the module
    whose import closure is examined.
    """
    problems: list[str] = []

    # The closure: every package module reachable from the starting module.
    closure: set[str] = set()
    pending = [start]
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        closure.add(name)
        path = files.get(name)
        if path is None:
            continue
        package = package_of(path, root)
        for target in imports_of(path, package):
            if target in files and target not in closure:
                pending.append(target)

    for name in sorted(closure):
        path = files[name]
        package = package_of(path, root)
        for target in sorted(imports_of(path, package)):
            if target in files:
                if target not in CORE_ALLOWED and target != PACKAGE:
                    problems.append(f"{name} imports {target}; the core may not import an adapter")
                continue
            root_name = target.split(".")[0]
            if root_name in FORBIDDEN_STDLIB:
                problems.append(
                    f"{name} imports {target}; the core may not reach the network "
                    f"or start a process"
                )
        for call in escape_hatches(path):
            problems.append(f"{name} calls {call}; the core may not use a dynamic escape")

    return problems


def package_files(root: Path = ROOT) -> tuple[dict[str, Path], Path]:
    """Every module in the package, keyed by dotted name."""
    base = root.parent
    files = {
        module_name(path, base): path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    return files, base


# --------------------------------------------------------------------------- #
# the tests
# --------------------------------------------------------------------------- #


class BoundaryTest(unittest.TestCase):
    """The core ledger's boundary, checked."""

    def test_the_core_imports_nothing_that_can_reach_outside(self):
        files, base = package_files()
        self.assertIn(CORE, files, "the core module is missing")
        problems = check(files, base, CORE)
        self.assertEqual(problems, [], "the core broke its own boundary")

    def test_the_core_imports_no_adapter(self):
        files, base = package_files()
        closure = set()
        pending = [CORE]
        while pending:
            name = pending.pop()
            if name in closure:
                continue
            closure.add(name)
            path = files.get(name)
            if path is None:
                continue
            for target in imports_of(path, package_of(path, base)):
                if target in files and target not in closure:
                    pending.append(target)
        adapters = closure - CORE_ALLOWED - {PACKAGE}
        self.assertEqual(adapters, set(), "the core may not import an adapter")

    def test_the_core_writes_to_nothing(self):
        files, _ = package_files()
        for name in (CORE, PACKAGE):
            self.assertEqual(escape_hatches(files[name]), [], f"{name} writes or escapes")

    def test_the_package_has_no_runtime_dependencies(self):
        """The core is stdlib-only, and so is the package it ships."""
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        dependencies = data.get("project", {}).get("dependencies", [])
        self.assertEqual(dependencies, [], "the package must have no runtime dependencies")


class CheckerTest(unittest.TestCase):
    """The checker has to actually catch things.

    A gate that cannot fail is decoration, so these build a tiny package
    that breaks each rule and assert that the rule is reported.
    """

    def setUp(self) -> None:
        self._temps: list[tempfile.TemporaryDirectory[str]] = []

    def tearDown(self) -> None:
        for temp in self._temps:
            temp.cleanup()

    def build(self, **modules: str) -> tuple[dict[str, Path], Path]:
        temp = tempfile.TemporaryDirectory()
        self._temps.append(temp)
        root = Path(temp.name) / PACKAGE
        root.mkdir(parents=True)
        files: dict[str, Path] = {}
        for name, source in modules.items():
            path = root / f"{name}.py"
            path.write_text(source, encoding="utf-8")
            files[f"{PACKAGE}.{name}"] = path
        return files, Path(temp.name)

    def test_a_network_import_is_caught(self):
        files, base = self.build(core="import socket\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("socket" in problem for problem in problems), problems)

    def test_a_subprocess_import_is_caught(self):
        files, base = self.build(core="import subprocess\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("subprocess" in problem for problem in problems), problems)

    def test_a_nested_network_import_is_caught(self):
        files, base = self.build(
            core="from urllib import request\n",
        )
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("urllib" in problem for problem in problems), problems)

    def test_an_adapter_import_is_caught(self):
        files, base = self.build(
            core="from . import github_sync\n",
            github_sync="import json\n",
        )
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("github_sync" in problem for problem in problems), problems)

    def test_an_adapter_reached_transitively_is_caught(self):
        files, base = self.build(
            core="from . import helper\n",
            helper="from . import github_sync\n",
            github_sync="import json\n",
        )
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("github_sync" in problem for problem in problems), problems)

    def test_a_dynamic_import_is_caught(self):
        files, base = self.build(core="import importlib\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("importlib" in problem for problem in problems), problems)

    def test_a_dynamic_import_call_is_caught(self):
        files, base = self.build(core="name = 'socket'\nmod = __import__(name)\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("__import__" in problem for problem in problems), problems)

    def test_a_print_is_caught(self):
        files, base = self.build(core="print('hello')\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("print" in problem for problem in problems), problems)

    def test_writing_to_stdout_is_caught(self):
        files, base = self.build(core="import sys\nsys.stdout.write('x')\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("sys.stdout" in problem for problem in problems), problems)

    def test_running_a_process_is_caught(self):
        files, base = self.build(core="import os\nos.system('ls')\n")
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertTrue(any("os.system" in problem for problem in problems), problems)

    def test_a_clean_core_is_not_reported(self):
        files, base = self.build(
            core="import json\nimport sqlite3\n\ndef load(text: str) -> dict:\n    return json.loads(text)\n"
        )
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertEqual(problems, [], problems)

    def test_a_stdlib_import_inside_an_allowed_module_is_not_reported(self):
        """Only the core's own closure is checked, not the whole package."""
        files, base = self.build(
            core="import json\n",
            dashboard="import socket\n",
        )
        problems = check(files, base, f"{PACKAGE}.core")
        self.assertEqual(problems, [], problems)


if __name__ == "__main__":
    unittest.main()
