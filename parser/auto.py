from datetime import datetime
import io
import json
from pathlib import Path
import zipfile
import requests

from law_parser import parse_law

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

def main():
    with open("import.json", "r") as f:
        importfile = json.load(f)

    importing = importfile["import"] if importfile["override"] == [] else importfile["override"]

    bare_dir = Path("bare")
    bare_dir.mkdir(parents=True, exist_ok=True)

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    for imp in importing:
        filename = download_law(imp["slug"])
        laws = parse_law(filename, prefix=imp["abbr"])

        for law in laws:
            if law["id"] in importfile["no_prefix"]:
                law["no_prefix"] = True

        with open(bare_dir / f"{imp["abbr"]}.json", "r") as f:
            bare = json.load(f)

        bare["laws"] = laws
        bare["updated"] = datetime.today().strftime("%Y-%m-%d")

        with open(output_dir / f"{imp["abbr"]}.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(bare, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    main()