#!/usr/bin/env python3
"""Capture the screenshots that ship in the README.

The screenshots are generated, not drawn: every frame in `docs/screenshots`
is the real output of the real CLI, captured against a throwaway ledger and
rendered as a terminal window. Re-run this script after a release and the
images tell the truth about the current build.

    python3 scripts/capture_screenshots.py [--out docs/screenshots]

Requires Google Chrome (or Chromium) for the final render. Nothing else is
needed: the script is stdlib-only, exactly like the package it documents.
"""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import pty
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #

BG = "#0b1020"
CHROME = "#141a2e"
FG = "#cbd5f5"
KEY = "#79c0ff"
STRING = "#a5d6ff"
NUMBER = "#f0b072"
LITERAL = "#ff8fa3"
PUNCT = "#6b7a99"
PROMPT = "#a78bfa"
COMMAND = "#e8ecff"
ERROR = "#ff7b72"

BASE16 = [
    "#000000",
    "#cd0000",
    "#00cd00",
    "#cdcd00",
    "#0000ee",
    "#cd00cd",
    "#00cdcd",
    "#e5e5e5",
    "#7f7f7f",
    "#ff0000",
    "#00ff00",
    "#ffff00",
    "#5c5cff",
    "#ff00ff",
    "#00ffff",
    "#ffffff",
]
CUBE = [0, 95, 135, 175, 215, 255]


def xterm256(index: int) -> str:
    """The rgb value of an xterm-256 palette entry."""
    if index < 16:
        return BASE16[index]
    if index < 232:
        offset = index - 16
        r = CUBE[offset // 36]
        g = CUBE[(offset // 6) % 6]
        b = CUBE[offset % 6]
        return f"#{r:02x}{g:02x}{b:02x}"
    level = 8 + (index - 232) * 10
    return f"#{level:02x}{level:02x}{level:02x}"


# --------------------------------------------------------------------------- #
# running the real CLI
# --------------------------------------------------------------------------- #


def _read_all(fd: int, sink: list[bytes]) -> None:
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        sink.append(chunk)


def run_pty(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[str, str]:
    """Run a command with a real tty on stdout and stderr.

    A tty matters: `flux art --color` only paints when stdout is a terminal,
    so a pipe would silently produce the uncoloured mark.
    """
    master_out, slave_out = pty.openpty()
    master_err, slave_err = pty.openpty()
    for master in (master_out, master_err):
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 240, 0, 0))
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=slave_out,
        stderr=slave_err,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_out)
    os.close(slave_err)
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    threads = [
        threading.Thread(target=_read_all, args=(master_out, out_chunks)),
        threading.Thread(target=_read_all, args=(master_err, err_chunks)),
    ]
    for thread in threads:
        thread.start()
    proc.wait()
    for thread in threads:
        thread.join()
    os.close(master_out)
    os.close(master_err)

    def decode(raw: list[bytes]) -> str:
        return b"".join(raw).decode("utf-8", "replace").replace("\r\n", "\n")

    return decode(out_chunks), decode(err_chunks)


class Flux:
    """A throwaway ledger driven through the real command line."""

    def __init__(self, workspace: Path, env: dict[str, str] | None = None) -> None:
        self.workspace = workspace
        # A relative ledger path keeps the machine's temporary directory out
        # of the screenshots, which are meant to look like real output.
        self.db = "ledger.sqlite"
        for variant in {str(workspace), str(workspace.resolve())}:
            if variant not in REDACTIONS:
                REDACTIONS.append(variant)
        REDACTIONS.sort(key=len, reverse=True)
        self.env = {
            **os.environ,
            "FLUX_DB": self.db,
            "TERM": "xterm-256color",
            "COLUMNS": "200",
            "PYTHONPATH": str(REPO),
        }
        self.env.pop("NO_COLOR", None)
        if env:
            self.env.update(env)

    def __call__(self, *args: str, env: dict[str, str] | None = None) -> tuple[str, str]:
        argv = [sys.executable, "-m", "fluxrecord", *args]
        environment = dict(self.env)
        if env:
            environment.update(env)
        return run_pty(argv, self.workspace, environment)

    def json(self, *args: str) -> dict:
        out, err = self(*args)
        stream = out.strip() or err.strip()
        try:
            return json.loads(stream)
        except json.JSONDecodeError:
            return {"_raw": stream}


# --------------------------------------------------------------------------- #
# text -> html
# --------------------------------------------------------------------------- #

ANSI = re.compile(r"\x1b\[([0-9;]*)m")


def esc(text: str) -> str:
    return html.escape(text, quote=False)


#: Paths that must not appear in a screenshot, longest first.
REDACTIONS: list[str] = []


def redact(text: str) -> str:
    """Hide machine-specific paths from a screenshot.

    The output is real, but the repository is public: the reader does not
    need the author's home directory or a temporary build path to see what
    the tool does.
    """
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "/Users/you")
    for path in REDACTIONS:
        text = text.replace(path, ".")
    return text


