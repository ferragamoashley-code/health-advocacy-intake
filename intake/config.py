"""Load the prototype's config files (config/ at the repo root)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_settings():
    with open(CONFIG_DIR / "settings.yaml") as fh:
        return yaml.safe_load(fh)


def load_schema():
    with open(CONFIG_DIR / "case_schema.yaml") as fh:
        return yaml.safe_load(fh)


def load_note_template():
    return (CONFIG_DIR / "note_template.md").read_text()


def outputs_dir(settings):
    path = ROOT / settings["outputs_dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path
