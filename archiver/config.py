"""Configuration loader and site config helpers."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any


class Config:
    """Loads and provides access to config.json."""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self._data: dict = {}
        self.reload()

    def reload(self):
        with open(self.config_path, "r") as f:
            self._data = json.load(f)

    @property
    def database_path(self) -> str:
        return self._data["database"]["path"]

    @property
    def images_dir(self) -> str:
        return self._data["storage"]["images_dir"]

    @property
    def snapshots_dir(self) -> str:
        return self._data["storage"]["snapshots_dir"]

    @property
    def scraping(self) -> dict:
        return self._data["scraping"]

    @property
    def antidetect(self) -> dict:
        return self._data["antidetect"]

    def get_enabled_sites(self) -> list[dict]:
        return [s for s in self._data["sites"] if s.get("enabled", True)]

    def get_all_sites(self) -> list[dict]:
        return self._data["sites"]

    def get_site(self, slug: str) -> dict | None:
        for s in self._data["sites"]:
            if s["slug"] == slug:
                return s
        return None

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
