"""1.1 Coleta metadados do Repositório Institucional da UNIR (DSpace 7) para data/sources.csv.

Usa a API REST do DSpace (https://ri.unir.br/server/api), não raspa o HTML.

Uso:
    python -m src.ingest.fetch_unir --n 10                    # itens mais recentes
    python -m src.ingest.fetch_unir --n 10 --query amazonia   # busca por termo
    python -m src.ingest.fetch_unir --handles 7311 7298       # itens específicos

O repositório não informa licença em `dc.rights`; as linhas entram com
licenca="verificar" e devem ser conferidas manualmente antes de usar na dissertação.
"""
import argparse
from pathlib import Path

import httpx
import pandas as pd

API = "https://ri.unir.br/server/api"
SITE = "https://ri.unir.br/handle"
HEADERS = {"User-Agent": "research-client/0.1"}
COLUMNS = ["doc_id", "titulo", "fonte", "url", "licenca", "formato", "escaneado",
           "idioma", "tipo", "ano", "assuntos", "pagina", "sha256"]


def _meta(item: dict, key: str, sep: str = "; ") -> str:
    return sep.join(v["value"] for v in item["metadata"].get(key, []))


def search_items(client: httpx.Client, n: int, query: str | None) -> list[dict]:
    params = {"dsoType": "ITEM", "size": n, "sort": "dc.date.accessioned,DESC"}
    if query:
        params["query"] = query
    r = client.get(f"{API}/discover/search/objects", params=params)
    r.raise_for_status()
    objs = r.json()["_embedded"]["searchResult"]["_embedded"]["objects"]
    return [o["_embedded"]["indexableObject"] for o in objs]


def item_by_handle(client: httpx.Client, handle: str) -> dict:
    r = client.get(f"{API}/pid/find", params={"id": f"123456789/{handle}"})
    r.raise_for_status()
    return r.json()


def main_pdf(client: httpx.Client, item_uuid: str) -> str | None:
    """URL do maior PDF do bundle ORIGINAL (o texto principal; ignora anexos menores)."""
    r = client.get(f"{API}/core/items/{item_uuid}/bundles")
    r.raise_for_status()
    best = None
    for bundle in r.json()["_embedded"]["bundles"]:
        if bundle["name"] != "ORIGINAL":
            continue
        bs = client.get(bundle["_links"]["bitstreams"]["href"]).json()
        for b in bs["_embedded"]["bitstreams"]:
            if b["name"].lower().endswith(".pdf") and (best is None or b["sizeBytes"] > best["sizeBytes"]):
                best = b
    return best["_links"]["content"]["href"] if best else None


def to_row(client: httpx.Client, item: dict) -> dict | None:
    url = main_pdf(client, item["uuid"])
    if url is None:
        print(f"[PULA] sem PDF: {item['name'][:80]}")
        return None
    handle = item["handle"].split("/")[-1]
    return {
        "doc_id": f"unir_{handle}",
        "titulo": _meta(item, "dc.title"),
        "fonte": "RI-UNIR",
        "url": url,
        "licenca": "verificar",
        "formato": "pdf",
        "escaneado": "",  # preenchido depois por extract (needs_ocr)
        "idioma": _meta(item, "dc.language.iso") or "pt",
        "tipo": _meta(item, "dc.type"),
        "ano": _meta(item, "dc.date.issued")[:4],
        "assuntos": _meta(item, "dc.subject"),
        "pagina": f"{SITE}/{item['handle']}",
        "sha256": "",
    }


def fetch(n: int = 10, query: str | None = None, handles: list[str] | None = None,
          out: str = "data/sources.csv") -> pd.DataFrame:
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        items = ([item_by_handle(client, h) for h in handles] if handles
                 else search_items(client, n, query))
        rows = [r for it in items if (r := to_row(client, it))]

    new = pd.DataFrame(rows, columns=COLUMNS)
    path = Path(out)
    if path.exists() and path.stat().st_size > 0:
        old = pd.read_csv(path, dtype=str).fillna("")
        new = pd.concat([old, new[~new.doc_id.isin(old.doc_id)]], ignore_index=True)
    new = new.reindex(columns=list(dict.fromkeys(COLUMNS + list(new.columns))))
    new.to_csv(path, index=False)
    print(f"{len(rows)} itens coletados; {len(new)} no total em {out}")
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--query")
    ap.add_argument("--handles", nargs="*")
    a = ap.parse_args()
    fetch(a.n, a.query, a.handles)
