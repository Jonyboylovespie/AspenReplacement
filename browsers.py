"""Resolve the user's browser preference and support Firefox's session protocol."""
import configparser
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from urllib.parse import urlparse


@dataclass(frozen=True)
class BrowserChoice:
    command: tuple
    engine: str
    name: str


def browser_engine(identifier):
    identifier = identifier.lower()
    if any(name in identifier for name in ("firefox", "zen", "librewolf", "floorp", "waterfox")):
        return "firefox"
    if any(name in identifier for name in ("chrome", "chromium", "brave", "vivaldi", "edge", "opera")):
        return "chromium"
    raise ValueError("Automatic sign-in supports Firefox, Zen, and Chromium-based browsers. Choose one as your default browser and try again.")


def desktop_browser(desktop_id):
    if Path(desktop_id).name != desktop_id or not desktop_id.endswith(".desktop"):
        raise ValueError("The default browser's application entry is invalid.")
    roots = [Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))]
    roots += [Path(path) for path in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if path]
    roots += [Path.home() / ".local/share/flatpak/exports/share", Path("/var/lib/flatpak/exports/share")]
    for root in roots:
        path = root / "applications" / desktop_id
        if not path.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
        tokens = shlex.split(entry["Exec"])
        command = tuple(token.replace("%%", "%") for token in tokens
                        if token not in {"%u", "%U", "%f", "%F", "%i", "%c", "%k", "@@u", "@@", "--file-forwarding"})
        if not command or not shutil.which(command[0]):
            raise ValueError("Your default browser's executable was not found.")
        name = entry.get("Name", desktop_id)
        return BrowserChoice(command, browser_engine(desktop_id + " " + name + " " + " ".join(command)), name)
    raise ValueError("Your default browser's application entry was not found.")


def default_browser():
    configured = os.environ.get("BETTERASPEN_BROWSER")
    if configured:
        if configured.endswith(".desktop"):
            return desktop_browser(configured)
        executable = shutil.which(configured)
        if not executable:
            raise ValueError("The configured sign-in browser was not found. Check BETTERASPEN_BROWSER and try again.")
        return BrowserChoice((executable,), browser_engine(executable), Path(executable).name)
    if shutil.which("xdg-settings"):
        try:
            result = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode == 0 and result.stdout.strip():
            # An unsupported preference is an error, never a silent browser switch.
            return desktop_browser(result.stdout.strip())
    return None


def profile_command(choice, profile):
    command = list(choice.command)
    if Path(command[0]).name == "flatpak" and "run" in command:
        index = command.index("run") + 1
        # Grant this instance only its dedicated profile directory. No file forwarding
        # or document portal is needed to open the web sign-in page.
        command[index:index] = ["--no-documents-portal", "--filesystem=" + str(profile.resolve())]
    return command


def firefox_command(choice, profile, port, url, headless=False):
    command = profile_command(choice, profile)
    if headless:
        command.append("--headless")
    command += ["--no-remote", "--profile", str(profile.resolve()),
                "--remote-debugging-port", str(port), "--new-window", url]
    return command


class FirefoxSession:
    def __init__(self, websocket):
        self.websocket = websocket
        self.sequence = 0

    def command(self, method, params):
        self.sequence += 1
        sequence = self.sequence
        self.websocket.send(json.dumps({"id": sequence, "method": method, "params": params}))
        while True:
            result = json.loads(self.websocket.recv(timeout=10))
            if result.get("id") != sequence:
                continue
            if result.get("type") == "error":
                raise RuntimeError("The Firefox session command failed.")
            return result.get("result", {})

    def start(self, url):
        self.command("session.new", {"capabilities": {}})
        contexts = self.command("browsingContext.getTree", {})["contexts"]
        if not contexts:
            raise RuntimeError("No Firefox sign-in window is available.")
        if not any(context["url"] == url for context in contexts):
            self.command("browsingContext.navigate", {"context": contexts[0]["context"], "url": url, "wait": "none"})

    def is_open(self):
        return bool(self.command("browsingContext.getTree", {}).get("contexts"))

    def cookies(self, urls):
        targets = [urlparse(url) for url in urls]
        selected = []
        for cookie in self.command("storage.getCookies", {}).get("cookies", []):
            domain = cookie["domain"].lstrip(".")
            path = cookie["path"]
            if not any((target.hostname == domain or target.hostname.endswith("." + domain))
                       and (target.path == path or target.path.startswith(path.rstrip("/") + "/"))
                       for target in targets):
                continue
            value = cookie["value"]
            if value.get("type") != "string":
                continue
            selected.append({"name": cookie["name"], "value": value["value"], "domain": cookie["domain"],
                             "path": path, "secure": cookie["secure"], "httpOnly": cookie["httpOnly"],
                             "expires": cookie.get("expiry")})
        return selected

    def close(self):
        try:
            self.command("browser.close", {})
        except Exception:
            pass
        self.websocket.close()
