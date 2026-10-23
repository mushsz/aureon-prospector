"""Monta a página de leads quentes para o fornecedor de material médico-hospitalar.

Uso: python gerar.py
Lê de dados/: compras_abertas.json (pncp.py), estabelecimentos.json (cnes.py) e maps.jsonl (coletar_maps.py).
Três listas na mesma página: compras públicas abertas, clínicas e empresas (WhatsApp) e hospitais (Compras).
Só biblioteca padrão. As mensagens são montadas na página, com o nome e a empresa que a pessoa digitar.
"""

import datetime as dt
import html
import json
import math
import re
import sys
from pathlib import Path

# (segmento, padrão que o nome ou a categoria precisa ter para ficar, pontos pelo consumo, ele já vendeu?)
SEGMENTOS = {
    "veterinária": (r"veterin|\bvet\b|vets\b|hospital vet|animal|pet", 12, True),
    "casa de repouso": (r"repouso|geri[aá]tr|idos|longa perman|ilpi|s[eê]nior|residencial|\blar\b|asilo|terceira idade|cuidado|vivenda|morada", 20, True),
    "estética": (r"est[eé]tic|harmoniza|dermat|laser|facial|corpo|beauty|skin|pele", 8, True),
    "pronto atendimento": (r"pronto|urg[eê]n|emerg|24 ?h|hospital|cl[ií]nica", 15, True),
    "home care": (r"home ?care|domicil|enferm|cuidador|sa[uú]de|care", 15, False),
    "ambulância e remoção": (r"ambul|remo[cç]|uti m[oó]vel|resgate|emerg|socorr|transporte", 15, False),
    "cirurgia e day hospital": (r"cirurg|pl[aá]stic|day|hospital|dermat", 18, False),
}
FORA = re.compile(r"sal[aã]o|barbe|manicure|c[ií]lios|sobrancelha|esmalter|cabel|pet ?shop|banho e tosa|ra[cç][aã]o|"
                  r"agropecu|farm[aá]cia|loja|escola|curso|faculdade|seguro|funer|imobili|hotel para c|creche|igreja|religios|"
                  r"reparti[cç][aã]o|departamento|educacional|canil|cuidados diurnos para cachorro", re.I)
# público compra por licitação (já está na aba de compras abertas); universidade e rede compram na matriz
PUBLICO = re.compile(r"\bUPA\b|pronto.?socorro|\bHPS\b|posto de sa[uú]de|unidade b[aá]sica|\bUBS\b|\bESF\b|prefeitura|"
                     r"municipal|estadual|\bSAMU\b|cl[ií]nicas veterin[aá]rias da ufrgs|universit|\bUFRGS\b|\bPUCRS\b|"
                     r"\bULBRA\b|unisinos|feevale|\bGHC\b|conceição|cristo redentor|brigada militar|ex[eé]rcito", re.I)
REDES = re.compile(r"^petz\b|\bseres\b|cobasi|wevets|dr\.? ?consulta|amor sa[uú]de|hapvida|unimed|"
                   r"notredame|intermedica|sorridents|odonto ?company", re.I)
GRANDES = re.compile(r"moinhos de vento|m[aã]e de deus|santa casa|s[aã]o lucas|divina provid|ernesto dornelles|"
                     r"instituto de cardiologia|banco de olhos|s[aã]o pietro|regina|unimed|humaniza|hospital porto alegre", re.I)
PLATAFORMAS = [("portaldecompraspublicas", "Portal de Compras Públicas"), ("compras.rs.gov.br", "Compras RS (CELIC)"),
               ("licitacoes-e", "Licitações-e (Banco do Brasil)"), ("comprasnet", "Compras.gov.br"),
               ("estaleiro.serpro", "Compras.gov.br"), ("pregaobanrisul", "Pregão Banrisul"), ("bll.org", "BLL Compras"),
               ("bnc.org", "BNC"), ("licitardigital", "Licitar Digital"), ("ecustomize", "e-Customize")]


def telefone(bruto, ddd="51"):
    """'(51) 91648678' → ('51991648678', 'celular'). Número de 8 dígitos sem DDD ganha o 51 da região."""
    d = re.sub(r"\D", "", re.split(r"ramal|/", bruto or "", flags=re.I)[0])
    d = d.lstrip("0")
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]   # +55 51 99999-9999
    if len(d) == 8:
        d = ddd + d
    if len(d) == 10 and d[2] in "6789":  # celular antigo, sem o 9 da frente
        d = d[:2] + "9" + d[2:]
    if len(d) not in (10, 11):
        return "", ""
    return d, ("celular" if len(d) == 11 and d[2] == "9" else "fixo")


def formatar(d):
    return f"({d[:2]}) {d[2:-4]}-{d[-4:]}" if d else ""


def ler_jsonl(arq):
    return [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()] if arq.exists() else []