def highlight_json(text: str) -> str:
    """Colour a JSON document without a parser dependency."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    break
                end += 1
            raw = text[index : end + 1]
            probe = end + 1
            while probe < length and text[probe] in " \t":
                probe += 1
            kind = "k" if probe < length and text[probe] == ":" else "s"
            out.append(f'<span class="{kind}">{esc(raw)}</span>')
            index = end + 1
        elif char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
            end = index
            while end < length and (text[end].isdigit() or text[end] in ".-+eE"):
                end += 1
            out.append(f'<span class="n">{esc(text[index:end])}</span>')
            index = end
        elif (
            text.startswith("true", index)
            or text.startswith("false", index)
            or text.startswith("null", index)
        ):
            for word in ("true", "false", "null"):
                if text.startswith(word, index):
                    out.append(f'<span class="b">{word}</span>')
                    index += len(word)
                    break
        else:
            out.append(esc(char))
            index += 1
    return "".join(out)


def ansi_to_html(text: str) -> str:
    """Turn SGR coloured terminal output into inline-styled html.

    The style in force changes mid-line — the wordmark is a horizontal
    gradient — so every run of text is wrapped in the state that was active
    when it was emitted.
    """
    colour: str | None = None
    bold = False
    dim = False

    def wrap(content: str) -> str:
        if not content:
            return ""
        style = []
        if colour:
            style.append(f"color:{colour}")
        if bold:
            style.append("font-weight:700")
        if dim:
            style.append("opacity:.62")
        if not style:
            return content
        return f'<span style="{";".join(style)}">{content}</span>'

    out: list[str] = []
    position = 0
    for match in ANSI.finditer(text):
        out.append(wrap(esc(text[position : match.start()])))
        position = match.end()
        codes = [part for part in match.group(1).split(";") if part != ""] or ["0"]
        index = 0
        while index < len(codes):
            code = int(codes[index])
            if code == 0:
                colour, bold, dim = None, False, False
            elif code == 1:
                bold = True
            elif code == 2:
                dim = True
            elif code == 22:
                bold = dim = False
            elif code == 39:
                colour = None
            elif 30 <= code <= 37:
                colour = BASE16[code - 30]
            elif 90 <= code <= 97:
                colour = BASE16[code - 90 + 8]
            elif code == 38 and index + 2 < len(codes) and codes[index + 1] == "5":
                colour = xterm256(int(codes[index + 2]))
                index += 2
            elif code == 38 and index + 4 < len(codes) and codes[index + 1] == "2":
                red, green, blue = (int(codes[index + 2 + n]) for n in range(3))
                colour = f"#{red:02x}{green:02x}{blue:02x}"
                index += 4
            index += 1
    out.append(wrap(esc(text[position:])))
    return "".join(out)


def render_output(text: str) -> str:
    """Colour JSON when the output is JSON, otherwise keep it verbatim."""
    text = redact(text)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return ansi_to_html(text)
        return highlight_json(text)
    return ansi_to_html(text)


# --------------------------------------------------------------------------- #
# html page
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin:0; padding:0; background:{page_bg}; }}
  body {{ padding:26px; display:inline-block; }}
  .win {{ border-radius:12px; overflow:hidden; background:{chrome};
          box-shadow:0 18px 48px rgba(0,0,0,.45); border:1px solid rgba(255,255,255,.07); }}
  .bar {{ height:38px; display:flex; align-items:center; padding:0 14px; position:relative;
          background:linear-gradient(180deg,#1b2238,#151b2d); border-bottom:1px solid rgba(255,255,255,.06); }}
  .dot {{ width:12px; height:12px; border-radius:50%; margin-right:8px; }}
  .dot.r {{ background:#ff5f57; }} .dot.y {{ background:#febc2e; }} .dot.g {{ background:#28c840; }}
  .title {{ position:absolute; left:0; right:0; text-align:center; font-size:12.5px;
            color:#8fa0c8; letter-spacing:.02em; font-weight:600; }}
  .term {{ padding:18px 20px 20px; background:{bg}; }}
  pre {{ margin:0; font-family:ui-monospace,"SF Mono",Menlo,Monaco,"Cascadia Mono","Roboto Mono",monospace;
         font-size:14.5px; line-height:1.52; color:{fg};
         width:max-content; max-width:104ch; white-space:pre-wrap; overflow-wrap:break-word; }}
  .k {{ color:{key}; }} .s {{ color:{string}; }} .n {{ color:{number}; }} .b {{ color:{literal}; }}
  .p {{ color:{prompt}; }} .c {{ color:{command}; font-weight:600; }}
  .e {{ color:{error}; }} .d {{ color:{punct}; }}
</style></head><body>
<div class="win" id="win">
  <div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
    <span class="title">{title}</span></div>
  <div class="term"><pre id="term">{body}</pre></div>
</div>
<pre id="__metrics" style="display:none"></pre>
<script>
window.addEventListener('load', function () {{
  var win = document.getElementById('win');
  var box = win.getBoundingClientRect();
  document.getElementById('__metrics').textContent = JSON.stringify(
    {{w: Math.ceil(box.width), h: Math.ceil(box.height)}});
}});
</script>
</body></html>
"""


