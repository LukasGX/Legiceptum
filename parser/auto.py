from datetime import datetime
import io
import json
import os
from pathlib import Path
import zipfile
from dotenv import load_dotenv
import requests

from law_parser import parse_law

load_dotenv()

API_KEY = os.getenv("ANTHROPIC_API_KEY")

def download_law(slug: str):
    url = f"https://www.gesetze-im-internet.de/{slug}/xml.zip"

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml_filename = next(
            name for name in archive.namelist()
            if name.lower().endswith(".xml")
        )

        xml_content = archive.read(xml_filename)

    output_dir = Path("imported")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{slug}.xml"
    output_file.write_bytes(xml_content)

    return output_file

def extract_metadata(response_data: dict) -> dict:
    content = response_data.get("content", [])

    text_blocks = [
        block["text"]
        for block in content
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    ]

    if not text_blocks:
        print(json.dumps(response_data, indent=4))
        raise ValueError("Claude returned no text block")

    raw_text = "\n".join(text_blocks).strip()

    try:
        metadata = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")

        if start == -1 or end == -1:
            print(json.dumps(response_data, indent=4))
            raise ValueError(f"No json found: {raw_text}")

        metadata = json.loads(raw_text[start:end + 1])

    required = {"desc", "field", "icon"}

    if not isinstance(metadata, dict):
        raise ValueError("Answer is not valid json")

    if not required.issubset(metadata):
        print(json.dumps(response_data, indent=4))
        raise ValueError(f"Missing fields: {required - metadata.keys()}")

    if not all(isinstance(metadata[key], str) and metadata[key].strip()
               for key in required):
        print(json.dumps(response_data, indent=4))
        raise ValueError("Metadata contain invalid or empty values")

    return metadata

def gen_bare_file(filename, id):
    abbr = input(f"Enter abbr for {id}: ")
    name = input(f"Input name for {id}: ")

    prompt = f"""
    Du bist ein juristischer Metadaten-Assistent für Legiceptum,
    eine Plattform zur Bereitstellung und Aufbereitung deutscher Gesetze.

    Deine Aufgabe ist es, anhand der übergebenen Gesetzesinformationen
    drei hochwertige Metadatenfelder zu generieren.

    GESETZESINFORMATIONEN:
    - ID: {id}
    - Abkürzung: {abbr}
    - Vollständiger Name: {name}

    GENERIERTE FELDER:

    1. desc
    Eine präzise, informative Beschreibung des Gesetzes.
    - Genau ein vollständiger Satz.
    - Beschreibe den wesentlichen Regelungsgegenstand.
    - Formuliere sachlich, verständlich und professionell.
    - Vermeide nichtssagende Formulierungen wie
    "Dieses Gesetz regelt verschiedene Aspekte ...".
    - Keine unbelegten Detailbehauptungen.
    - Maximal 150 Zeichen.

    2. field
    Das primäre juristische Rechtsgebiet des Gesetzes.
    - Verwende eine kurze, etablierte deutsche Bezeichnung.
    - Beispiele: Zivilrecht, Strafrecht, Gesellschaftsrecht,
    Verfassungsrecht, Verwaltungsrecht, Arbeitsrecht,
    Steuerrecht, Sozialrecht, Handelsrecht.
    - Wähle möglichst eine bereits etablierte Kategorie
    statt unnötig neue Bezeichnungen zu erfinden.
    - Gib genau ein Rechtsgebiet zurück.

    3. icon
    Ein passendes Icon aus Font Awesome Free Solid.
    - Verwende ausschließlich den Icon-Namen im kebab-case-Format.
    - Beispiele: scale-balanced, gavel, building-columns,
    chart-line, landmark, users, briefcase, file-contract.
    - Keine Präfixe wie "fa-" oder "fas".
    - Verwende ein semantisch passendes Icon.
    - Erfinde keine Icon-Namen.

    AUSGABEFORMAT:

    Antworte ausschließlich mit einem gültigen JSON-Objekt.
    Keine Markdown-Codeblöcke, keine Kommentare,
    keine Erklärungen und keine zusätzlichen Felder.

    Exaktes Schema:
    {{
        "desc": "Beschreibung",
        "field": "Rechtsgebiet",
        "icon": "font-awesome-icon"
    }}
    """

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-haiku-5-5",
            "max_tokens": 900,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    metadata = extract_metadata(result)

    data = {
        "id": id,
        "abbr": abbr,
        "name": name,
        "desc": metadata["desc"],
        "scope": "bund",
        "field": metadata["field"],
        "icon": metadata["icon"],
        "updated": "",
        "prefix": "§"
    }

    with open(filename, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=4, ensure_ascii=False))

def check_links(elements):
    dead = 0

    for el in elements:
        url = f"https://www.gesetze-im-internet.de/{el["slug"]}/"

        response = requests.get(url, timeout=10)

        if not response.ok:
            dead += 1
            print(f"{url} not found.")

    return True if dead == 0 else False

def main():
    claude_counter = 0

    with open("import.json", "r") as f:
        importfile = json.load(f)

    importing = importfile["import"] if importfile["override"] == [] else importfile["override"]

    bare_dir = Path("bare")
    bare_dir.mkdir(parents=True, exist_ok=True)

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not check_links(importing):
        return

    for imp in importing:
        filename = download_law(imp["slug"])
        laws = parse_law(filename, prefix=imp["abbr"])

        for law in laws:
            if law["id"] in importfile["no_prefix"]:
                law["no_prefix"] = True

        bare_file = bare_dir / f"{imp["abbr"]}.json"

        if not bare_file.exists():
            claude_counter += 1
            gen_bare_file(bare_file, imp["abbr"])

        with open(bare_dir / f"{imp["abbr"]}.json", "r") as f:
            bare = json.load(f)

        bare["laws"] = laws
        bare["updated"] = datetime.today().strftime("%Y-%m-%d")

        with open(output_dir / f"{imp["abbr"]}.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(bare, indent=4, ensure_ascii=False))

    print(f"Updated {len(importing)} books.")
    print(f"Called the Claude API {claude_counter} times.")

if __name__ == "__main__":
    main()