def pontos_empresa(l):
    """Chance de comprar: consumo do segmento, WhatsApp direto, movimento, 24 horas, e se ele já vendeu para esse tipo."""
    base, ja = SEGMENTOS[l["segmento"]][1:]
    if l["segmento"] == "veterinária" and re.search(r"hospital|24", f"{l['nome']} {l['categoria']}", re.I):
        base = 20  # hospital veterinário opera e interna: gasta mais sutura e equipamento
    motivos = []
    p = base
    if ja:
        p += 10
        motivos.append(f"já vendeu para {l['segmento']}")
    if l["tipo_telefone"] == "celular":
        p += 25
        motivos.append("celular (WhatsApp)")
    av = l.get("avaliacoes") or 0
    p += round(20 * math.log10(1 + min(av, 150)) / math.log10(151))
    if av >= 80:
        motivos.append(f"movimento alto ({av} avaliações)")
    if l.get("h24"):
        p += 10
        motivos.append("abre 24 horas")
    if (l.get("nota") or 0) >= 4.5:
        p += 5
    if l.get("rede"):
        p -= 20
        motivos.append("rede: a compra pode ser na matriz")
    return max(p, 0), motivos


def temperatura(p):
    return "quente" if p >= 70 else "morno" if p >= 50 else "frio"


def empresas(maps, cnes):
    leads, por_tel = [], {}
    for m in maps:
        texto = f"{m['nome']} {m['categoria']}"
        padrao = SEGMENTOS[m["segmento"]][0]
        if PUBLICO.search(m["nome"]) or FORA.search(m["categoria"]) or not re.search(padrao, texto, re.I):
            continue
        d, tipo = telefone(m["telefone"])
        if not d:
            continue
        leads.append({"id": m["id"], "origem": "Google Maps", "nome": m["nome"], "categoria": m["categoria"],
                      "segmento": m["segmento"], "cidade": m["cidade"], "bairro": m.get("bairro") or "",
                      "endereco": m.get("endereco") or "", "digitos": d, "telefone": formatar(d), "tipo_telefone": tipo,
                      "email": "", "nota": m.get("nota"), "avaliacoes": m.get("avaliacoes") or 0, "h24": m.get("h24", False),
                      "site": m.get("site") or "", "maps": m.get("link") or "", "rede": bool(REDES.search(m["nome"]))})
    for c in cnes:  # home care do CNES entra aqui: é negócio pequeno, que o dono atende
        if c["tipo"] != "home care":
            continue
        d, tipo = telefone(c["telefone"])
        if not d and not c["email"]:
            continue
        leads.append({"id": c["id"], "origem": "CNES", "nome": c["nome"].title(), "categoria": "Home care (CNES)",
                      "segmento": "home care", "cidade": c["cidade"], "bairro": c["bairro"], "endereco": c["endereco"],
                      "digitos": d, "telefone": formatar(d), "tipo_telefone": tipo or "sem telefone",
                      "email": c["email"] if "@" in c["email"] else "", "nota": None, "avaliacoes": 0, "h24": False,
                      "site": "", "maps": "https://www.google.com/maps/search/?api=1&query="
                      + re.sub(r"\s+", "+", f"{c['nome']} {c['cidade']}"), "rede": False})
    for l in leads:
        l["pontos"], l["motivos"] = pontos_empresa(l)
        l["temperatura"] = temperatura(l["pontos"])
        chave = l["digitos"] or l["id"]
        atual = por_tel.get(chave)
        if atual is None or l["pontos"] > atual["pontos"]:
            por_tel[chave] = l
    return sorted(por_tel.values(), key=lambda l: -l["pontos"])


def hospitais(cnes):
    saida = []
    for c in cnes:
        if c["tipo"] == "home care":
            continue
        d, tipo = telefone(c["telefone"])
        grande = bool(GRANDES.search(c["nome"]))
        saida.append({"id": c["id"], "nome": c["nome"].title(), "tipo": c["tipo"], "cidade": c["cidade"], "bairro": c["bairro"],
                      "endereco": c["endereco"], "digitos": d, "telefone": formatar(d), "tipo_telefone": tipo,
                      "email": c["email"] if "@" in c["email"] else "", "cnpj": c.get("cnpj") or "",
                      "filantropico": c["filantropico"], "centro_cirurgico": c["centro_cirurgico"], "grande": grande,
                      "maps": "https://www.google.com/maps/search/?api=1&query=" + re.sub(r"\s+", "+", f"{c['nome']} {c['cidade']}")})
    # os médios primeiro: decidem mais rápido que os grandes, que compram por plataforma de cotação
    return sorted(saida, key=lambda h: (h["grande"], not h["centro_cirurgico"], h["cidade"] != "Porto Alegre", h["nome"]))


MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "a", "o", "em", "para", "com"}


def sem_caixa_alta(t):
    """'FUNDACAO DE SAUDE PUBLICA' vira 'Fundacao de Saude Publica'; texto que já tem minúsculas fica como está."""
    if sum(c.isupper() for c in t) < 0.6 * sum(c.isalpha() for c in t):
        return t
    return " ".join(p.lower() if i and p.lower() in MINUSCULAS else p.capitalize() for i, p in enumerate(t.split()))