def page(title: str, body: str) -> str:
    return PAGE.format(
        page_bg="#070b16",
        chrome=CHROME,
        bg=BG,
        fg=FG,
        key=KEY,
        string=STRING,
        number=NUMBER,
        literal=LITERAL,
        punct=PUNCT,
        prompt=PROMPT,
        command=COMMAND,
        error=ERROR,
        title=title,
        body=body,
    )


def prompt_line(directory: str, command: str) -> str:
    return (
        f'<span class="p">~/{esc(directory)}</span> <span class="d">$</span> '
        f'<span class="c">{esc(command)}</span>'
    )


# --------------------------------------------------------------------------- #
# chrome
# --------------------------------------------------------------------------- #


def find_chrome() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("no Chrome or Chromium found; cannot render the screenshots")


def chrome_run(chrome: str, args: list[str]) -> subprocess.CompletedProcess:
    # Chrome's headless argument parser treats any space-separated value as a
    # second target, so every flag here has to use the --name=value form.
    return subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-networking",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def measure(chrome: str, html_path: Path) -> tuple[int, int]:
    """Ask the browser how tall the page actually is."""
    result = chrome_run(chrome, ["--dump-dom", f"file://{html_path}"])
    match = re.search(r'id="__metrics"[^>]*>(\{[^<]*\})<', result.stdout)
    if not match:
        raise SystemExit("could not measure the rendered page")
    metrics = json.loads(match.group(1).replace("&quot;", '"'))
    return int(metrics["w"]), int(metrics["h"])


def shoot(chrome: str, html_path: Path, png_path: Path, width: int, height: int) -> None:
    chrome_run(
        chrome,
        [
            f"--screenshot={png_path}",
            f"--window-size={width},{height}",
            "--force-device-scale-factor=2",
            f"file://{html_path}",
        ],
    )
    if not png_path.exists():
        raise SystemExit(f"chrome produced no image for {png_path}")


def render(chrome: str, title: str, body: str, png_path: Path, workdir: Path) -> None:
    html_path = workdir / (png_path.stem + ".html")
    html_path.write_text(page(title, body), encoding="utf-8")
    width, height = measure(chrome, html_path)
    shoot(chrome, html_path, png_path, width + 52, height + 52)


# --------------------------------------------------------------------------- #
# the scenes
# --------------------------------------------------------------------------- #


