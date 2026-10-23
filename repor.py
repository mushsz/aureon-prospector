"""Conta quantos leads ainda faltam ver em cada lista, para a busca trazer mais antes de acabar.

Lista = "sites" (página de sites) ou "hosp" (clínicas da página de equipamentos). "Falta ver" é o mesmo que a página
chama de fila: lead que ninguém marcou como enviado e que não apareceu no lote de um dia anterior. As marcas vêm da
função da Netlify (/api/marcas), a mesma que as páginas usam, então a conta vale para todos que dividem a lista.
Licitações e hospitais não entram: licitação renova sozinha a cada rodada e os hospitais são um cadastro fixo do CNES.

Uso: python repor.py [--site https://...] [--limite 50]
"""

import argparse
import datetime as dt
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PASTA = Path(__file__).resolve().parent
SITE = "https://aureon-prospeccao-2d44fa.netlify.app"
LIMITE = 50


def _carregar(nome, arquivo):
    spec = importlib.util.spec_from_file_location(nome, arquivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(arquivo.parent))
    spec.loader.exec_module(modulo)
    return modulo


def ids_da_lista(lista):
    """Os ids dos leads que a página mostra no Lote do dia, montados pelo mesmo código que gera a página."""
    if lista == "sites":
        g = _carregar("gerar_sites", PASTA / "sites" / "gerar.py")
        dados = PASTA / "sites" / "dados" / "maps.jsonl"
        registros = [json.loads(x) for x in dados.read_text(encoding="utf-8").splitlines() if x.strip()] if dados.exists() else []
        return [l["id"] for l in g.leads(registros)]
    g = _carregar("gerar_hosp", PASTA / "hospitalar" / "gerar.py")
    dados = PASTA / "hospitalar" / "dados"
    cnes_arq = dados / "estabelecimentos.json"
    cnes = json.loads(cnes_arq.read_text(encoding="utf-8")) if cnes_arq.exists() else []
    return [l["id"] for l in g.empresas(g.ler_jsonl(dados / "maps.jsonl"), cnes) if l["digitos"]]


def marcas(lista, site=SITE):
    """As marcas da lista na Netlify, ou None se não deu para ler (sem internet, função fora do ar)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{site}/api/marcas?lista={lista}", headers={"User-Agent": "aureon-leads"}), timeout=30) as r:
            return json.load(r)["marcas"]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def faltam(lista, site=SITE, hoje=None):
    """Quantos leads ainda podem entrar em um lote. None se as marcas não puderam ser lidas."""
    m = marcas(lista, site)
    if m is None:
        return None
    hoje = hoje or dt.date.today().isoformat()
    return sum(1 for i in ids_da_lista(lista) if f"enviado:{i}" not in m and m.get(f"visto:{i}", hoje) == hoje)


def situacao(site=SITE):
    """{'sites': 312, 'hosp': 580} (None onde não foi possível contar)."""
    return {lista: faltam(lista, site) for lista in ("sites", "hosp")}


def precisa(n, limite=LIMITE):
    return n is not None and n <= limite


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=SITE)
    ap.add_argument("--limite", type=int, default=LIMITE)
    args = ap.parse_args()
    for lista, n in situacao(args.site).items():
        estado = "não consegui ler as marcas" if n is None else ("PRECISA REPOR" if precisa(n, args.limite) else "ok")
        print(f"{lista}: {'?' if n is None else n} na fila ({estado})")