def frase(t):
    """Objeto e itens em caixa alta viram frase: só a primeira letra maiúscula."""
    if sum(c.isupper() for c in t) < 0.6 * sum(c.isalpha() for c in t):
        return t
    t = t.lower()
    return t[:1].upper() + t[1:]


def limpar(t):
    """Tira HTML e entidades; a descrição vem cortada, então pode sobrar um '&c' pela metade no fim."""
    t = re.sub(r"&[#\w]*$", "", t or "")
    if "Ã" in t:  # algumas prefeituras mandam o texto com a acentuação quebrada ('aquisiÃ§Ã£o')
        try:
            t = t.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t))).strip()


def nome_orgao(c):
    """O PNCP traz 'MUNICIPIO DE INDEPENDENCIA', sem acento: a cidade vem com acento, então vira 'Prefeitura de Independência'."""
    if re.match(r"MUNIC[IÍ]PIO D[EO]", c["orgao"], re.I):
        return f"Prefeitura de {c['cidade']}"
    return sem_caixa_alta(c["orgao"]).replace("S/a", "S/A")


def compras(pncp):
    saida, vistos = [], set()
    for c in sorted(pncp, key=lambda c: c["encerramento"], reverse=True):
        # a mesma compra publicada duas vezes (PNCP e Portal de Compras Públicas): fica a que fecha mais tarde
        objeto = limpar(c["objeto"]).replace("[Portal de Compras Públicas] - ", "")
        chave = (c["id"].split("-")[0], objeto[:80].lower(), c["encerramento"][:10])
        if chave in vistos:
            continue
        vistos.add(chave)
        host = (c.get("link_sistema") or "").lower()
        plataforma = next((nome for chave, nome in PLATAFORMAS if chave in host), "")
        itens = sorted(c["itens_da_linha"], key=lambda i: (not set(i["linhas"]) - {"descartáveis"}, -(i["valor_total"] or 0)))
        linhas = sorted({l for i in itens for l in i["linhas"]})
        saida.append({**{k: c[k] for k in ("id", "cidade", "modalidade", "valor_estimado", "encerramento", "link_pncp",
                                            "total_itens", "itens_principais", "valor_itens_da_linha")},
                      "orgao": nome_orgao(c), "unidade": sem_caixa_alta(limpar(c["unidade"])), "objeto": frase(objeto),
                      "exclusiva_me": c.get("exclusiva_me", False),
                      "link_sistema": c.get("link_sistema") or "", "plataforma": plataforma, "linhas": linhas,
                      "itens": [{"descricao": frase(limpar(i["descricao"]).split("Especificação:")[-1].strip())[:120],
                                 "quantidade": i["quantidade"], "unidade": i["unidade"], "valor": i["valor_total"]}
                                for i in itens[:8]],
                      "mais_itens": max(len(itens) - 8, 0), "n_itens_linha": len(itens)})
    return sorted(saida, key=lambda c: c["encerramento"])


PASTA = Path(__file__).resolve().parent
PUBLICAR = PASTA.parent / "publicar" / "p" / "equipamentos.html"  # o endereço do site é um só ("/"): o _redirects escolhe esta ou a de sites
sys.path.insert(0, str(PASTA.parent))  # a pasta Leads, onde está o plano_diario.py
import plano_diario as pd  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dados = PASTA / "dados"
    arq = PASTA / "saida" / f"leads-hospitalar-{dt.date.today():%Y-%m-%d}.html"
    ler = lambda nome: json.loads((dados / nome).read_text(encoding="utf-8")) if (dados / nome).exists() else []
    cnes = ler("estabelecimentos.json")
    pacote = {"atualizado": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
              "compras": compras(ler("compras_abertas.json")),
              "empresas": empresas(ler_jsonl(dados / "maps.jsonl"), cnes),
              "hospitais": hospitais(cnes)}
    # o dia em que cada licitação, clínica ou hospital apareceu: a página mostra o que é novo
    pd.marcar_vistos([pacote["compras"], pacote["empresas"], pacote["hospitais"]], dados / "vistos_em.json")
    modelo =(Path(__file__).with_name("modelo.html")).read_text(encoding="utf-8")
    arq.parent.mkdir(parents=True, exist_ok=True)
    pagina = modelo.replace("{{DADOS}}", json.dumps(pacote, ensure_ascii=False).replace("</", "<\\/"))
    arq.write_text(pagina, encoding="utf-8")
    PUBLICAR.parent.mkdir(parents=True, exist_ok=True)
    PUBLICAR.write_text(pagina, encoding="utf-8")
    e = pacote["empresas"]
    print(f"{len(pacote['compras'])} compras abertas · {len(e)} empresas "
          f"({sum(l['temperatura'] == 'quente' for l in e)} quentes, {sum(l['tipo_telefone'] == 'celular' for l in e)} celulares) · "
          f"{len(pacote['hospitais'])} hospitais\n{arq}\n{PUBLICAR}")


if __name__ == "__main__":
    main()
