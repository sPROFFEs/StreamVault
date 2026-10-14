"""Version shared by the server, Android Gradle build and release tooling."""
import json
from pathlib import Path


VERSION = json.loads(Path(__file__).with_name("version.json").read_text())["version"]
