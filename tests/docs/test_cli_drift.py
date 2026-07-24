"""CLI commands and flags mentioned in the docs actually exist.

Catches the ``thestill process`` / ``--no-narrate`` class of drift. Only
code spans and fenced blocks are scanned — prose is never interpreted.
"""

import re

import click

from tests.docs.helpers import code_text, iter_doc_files, read_doc

# Same-line only ([ \t], not \s): "image: thestill\nos: linux" must not
# read as `thestill os`. A captured token starting with "thestill" is the
# container/script name case (`docker exec thestill thestill list`,
# `thestill-mcp`), not a subcommand.
_CMD_RE = re.compile(r"(?<![\w/.-])thestill[ \t]+([a-z][a-z0-9-]*)")
_FLAG_RE = re.compile(r"(--[a-z][a-z0-9-]+)")


def _cli_group():
    from thestill.cli import main

    return main


def _all_commands(group, prefix=""):
    names = {}
    for name, cmd in group.commands.items():
        full = f"{prefix}{name}"
        names[full] = cmd
        if isinstance(cmd, click.Group):
            names.update(_all_commands(cmd, prefix=f"{full} "))
    return names


def _command_flags(cmd) -> set[str]:
    flags = set()
    for param in cmd.params:
        flags.update(o for o in param.opts if o.startswith("--"))
        flags.update(o for o in param.secondary_opts if o.startswith("--"))
    flags.add("--help")
    return flags


def test_documented_cli_commands_exist():
    commands = _all_commands(_cli_group())
    top_level = {name for name in commands if " " not in name}
    unknown = []
    for doc in iter_doc_files():
        for match in _CMD_RE.finditer(code_text(read_doc(doc))):
            name = match.group(1)
            if name.startswith("thestill"):
                continue
            if name not in top_level:
                unknown.append(f"{doc.name}: thestill {name}")
    assert not unknown, "Docs reference CLI commands that do not exist:\n" + "\n".join(sorted(set(unknown)))


def test_documented_cli_flags_exist():
    """For fenced command lines (`thestill <cmd> [subcmd] --flags`),
    every --flag must be a real option of that (sub)command."""
    commands = _all_commands(_cli_group())
    problems = []
    for doc in iter_doc_files():
        for line in code_text(read_doc(doc)).splitlines():
            match = re.search(r"(?<![\w/.-])thestill[ \t]+(.+)$", line.strip())
            if not match:
                continue
            tokens = match.group(1).split()
            if not tokens or tokens[0] not in commands:
                continue
            cmd_name = tokens[0]
            cmd = commands[cmd_name]
            if isinstance(cmd, click.Group) and len(tokens) > 1 and f"{cmd_name} {tokens[1]}" in commands:
                cmd_name = f"{cmd_name} {tokens[1]}"
                cmd = commands[cmd_name]
            valid = _command_flags(cmd)
            for flag in _FLAG_RE.findall(line):
                if flag.split("=", 1)[0] not in valid:
                    problems.append(f"{doc.name}: `thestill {cmd_name}` has no {flag}")
    assert not problems, "Docs reference CLI flags that do not exist:\n" + "\n".join(sorted(set(problems)))
