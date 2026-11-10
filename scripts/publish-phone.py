"""Publish the phone's app: build it from what's committed and put it on the VPS, where the phone finds it and updates itself
(server/natro_agent/app_updates.py, android/.../AppUpdates.kt). Its version is the number of commits, so commit first, or
the phone won't see it as newer.

Usage: python scripts/publish-phone.py [--no-build]
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
APK = ANDROID / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main():
    version = int(git("rev-list", "--count", "HEAD"))
    if "--no-build" not in sys.argv:
        gradle = ANDROID / ("gradlew.bat" if os.name == "nt" else "gradlew")
        subprocess.run([str(gradle), "assembleDebug", "--console=plain"], cwd=ANDROID, check=True)
    data = APK.read_bytes()
    published = {"version": version, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    host, _, folder = git("remote", "get-url", "vps").partition(":")
    app = f"{folder}/app"
    subprocess.run(["ssh", host, f"mkdir -p {app}"], check=True)
    subprocess.run(["scp", "-q", str(APK), f"{host}:{app}/natro.apk.part"], check=True)
    with tempfile.TemporaryDirectory() as temporary:
        said = Path(temporary) / "app.json"
        said.write_text(json.dumps(published), encoding="utf-8")
        subprocess.run(["scp", "-q", str(said), f"{host}:{app}/app.json.part"], check=True)
    # The build first, and what's said of it last: a phone told of it can fetch it.
    subprocess.run(["ssh", host, f"mv {app}/natro.apk.part {app}/natro.apk && mv {app}/app.json.part {app}/app.json"], check=True)
    print(f"Published version {version}, {len(data) / 2**20:.1f} MB: the phone updates itself within a minute or so.")


if __name__ == "__main__":
    main()
