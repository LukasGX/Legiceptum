import json

def main():
    with open("import.json", "r") as f:
        contents = json.load(f)

    for e in contents["import"]:
        print(e["abbr"])

if __name__ == "__main__":
    main()