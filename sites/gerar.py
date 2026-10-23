"""Monta a página de leads quentes para quem vende site: só quem não tem site no ar e tem sinais de que compraria.

Uso: python gerar.py
Lê dados/maps.jsonl (coletar_maps.py), pontua a chance de comprar site e separa os motivos a favor e os pontos
de atenção, para a pessoa decidir antes de mandar. As mensagens são montadas na página, com o nome e a empresa
que a pessoa digitar. Grava saida/leads-sites-AAAA-MM-DD.html e publicar/p/sites.html.
"""

import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
PUBLICAR = PASTA.parent / "publicar" / "p" / "sites.html"  # o endereço do site é um só ("/"): o _redirects escolhe esta ou a de equipamentos
sys.path.insert(0, str(PASTA.parent))  # a pasta Leads, onde está o plano_diario.py
import plano_diario as pd  # noqa: E402

# segmento: (como chamar no plural, o que o cliente procura no Google, ticket alto?)
SEGMENTOS = {
    "advocacia": ("escritórios de advocacia", "advogado", True),
    "contabilidade": ("escritórios de contabilidade", "contador", True),
    "estética": ("clínicas de estética", "clínica de estética", True),
    "fisioterapia": ("clínicas de fisioterapia", "fisioterapia", False),
    "psicologia": ("psicólogos e clínicas de psicologia", "psicólogo", False),
    "arquitetura e interiores": ("arquitetos e designers de interiores", "arquiteto", True),
    "móveis planejados": ("marcenarias e lojas de planejados", "móveis planejados", True),
    "energia solar": ("empresas de energia solar", "energia solar", True),
    "estética automotiva": ("estéticas automotivas", "estética automotiva", False),
    "dedetização": ("dedetizadoras", "dedetizadora", False),
    "buffet e eventos": ("buffets e espaços de festa", "buffet infantil", True),
}
# o que o nome ou a categoria precisa ter para ficar no segmento (a busca do Maps traz vizinhos de outro ramo)
NICHO = {
    "advocacia": r"advog|advocacia|jur[ií]dic|direito|dr\.|dra\.",
    "contabilidade": r"cont[aá]b|contador|fiscal|assessoria empresarial",
    "estética": r"est[eé]tic|harmoniza|depila|laser|beleza|facial|corporal|spa\b|pele|skin",
    "fisioterapia": r"fisio|pilates|reabilita|osteopat|quiropra|rpg",
    "psicologia": r"psic|terapia|terapeuta|sa[uú]de mental|neuropsic",
    "arquitetura e interiores": r"arquitet|interiores|design|decora|projeto",
    "móveis planejados": r"m[oó]veis|planejad|marcenar|cozinhas|ambientes|marceneiro",
    "energia solar": r"solar|fotovolt|energia",
    "estética automotiva": r"automotiv|car ?wash|lava|polimento|vitrifica|detail|envelopamento|insulfilm|martelinho",
    "dedetização": r"dedetiz|pragas|controle de|desratiz|cupim|sanitiza|limpeza de caixa",
    "buffet e eventos": r"buffet|festa|eventos|recrea|sal[aã]o de|espa[cç]o",
}
REDES = re.compile(r"franquia|unidade |sorridents|odontocompany|o botic|depilaz|espaçolaser|espaco laser|"
                   r"maria bonita|pello menos|onodera|emagrecentro|dr\.? ?consulta|amor sa[uú]de|"
                   r"contabilizei|rede |grupo |orbi|^vivo |jet oil|lavacar express", re.I)
FORA = re.compile(r"prefeitura|municipal|estadual|federal|tribunal|defensoria|minist[eé]rio|universidade|faculdade|"
                  r"sindicato|conselho regional|\boab\b|cartório|delegacia|escola|curso|loja de material", re.I)


def meses_atras(quando):
    """'um mês atrás' → 1, '3 semanas atrás' → 0.75, 'um ano atrás' → 12."""
    m = re.search(r"(\d+|um|uma) (dias?|semanas?|m[eê]s|meses|anos?)", quando or "")
    if not m:
        return None
    n = 1 if m[1] in ("um", "uma") else int(m[1])
    unidade = m[2]
    return n / 30 if unidade.startswith("dia") else n / 4 if unidade.startswith("semana") else n * 12 if unidade.startswith("ano") else n


def telefone(bruto):
    d = re.sub(r"\D", "", bruto or "").lstrip("0")
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]   # +55 51 99999-9999
    if len(d) == 8:
        d = "51" + d
    if len(d) not in (10, 11):
        return "", ""
    return d, ("celular" if len(d) == 11 and d[2] == "9" else "fixo")


def situacao(l):
    """Como está a presença do lead fora do Google: o que a ficha mostra no campo 'site'."""
    tipo, rot = l.get("tipo_link"), l.get("rotulo_link") or ""
    if tipo == "próprio":
        return "site quebrado", f"site fora do ar: {l['site_quebrado']}"
    if tipo == "grátis" and re.search(r"business\.site|negocio\.site", l.get("site", "")):
        return "site do Google desativado", "o link é do site grátis do Google, desativado em 2024"
    if tipo == "grátis":
        return "site grátis", f"usa site grátis, sem domínio próprio ({rot})"
    if tipo == "perfil":
        return "só rede social", f"pôs o {rot} no lugar do site"
    return "sem site", "a ficha não tem site"


