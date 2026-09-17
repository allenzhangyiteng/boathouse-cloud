"""Embed maintained starter files into the standalone bh executable."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "# BEGIN EMBEDDED STARTERS\n"
END = "# END EMBEDDED STARTERS\n"


def generated():
    allowed = {"team": ("app.py", "Dockerfile", "README.md", ".gitignore", ".dockerignore"),
               "static": ("index.html", "README.md", ".gitignore")}
    data = {kind: {name: (ROOT / "templates" / kind / name).read_text() for name in names}
            for kind, names in allowed.items()}
    return START + "STARTER_FILES = json.loads(" + repr(json.dumps(data, ensure_ascii=False)) + ")\n" + END


if __name__ == "__main__":
    path = ROOT / "cli" / "bh"
    before = path.read_text()
    a, b = before.index(START), before.index(END) + len(END)
    path.write_text(before[:a] + generated() + before[b:])
