#!/usr/bin/env python3
"""Archive l'état des parkings Citédia (Rennes) dans UN SEUL fichier CSV.

- Bibliothèque standard uniquement.
- Fichier : data/parkings.csv  (une ligne par parking et par relevé)
- Première colonne : collected_at (heure de Paris, avec décalage +01:00/+02:00).
- Si les données sont identiques au dernier relevé, rien n'est ajouté.
- Si l'API ajoute un nouveau champ, le fichier est réécrit avec la colonne en plus.
- Code de sortie non nul en cas d'échec (le run GitHub passe en erreur).
"""
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

DATASET = "export-api-parking-citedia"
BASE = f"https://data.rennesmetropole.fr/api/explore/v2.1/catalog/datasets/{DATASET}/records"
PAGE_SIZE = 100
CSV_PATH = Path("data/parkings.csv")
TS = "collected_at"
TZ = ZoneInfo("Europe/Paris")


def get_json(url, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "rennes-parkings-archiver/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"Tentative {attempt + 1}/{tries} échouée : {exc}", file=sys.stderr)
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))


def fetch_all():
    results, offset = [], 0
    while True:
        page = get_json(f"{BASE}?limit={PAGE_SIZE}&offset={offset}")
        batch = page.get("results", [])
        results.extend(batch)
        total = page.get("total_count", len(results))
        offset += PAGE_SIZE
        if not batch or len(results) >= total:
            break
    return results


def flatten(d, prefix=""):
    """Aplatit un enregistrement : {"a": {"b": 1}} -> {"a.b": "1"}."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, (list, bool)):
            out[key] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            out[key] = ""
        else:
            out[key] = str(v)
    return out


def read_header():
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        return None
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def tail_rows(header, n):
    """Relit les n dernières lignes du CSV sans charger tout le fichier."""
    size = CSV_PATH.stat().st_size
    with CSV_PATH.open("rb") as f:
        f.seek(max(0, size - 256 * 1024))
        chunk = f.read().decode("utf-8", errors="ignore")
    rows = []
    for r in csv.reader(chunk.splitlines()[-n:]):
        if len(r) == len(header):
            rows.append(dict(zip(header, r)))
    return rows


def rewrite_with_header(old_header, new_header):
    tmp = CSV_PATH.with_suffix(".tmp")
    with CSV_PATH.open(newline="", encoding="utf-8") as src, tmp.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        writer = csv.DictWriter(dst, fieldnames=new_header, restval="", lineterminator="\n")
        writer.writeheader()
        for row in csv.DictReader(src):
            writer.writerow(row)
    tmp.replace(CSV_PATH)


def main():
    records = fetch_all()
    if not records:
        print("Réponse vide : rien enregistré.", file=sys.stderr)
        return 1

    rows = [flatten(r) for r in records]
    rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
    data_cols = sorted({c for r in rows for c in r})

    CSV_PATH.parent.mkdir(exist_ok=True)
    header = read_header()
    schema_changed = False

    if header is None:
        header = [TS] + data_cols
    else:
        missing = [c for c in data_cols if c not in header]
        if missing:
            new_header = header + missing
            rewrite_with_header(header, new_header)
            header = new_header
            schema_changed = True

    # Déduplication : on compare au dernier relevé
    if not schema_changed and CSV_PATH.exists() and CSV_PATH.stat().st_size > 0:
        cols = [c for c in header if c != TS]
        key = lambda r: tuple(r.get(c, "") for c in cols)
        previous = sorted(key(r) for r in tail_rows(header, len(rows)))
        if previous == sorted(key(r) for r in rows):
            print("Données inchangées, rien à écrire.")
            return 0

    now = datetime.now(TZ).isoformat(timespec="seconds")
    new_file = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, restval="", lineterminator="\n")
        if new_file:
            writer.writeheader()
        for r in rows:
            writer.writerow({TS: now, **r})
    print(f"{len(rows)} lignes ajoutées à {CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