def scene_hero(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("art", "--style", "large", "--color")
    return "hero", "fluxrecord — flux art --style large --color", render_output(out.rstrip("\n"))


def scene_init(flux: Flux) -> tuple[str, str, str]:
    out, err = flux("init", "--project", ".fluxrecord")
    body = prompt_line("fluxrecord", "flux init --project .fluxrecord")
    body += "\n" + render_output(out.rstrip("\n"))
    if err.strip():
        body += "\n" + ansi_to_html(err.rstrip("\n"))
    return "init", "fluxrecord — flux init", body


def scene_lifecycle(flux: Flux) -> tuple[str, str, str]:
    lines: list[str] = []

    def step(command: str, out: str) -> None:
        lines.append(prompt_line("fluxrecord", command))
        if out.strip():
            lines.append(render_output(out.rstrip("\n")))
        lines.append("")

    out, _ = flux(
        "task-add",
        "M-397001",
        "--objective",
        "Implement JWT-based authentication for API endpoints",
        "--acceptance",
        "Tokens issued, refreshed, revoked and introspected",
        "--scope",
        "repo/auth",
        "--scope",
        "repo/api",
        "--budget",
        "8000",
        "--tag",
        "security",
    )
    step(
        "flux task-add M-397001 --objective 'Implement JWT auth' "
        "--acceptance 'Tokens issued, refreshed, revoked and introspected' "
        "--scope repo/auth --scope repo/api --budget 8000",
        out,
    )

    out, _ = flux("start", "M-397001", "--owner", "terminal-1", "--lease", "900")
    step("flux start M-397001 --owner terminal-1 --lease 900", out)
    session = json.loads(out)["session_id"]

    out, _ = flux(
        "checkpoint",
        session,
        "--summary",
        "Implemented token issuance and refresh rotation",
        "--evidence",
        "diff 4f2a91c; POST /api/auth/login returns an access+refresh pair",
        "--next",
        "Add revocation and introspection",
        "--tokens",
        "1200",
    )
    step(
        f"flux checkpoint {session[:8]}… --summary 'Token issuance done' "
        "--evidence 'diff 4f2a91c; POST /api/auth/login returns a pair' "
        "--next 'Add revocation and introspection' --tokens 1200",
        out,
    )

    out, _ = flux(
        "finish",
        session,
        "--evidence",
        "All five endpoints tested; diff 4f2a91c; 41 tests pass",
    )
    step(f"flux finish {session[:8]}… --evidence 'All five endpoints tested; diff 4f2a91c'", out)

    out, _ = flux(
        "decide",
        "M-397001",
        "accept",
        "--reason",
        "Reviewed the diff and the acceptance evidence",
    )
    step("flux decide M-397001 accept --reason 'Reviewed the diff and the evidence'", out)

    return "lifecycle", "fluxrecord — one ticket, one gate", "\n".join(lines).rstrip("\n")


def scene_failure(flux: Flux) -> tuple[str, str, str]:
    """A failure is a first-class record, not a stack trace."""
    flux(
        "task-add",
        "M-397142",
        "--objective",
        "Shrink the context packet without dropping evidence",
        "--acceptance",
        "A 12 kB packet still carries the objective and the last checkpoint",
        "--scope",
        "repo/core",
        "--budget",
        "4000",
    )
    out, _ = flux("start", "M-397142", "--owner", "terminal-2", "--lease", "900")
    session = json.loads(out)["session_id"]
    out, _ = flux(
        "fail",
        session,
        "--category",
        "tool",
        "--summary",
        "The reference implementation could not be built",
    )
    body = prompt_line(
        "fluxrecord",
        f"flux fail {session[:8]}… --category tool --summary 'The reference build failed'",
    )
    body += "\n" + render_output(out.rstrip("\n"))
    body += "\n" + prompt_line("fluxrecord", "flux list --status blocked")
    out, _ = flux("list", "--status", "blocked")
    body += "\n" + render_output(out.rstrip("\n"))
    return "failure", "fluxrecord — a visible failure", body


def scene_status(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("status")
    return (
        "status",
        "fluxrecord — flux status",
        prompt_line("fluxrecord", "flux status") + "\n" + render_output(out.rstrip("\n")),
    )


def scene_list(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("list", "--limit", "3")
    return (
        "list",
        "fluxrecord — flux list",
        prompt_line("fluxrecord", "flux list --limit 3") + "\n" + render_output(out.rstrip("\n")),
    )


def scene_show(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("show", "M-397142")
    return (
        "show",
        "fluxrecord — flux show M-397142",
        prompt_line("fluxrecord", "flux show M-397142") + "\n" + render_output(out.rstrip("\n")),
    )


def scene_pack(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("pack", "M-397001", "--max-bytes", "12000")
    return (
        "pack",
        "fluxrecord — flux pack M-397001",
        prompt_line("fluxrecord", "flux pack M-397001 --max-bytes 12000")
        + "\n"
        + render_output(out.rstrip("\n")),
    )


def scene_tools(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("tools", "--limit", "5")
    return (
        "tools",
        "fluxrecord — flux tools",
        prompt_line("fluxrecord", "flux tools --limit 5") + "\n" + render_output(out.rstrip("\n")),
    )


def scene_export(flux: Flux) -> tuple[str, str, str]:
    out, _ = flux("export", "--project", ".fluxrecord")
    return (
        "export",
        "fluxrecord — flux export",
        prompt_line("fluxrecord", "flux export --project .fluxrecord")
        + "\n"
        + render_output(out.rstrip("\n")),
    )


def scene_benchmark(flux: Flux) -> tuple[str, str, str]:
    """The first promise, measured rather than claimed.

    The ticket names a file that exists, so the comparison is against real
    bytes rather than a flattering estimate.
    """
    flux(
        "task-add",
        "M-397002",
        "--objective",
        "Implement JWT-based authentication for API endpoints",
        "--acceptance",
        "Tokens issued, refreshed, revoked and introspected",
        "--scope",
        "fluxrecord/core.py",
        "--budget",
        "8000",
    )
    out, _ = flux("benchmark", "M-397002", "--root", str(REPO))
    return (
        "benchmark",
        "fluxrecord — flux benchmark",
        prompt_line("fluxrecord", "flux benchmark M-397002 --root .")
        + "\n"
        + render_output(out.rstrip("\n")),
    )


def scene_doctor(flux: Flux) -> tuple[str, str, str]:
    """One report about the whole system."""
    out, _ = flux(
        "doctor",
        "--project",
        ".fluxrecord",
        "--sync-db",
        str(flux.workspace / "github.sqlite"),
    )
    return (
        "doctor",
        "fluxrecord — flux doctor",
        prompt_line("fluxrecord", "flux doctor --project .fluxrecord")
        + "\n"
        + render_output(out.rstrip("\n")),
    )


def scene_watch(flux: Flux) -> tuple[str, str, str]:
    """Real-time, with no server and no socket."""
    out, _ = flux("watch", "--once")
    return (
        "watch",
        "fluxrecord — flux watch --once",
        prompt_line("fluxrecord", "flux watch --once") + "\n" + render_output(out.rstrip("\n")),
    )


def scene_delta(flux: Flux) -> tuple[str, str, str]:
    """What changed, rather than the whole packet again."""
    # A ticket that is still workable: the rich ledger has already
    # accepted M-397001, and a finished ticket cannot be started again.
    task = "M-397203"
    out, _ = flux("pack", task)
    digest = json.loads(out)["packet_hash"]
    session = json.loads(flux("start", task, "--owner", "terminal-3")[0])["session_id"]
    flux(
        "checkpoint",
        session,
        "--summary",
        "Wrote the token route",
        "--evidence",
        "diff 4f2a91c",
        "--next",
        "Rotate the refresh token",
        "--tokens",
        "1200",
    )
    out, _ = flux("pack", task, "--since", digest, "--delta-only")
    return (
        "delta",
        "fluxrecord — flux pack --since",
        prompt_line("fluxrecord", "flux pack M-397203 --since <hash> --delta-only")
        + "\n"
        + render_output(out.rstrip("\n")),
    )


def scene_verify(flux: Flux) -> tuple[str, str, str]:
    """The event log, and the proof that it was not rewritten."""
    out, _ = flux("verify")
    return (
        "verify",
        "fluxrecord — flux verify",
        prompt_line("fluxrecord", "flux verify") + "\n" + render_output(out.rstrip("\n")),
    )


def seed(flux: Flux) -> None:
    """Build the ledger the scenes read from."""
    flux(
        "task-add",
        "M-397006",
        "--objective",
        "Add the middleware framework",
        "--acceptance",
        "A request passes through the middleware chain",
        "--scope",
        "repo/core",
        "--budget",
        "3000",
        "--tag",
        "core",
    )
    flux(
        "task-add",
        "M-397203",
        "--objective",
        "Expose the ledger over loopback HTTP",
        "--acceptance",
        "Only 127.0.0.1 is reachable; every write is still a CLI call",
        "--scope",
        "repo/api",
        "--budget",
        "6000",
        "--tag",
        "api",
        "--priority",
        "3",
    )

    out, _ = flux("start", "M-397006", "--owner", "terminal-1", "--lease", "900")
    session = json.loads(out)["session_id"]
    flux(
        "checkpoint",
        session,
        "--summary",
        "Middleware chain dispatches in order",
        "--evidence",
        "diff 91ab004; tests/test_adapters.py passes",
        "--next",
        "Wire the auth routes into the chain",
        "--tokens",
        "900",
    )
    flux("finish", session, "--evidence", "diff 91ab004; adapters covered")
    flux("decide", "M-397006", "accept", "--reason", "Reviewed the chain and the tests")


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def build_rich(workdir: Path) -> Flux:
    """A ledger with a finished ticket, a ready ticket and a failed one."""
    flux = Flux(workdir / "rich")
    flux.workspace.mkdir(parents=True, exist_ok=True)
    flux("init", "--project", ".fluxrecord")
    seed(flux)
    return flux


def build_fresh(workdir: Path) -> Flux:
    """An empty project, so `flux init` reports what it created."""
    flux = Flux(workdir / "fresh")
    flux.workspace.mkdir(parents=True, exist_ok=True)
    return flux


def build_small(workdir: Path) -> Flux:
    """A young project with one accepted ticket and one blocked ticket."""
    flux = Flux(workdir / "small")
    flux.workspace.mkdir(parents=True, exist_ok=True)
    flux("init", "--project", ".fluxrecord")
    flux(
        "task-add",
        "M-397001",
        "--objective",
        "Implement JWT-based authentication",
        "--acceptance",
        "Tokens issued, refreshed, revoked and introspected",
        "--scope",
        "repo/auth",
        "--budget",
        "8000",
        "--tag",
        "security",
    )
    out, _ = flux("start", "M-397001", "--owner", "terminal-1", "--lease", "900")
    session = json.loads(out)["session_id"]
    flux(
        "checkpoint",
        session,
        "--summary",
        "Token issuance and refresh rotation",
        "--evidence",
        "diff 4f2a91c; POST /api/auth/login returns a pair",
        "--next",
        "Add revocation and introspection",
        "--tokens",
        "1200",
    )
    flux("finish", session, "--evidence", "diff 4f2a91c; 41 tests pass")
    flux("decide", "M-397001", "accept", "--reason", "Reviewed the diff and the evidence")
    flux(
        "task-add",
        "M-397142",
        "--objective",
        "Shrink the context packet",
        "--acceptance",
        "A 12 kB packet still carries the objective",
        "--scope",
        "repo/core",
        "--budget",
        "4000",
        "--tag",
        "performance",
    )
    out, _ = flux("start", "M-397142", "--owner", "terminal-2", "--lease", "900")
    session = json.loads(out)["session_id"]
    flux(
        "fail",
        session,
        "--category",
        "tool",
        "--summary",
        "The reference implementation could not be built",
    )
    return flux


def build_signed(workdir: Path) -> Flux:
    """A ledger whose event log is chained, so `flux verify` has something
    to verify."""
    flux = Flux(workdir / "signed", env={"FLUX_EVENT_KEY": "a-signing-key-for-the-screenshot"})
    flux.workspace.mkdir(parents=True, exist_ok=True)
    flux("init", "--project", ".fluxrecord")
    flux(
        "task-add",
        "M-397001",
        "--objective",
        "Implement JWT-based authentication",
        "--acceptance",
        "Tokens issued, refreshed, revoked and introspected",
        "--scope",
        "repo/auth",
        "--budget",
        "8000",
        "--tag",
        "security",
    )
    out, _ = flux("start", "M-397001", "--owner", "terminal-1")
    session = json.loads(out)["session_id"]
    flux(
        "checkpoint",
        session,
        "--summary",
        "Token issuance and refresh rotation",
        "--evidence",
        "diff 4f2a91c; POST /api/auth/login returns a pair",
        "--next",
        "Add revocation and introspection",
        "--tokens",
        "1200",
    )
    flux("finish", session, "--evidence", "diff 4f2a91c; 41 tests pass")
    flux("decide", "M-397001", "accept", "--reason", "Reviewed the diff and the evidence")
    return flux


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO / "docs" / "screenshots"))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()

    with tempfile.TemporaryDirectory(prefix="flux-shots-") as tmp:
        workdir = Path(tmp)
        rich = build_rich(workdir)
        small = build_small(workdir)
        fresh = build_fresh(workdir)
        signed = build_signed(workdir)

        scenes = [
            scene_hero(rich),
            scene_init(fresh),
            scene_lifecycle(rich),
            scene_failure(rich),
            scene_list(rich),
            scene_show(rich),
            scene_pack(rich),
            scene_delta(rich),
            scene_benchmark(rich),
            scene_doctor(rich),
            scene_watch(rich),
            scene_verify(signed),
            scene_tools(rich),
            scene_export(rich),
            scene_status(small),
        ]
        for name, title, body in scenes:
            png = out_dir / f"{name}.png"
            render(chrome, title, body, png, workdir)
            print(f"wrote {png}")


if __name__ == "__main__":
    main()
