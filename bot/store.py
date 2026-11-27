"""JSON persistence + publishing to the `data` branch (read by the dashboard)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class Store:
    def __init__(self, root: str | Path, publish_enabled: bool = False):
        self.root = Path(root)
        (self.root / "days").mkdir(parents=True, exist_ok=True)
        self.publish_enabled = publish_enabled
        if not (self.root / "index.json").exists():
            self._write(self.root / "index.json", {"days": []})

    # ------------------------------------------------------------------ files
    def _write(self, path: Path, obj) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def load_day(self, date: str) -> dict | None:
        p = self.root / "days" / f"{date}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def save_day(self, day: dict) -> None:
        self._write(self.root / "days" / f"{day['id']}.json", day)
        self._update_index(day)

    def _update_index(self, day: dict) -> None:
        p = self.root / "index.json"
        idx = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"days": []}
        row = {"date": day["id"], "status": day["status"], "half_day": day["session"].get("half_day"),
               "demo": day.get("demo", False), "scorer": day.get("scorer"), **(day.get("summary") or {})}
        idx["days"] = [r for r in idx["days"] if r["date"] != day["date"]] + [row]
        idx["days"].sort(key=lambda r: r["date"], reverse=True)
        self._write(p, idx)

    def latest_day(self) -> str | None:
        p = self.root / "index.json"
        if not p.exists():
            return None
        days = json.loads(p.read_text(encoding="utf-8")).get("days", [])
        return days[0]["date"] if days else None

    def write_latest(self, obj: dict) -> None:
        self._write(self.root / "latest.json", obj)

    # ------------------------------------------------------------------ publish
    def publish(self) -> None:
        """Force-push the data folder as a single commit to the `data` branch (GitHub Actions only)."""
        if not self.publish_enabled:
            return
        repo = os.environ.get("GITHUB_REPOSITORY")
        token = os.environ.get("GITHUB_TOKEN")
        if not repo or not token:
            log.warning("publish skipped: GITHUB_REPOSITORY / GITHUB_TOKEN not set")
            return
        tmp = Path(tempfile.mkdtemp(prefix="pub-"))
        try:
            shutil.copytree(self.root, tmp, dirs_exist_ok=True)
            remote = f"https://x-access-token:{token}@github.com/{repo}.git"
            cmds = [
                ["git", "init", "-q", "-b", "data"],
                ["git", "add", "-A"],
                ["git", "-c", "user.name=news-sim-bot", "-c", "user.email=bot@users.noreply.github.com",
                 "commit", "-q", "-m", "data snapshot"],
                ["git", "push", "-q", "-f", remote, "data"],
            ]
            for c in cmds:
                r = subprocess.run(c, cwd=tmp, capture_output=True, text=True, timeout=60)
                if r.returncode != 0:
                    log.warning("publish step failed: %s", (r.stderr or r.stdout).replace(token, "***")[:300])
                    return
            log.info("published data snapshot")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