def pontuar(l):
    """Chance de comprar site, com os motivos a favor e os pontos de atenção."""
    plural, busca, alto = SEGMENTOS[l["segmento"]]
    sit = l["situacao"]
    a_favor, atencao = [], []
    # a situação do site já vem em destaque no cartão: aqui só entra o porquê de ela pesar a favor
    p = {"site quebrado": 25, "site do Google desativado": 25, "site grátis": 20, "só rede social": 15, "sem site": 0}[sit]
    if sit in ("site quebrado", "site do Google desativado", "site grátis"):
        a_favor.append("já quis ter site: é trocar, não convencer")
    elif sit == "só rede social":
        a_favor.append("preencheu o campo de site com rede social: quer um link")
    av = l.get("avaliacoes") or 0
    p += round(20 * math.log10(1 + min(av, 150)) / math.log10(151))
    if sit == "sem site" and av >= 100:
        p += 10
        a_favor.append("muita procura no Google e nenhum site: perde quem quer comparar")
    if av >= 60:
        a_favor.append(f"{av} avaliações: tem clientela")
    elif av < 10:
        p -= 10
        atencao.append(f"só {av} avaliações: pode ser pequeno demais")
    respostas = sum(1 for r in l.get("reviews", []) if r.get("resposta"))
    if respostas:
        p += 15
        a_favor.append("o dono responde as avaliações: cuida da imagem")
    if alto:
        p += 10
        a_favor.append("serviço de valor alto: um cliente novo paga o site")
    if l["tipo_telefone"] == "celular":
        p += 10
        a_favor.append("celular (WhatsApp)")
    if (l.get("fotos") or 0) >= 20:
        p += 5
        a_favor.append(f"{l['fotos']} fotos na ficha")
    if (l.get("nota") or 0) >= 4.7 and av >= 20:
        p += 5
    meses = [m for m in (meses_atras(r.get("quando")) for r in l.get("reviews", [])) if m is not None]
    if meses and min(meses) <= 2:
        p += 5
    elif meses and min(meses) >= 12:
        p -= 15
        atencao.append("nenhuma avaliação nova há mais de um ano")
    if l.get("reivindicada") is False:
        p -= 10
        atencao.append("ficha não reivindicada: o dono não cuida do Google")
    if (l.get("nota") or 5) < 3.8:
        p -= 10
        atencao.append(f"nota {str(l['nota']).replace('.', ',')}: o problema pode ser outro")
    if sit == "sem site" and av < 30 and not respostas:
        atencao.append("sem site, sem Instagram e pouca atividade: talvez nem use internet")
    if l.get("rede"):
        p -= 30
        atencao.append("parece rede ou franquia: o site costuma vir da matriz")
    return max(p, 0), a_favor, atencao


def leads(registros):
    saida, por_tel = [], {}
    for r in registros:
        texto = f"{r['nome']} {r.get('categoria', '')}"
        if FORA.search(texto) or not re.search(NICHO[r["segmento"]], texto, re.I):
            continue
        d, tipo = telefone(r.get("telefone"))
        if not d:
            continue
        l = {k: r.get(k) for k in ("id", "nome", "nota", "avaliacoes", "categoria", "segmento", "cidade", "bairro",
                                    "endereco", "site", "tipo_link", "rotulo_link", "site_quebrado", "fotos", "topicos",
                                    "destaques", "reviews", "horario", "atributos", "descricao", "reivindicada")}
        l.update(digitos=d, telefone=f"({d[:2]}) {d[2:-4]}-{d[-4:]}", tipo_telefone=tipo, maps=r.get("link", ""),
                 rede=bool(REDES.search(r["nome"])))
        l["situacao"], l["situacao_texto"] = situacao(l)
        l["plural"], l["busca"], _ = SEGMENTOS[l["segmento"]]
        l["pontos"], l["a_favor"], l["atencao"] = pontuar(l)
        l["temperatura"] = "quente" if l["pontos"] >= 70 else "morno" if l["pontos"] >= 50 else "frio"
        l["responde"] = any(x.get("resposta") for x in l.get("reviews") or [])
        # o Google mostra as 3 avaliações "mais relevantes", não as mais novas: é só um indício de atividade
        recentes = sorted((m, x["quando"]) for x in l.get("reviews") or [] if (m := meses_atras(x.get("quando"))) is not None)
        l["ultima_avaliacao"] = recentes[0][1] if recentes else ""
        atual = por_tel.get(d)
        if atual is None or l["pontos"] > atual["pontos"]:
            por_tel[d] = l
    return sorted(por_tel.values(), key=lambda l: -l["pontos"])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    arq_dados = PASTA / "dados" / "maps.jsonl"
    registros = [json.loads(x) for x in arq_dados.read_text(encoding="utf-8").splitlines() if x.strip()]
    lista = leads(registros)
    pd.marcar_vistos([lista], PASTA / "dados" / "vistos_em.json")  # o dia em que cada lead apareceu
    pacote ={"atualizado": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"), "leads": lista}
    modelo = (PASTA / "modelo.html").read_text(encoding="utf-8")
    pagina = modelo.replace("{{DADOS}}", json.dumps(pacote, ensure_ascii=False).replace("</", "<\\/"))
    arq = PASTA / "saida" / f"leads-sites-{dt.date.today():%Y-%m-%d}.html"
    destinos = [arq] + ([PUBLICAR] if PUBLICAR.parent.parent.exists() else [])
    for destino in destinos:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(pagina, encoding="utf-8")
    q = sum(l["temperatura"] == "quente" for l in lista)
    print(f"{len(lista)} leads ({q} quentes, {sum(l['tipo_telefone'] == 'celular' for l in lista)} celulares)")
    print("\n".join(str(d) for d in destinos))


if __name__ == "__main__":
    main()
