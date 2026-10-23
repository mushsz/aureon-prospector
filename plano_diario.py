"""Monta as pesquisas das coletas de todo dia (equipamentos e sites) e escolhe as do dia.

Cada dia sai um pouco de cada nicho. Primeiro vêm as pesquisas nunca feitas; quando acabam,
as feitas há mais de 30 dias, que trazem as empresas que apareceram no Maps depois.
"""

import datetime as dt
import json

ZONAS_POA = ["Zona Norte", "Zona Sul", "Zona Leste", "Centro"]
BASE = "2026-10-20"  # data das primeiras coletas, que não gravavam o dia


def marcar_vistos(listas, arq):
    """Grava em `arq` o dia em que cada lead apareceu e põe esse dia em lead["visto_em"] (a página mostra "novo").
    Na primeira vez, o que já existia vira "base": não ganha selo de novo."""
    vistos = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None
    primeira, hoje = vistos is None, dt.date.today().isoformat()
    vistos = vistos or {}
    for lista in listas:
        for item in lista:
            vistos.setdefault(item["id"], "base" if primeira else hoje)
            item["visto_em"] = None if vistos[item["id"]] == "base" else vistos[item["id"]]
    arq.write_text(json.dumps(vistos, ensure_ascii=False), encoding="utf-8")


def bairros():
    """Porto Alegre inteira, as zonas e cada bairro, mais as cidades vizinhas e seus bairros (os do coletar.py)."""
    import coletar as co
    lugares =[(None, "Porto Alegre")] + [(z, "Porto Alegre") for z in ZONAS_POA] + [(b, "Porto Alegre") for b in co.BAIRROS_POA]
    for cidade, bs in co.CIDADES_VIZINHAS:
        lugares += [(None, cidade)] + [(b, cidade) for b in bs]
    return lugares


def cidades():
    """Só a cidade inteira: para nicho com pouca empresa, pesquisar por bairro repete os mesmos resultados."""
    import coletar as co
    return [(None, "Porto Alegre")] + [(None, c) for c, _ in co.CIDADES_VIZINHAS]


def chave(pesquisa):
    return "|".join(str(x) for x in pesquisa)


def do_dia(plano, feitas, maximo, revisitar_dias=30, hoje=None):
    """plano: [(termo, segmento, lugares)]. Devolve até `maximo` pesquisas (termo, segmento, bairro, cidade)."""
    hoje = hoje or dt.date.today()
    filas = [[(t, s, b, c) for b, c in lugares] for t, s, lugares in plano]
    ordem = [fila[i] for i in range(max(map(len, filas))) for fila in filas if i < len(fila)]
    nunca = [p for p in ordem if chave(p) not in feitas]
    idade = lambda p: (hoje - dt.date.fromisoformat(feitas[chave(p)].get("data") or BASE)).days
    antigas = sorted((p for p in ordem if chave(p) in feitas and idade(p) >= revisitar_dias), key=idade, reverse=True)
    return (nunca + antigas)[:maximo], len(nunca)
