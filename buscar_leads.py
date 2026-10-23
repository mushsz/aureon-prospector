"""Acha empresas sem site próprio no Google Maps e monta a mensagem de abordagem de cada uma.

Uso:
    python buscar_leads.py --cidade "Campinas, SP" --cidade "Valinhos, SP"
    python buscar_leads.py --cidade "Campinas, SP" --repetir      # mostra de novo os já listados
    python buscar_leads.py --coletados coleta/porto-alegre.jsonl  # grátis: lê o que o navegador coletou

A API precisa da chave do Google Places (New) na variável de ambiente GOOGLE_PLACES_API_KEY.
--coletados lê linhas JSON com: id, nome, nota, avaliacoes, categoria, endereco, telefone, site,
status, termo, cidade (lista do Google Maps lida no navegador, sem API).
Só biblioteca padrão. Gera, em saida/, um .csv (Excel) e um .html para mandar as mensagens.

Regras do Google: nomes e telefones não podem virar uma base permanente; o place ID pode.
Por isso vistos.json guarda só IDs, e a lista de cada rodada serve para a campanha da vez.
"""

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REMETENTE = "a Giovanna, da Aureon Studio"  # entra em "Sou a Giovanna, ..."
LIMITE_MENSAL = 900  # a API dá 1.000 buscas Enterprise grátis por mês; a sobra é margem
PROFUNDIDADE_MAX = 2  # quantas vezes uma área lotada (60 resultados) é dividida em 4

# (o que buscar, como chamar o segmento na mensagem, como o negócio chama quem atende)
# Em ordem de prioridade: se a cota do mês acabar, o que fica de fora é o fim da lista.
TERMOS = [
    ("dentista", "clínicas odontológicas", "pacientes"),
    ("clínica odontológica", "clínicas odontológicas", "pacientes"),
    ("harmonização facial", "clínicas odontológicas", "pacientes"),
    ("clínica de estética", "clínicas de estética", "clientes"),
    ("dermatologista", "consultórios e clínicas", "pacientes"),
    ("clínica médica", "consultórios e clínicas", "pacientes"),
    ("fisioterapia", "consultórios e clínicas", "pacientes"),
    ("psicólogo", "consultórios e clínicas", "pacientes"),
    ("nutricionista", "consultórios e clínicas", "pacientes"),
    ("fonoaudiólogo", "consultórios e clínicas", "pacientes"),
    ("pilates", "studios de pilates", "alunos"),
    ("salão de beleza", "negócios de beleza e estética", "clientes"),
    ("barbearia", "barbearias", "clientes"),
    ("extensão de cílios", "negócios de beleza e estética", "clientes"),
    ("design de sobrancelhas", "negócios de beleza e estética", "clientes"),
    ("depilação", "negócios de beleza e estética", "clientes"),
    ("esmalteria", "negócios de beleza e estética", "clientes"),
    ("massoterapia", "negócios de beleza e estética", "clientes"),
    ("podologia", "consultórios e clínicas", "clientes"),
]

# Links que aparecem no campo "site" do Google mas não são site próprio
PERFIS = {
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
    "linktr.ee": "Linktree",
    "bio.link": "link na bio",
    "beacons.ai": "link na bio",
    "taplink.cc": "link na bio",
    "linkbio.co": "link na bio",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "doctoralia.com.br": "Doctoralia",
    "boaconsulta.com": "BoaConsulta",
    "booksy.com": "Booksy",
    "trinks.com": "Trinks",
    "business.site": "site antigo do Google, que foi desativado",
    "g.page": "perfil do Google",
}

# Precisão: o que não é do nicho ou não decide sozinho sai da lista
NICHO_ODONTO = re.compile(r"dent|odont|ortodon|endodon|periodon|implant|pr[oó]tese|sorriso|smile", re.I)
NICHO_ESTETICA = re.compile(r"est[eé]tic|harmoniza|beleza|facial|pele|dermat|spa\b|botox|laser|depila|beauty", re.I)
NICHO_POR_TERMO = {
    "dentista": NICHO_ODONTO,
    "clínica odontológica": NICHO_ODONTO,
    "harmonização facial": NICHO_ODONTO,  # só a harmonização orofacial, feita por dentista
    "clínica de estética": NICHO_ESTETICA,
}
# Redes, franquias e clínicas-escola: a decisão é da matriz e quase todas têm site da marca
REDES = re.compile(r"odonto ?company|sorridents|oral ?sin|odonto ?excellence|orthodontic|amor sa[uú]de|"
                   r"dr\.? ?consulta|odontoprev|uniodonto|hapvida|\bsesc\b|\bsesi\b|ufrgs|pucrs|ulbra", re.I)
# (o que aparece no nome ou na categoria, como chamar na mensagem, é tratamento de valor alto?)
TRATAMENTOS = [
    (r"implant", "implante dentário", True),
    (r"lente|faceta", "lentes de contato dental", True),
    (r"harmoniza|botox|preenchimento", "harmonização facial", True),
    (r"ortodon|alinhador|invisalign|aparelho", "aparelho ou alinhador", True),
    (r"pr[oó]tese|reabilita", "prótese dentária", True),
    (r"clareamento", "clareamento dental", False),
    (r"odontopediatr|infantil|kids", "dentista para criança", False),
    (r"endodon|canal", "tratamento de canal", False),
]

URL_BUSCA = "https://places.googleapis.com/v1/places:searchText"
CAMPOS_LUGAR = ",".join(
    "places." + c
    for c in ["id", "displayName", "formattedAddress", "nationalPhoneNumber", "websiteUri", "rating",
              "userRatingCount", "businessStatus", "googleMapsUri", "primaryTypeDisplayName"]
) + ",nextPageToken"

PASTA = Path(__file__).resolve().parent
ARQ_USO = PASTA / "uso_api.json"
ARQ_VISTOS = PASTA / "vistos.json"


class LimiteAtingido(Exception):
    pass


def ler_chave():
    chave = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not chave and sys.platform == "win32":
        # a variável criada depois que o terminal abriu só existe no registro
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                chave = winreg.QueryValueEx(k, "GOOGLE_PLACES_API_KEY")[0]
        except OSError:
            pass
    if not chave:
        sys.exit("Falta a chave: crie a variável de ambiente GOOGLE_PLACES_API_KEY.")
    return chave


def ler_json(arq, padrao):
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


class Api:
    def __init__(self, chave):
        self.chave = chave
        self.mes = dt.date.today().strftime("%Y-%m")
        self.uso = ler_json(ARQ_USO, {})
        self.feitas = 0

    def buscar(self, corpo, campos):
        if self.uso.get(self.mes, 0) >= LIMITE_MENSAL:
            raise LimiteAtingido
        req = urllib.request.Request(
            URL_BUSCA,
            data=json.dumps(corpo).encode(),
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": self.chave, "X-Goog-FieldMask": campos},
        )
        for tentativa in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    dados = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and tentativa < 3:
                    time.sleep(2 ** tentativa)
                    continue
                detalhe = e.read().decode(errors="replace")
                sys.exit(f"A API do Google recusou a busca ({e.code}):\n{detalhe}")
        self.uso[self.mes] = self.uso.get(self.mes, 0) + 1
        self.feitas += 1
        ARQ_USO.write_text(json.dumps(self.uso), encoding="utf-8")
        return dados


def area_da_cidade(api, cidade):
    dados = api.buscar({"textQuery": cidade, "languageCode": "pt-BR", "regionCode": "BR"},
                       "places.displayName,places.viewport")
    lugares = dados.get("places") or []
    if not lugares or "viewport" not in lugares[0]:
        sys.exit(f"Não achei a área de '{cidade}'. Tente no formato 'Cidade, UF'.")
    return lugares[0]["viewport"]


def dividir(ret):
    lo, hi = ret["low"], ret["high"]
    mlat = (lo["latitude"] + hi["latitude"]) / 2
    mlng = (lo["longitude"] + hi["longitude"]) / 2
    return [
        {"low": {"latitude": a, "longitude": b}, "high": {"latitude": c, "longitude": d}}
        for a, c in ((lo["latitude"], mlat), (mlat, hi["latitude"]))
        for b, d in ((lo["longitude"], mlng), (mlng, hi["longitude"]))
    ]


def buscar_area(api, termo, ret, profundidade=0):
    """Até 60 lugares por área; se lotar, divide em 4 para achar os que ficaram de fora."""
    lugares, token = [], None
    for _ in range(3):
        corpo = {"textQuery": termo, "languageCode": "pt-BR", "regionCode": "BR", "pageSize": 20,
                 "locationRestriction": {"rectangle": ret}}
        if token:
            corpo["pageToken"] = token
        dados = api.buscar(corpo, CAMPOS_LUGAR)
        lugares += dados.get("places", [])
        token = dados.get("nextPageToken")
        if not token:
            break
    if len(lugares) >= 60 and profundidade < PROFUNDIDADE_MAX:
        for sub in dividir(ret):
            lugares += buscar_area(api, termo, sub, profundidade + 1)
    return lugares


def situacao_site(url):
    """('sem site' | 'só Instagram' ...) ou None se tem site próprio."""
    if not url:
        return "sem site", None
    host = (urllib.parse.urlparse(url).hostname or "").removeprefix("www.")
    for dominio, nome in PERFIS.items():
        if host == dominio or host.endswith("." + dominio):
            return f"só {nome}", nome
    return None


def tipo_telefone(digitos):
    if digitos.startswith("0"):
        return "0800"
    if len(digitos) == 11 and digitos[2] == "9":
        return "celular"
    return "fixo"


def tratamento(lead, termo):
    """O tratamento que a clínica mostra no nome ou na categoria; senão, o que foi pesquisado."""
    texto = f"{lead['nome']} {lead['categoria']}"
    for padrao, nome, alto in TRATAMENTOS:
        if re.search(padrao, texto, re.I):
            return nome, alto
    return ("dentista" if NICHO_POR_TERMO.get(termo) is NICHO_ODONTO else termo), False


def pontos(lead):
    """Chance de virar cliente: WhatsApp, clínica ativa, tratamento caro e quem decide lendo a mensagem."""
    p = 30 if lead["tipo_telefone"] == "celular" else 0
    p += round(30 * math.log10(1 + min(lead["avaliacoes"], 150)) / math.log10(151))  # 150+ avaliações = 30
    nota = lead["nota"] or 0
    p += 10 if nota >= 4.7 else 5 if nota >= 4.3 else 0
    p += 15 if lead["ticket_alto"] else 0
    p += 10 if re.search(r"\bDra?\b\.?\s", lead["nome"]) else 0  # consultório da própria dentista
    p += 5 if lead["perfil"] else 0  # já cuida de rede social
    return p


def nivel(lead):
    if lead["tipo_telefone"] != "celular":
        return "C"
    return "A" if lead["pontos"] >= 75 else "B" if lead["pontos"] >= 60 else "C"


def nome_curto(nome):
    """Tira as palavras-chave que muitos põem no nome do Google: 'Dra X | Implantes | POA' vira 'Dra X'."""
    separador = r"\s*\|\s*|\s+[-–]\s+|(?<!\bDr)(?<!\bDra)(?<!\b[A-ZÀ-Ú])\.\s+(?=[A-ZÀ-Ú])"
    partes = [p for p in re.split(separador, re.sub(r"\s*\(.*?\)", "", nome)) if p]
    pessoa = next((p for p in partes if re.match(r"Dra?\.?\s", p)), None)
    return pessoa or (partes[0] if partes else nome)


def sem_caixa_alta(nome):
    """'ODONTIK PREMIUM ODONTOLOGIA' vira 'Odontik Premium Odontologia'. Sigla de uma palavra só fica como está."""
    if not nome.isupper() or " " not in nome:
        return nome
    palavras = nome.title().split(" ")
    return " ".join(p.lower() if i and p.lower() in {"de", "da", "do", "das", "dos", "e"} else p
                    for i, p in enumerate(palavras))


def mensagem(lead, termo, segmento, publico, cidade):
    """Curta, com o nome da clínica na primeira linha (é o que aparece na notificação e faz parecer conversa,
    e não disparo em massa), quem fala, uma chamada fácil de responder e a saída para quem não quer.
    Quando o nome no Google é de uma dentista ('Dra. Vanessa Freitas'), quem lê costuma ser ela: a mensagem
    fala direto com ela, pelo título e primeiro nome que ela mesma usa.
    Duas versões em teste A/B (lead["modelo"]), para ver qual tem mais resposta:
    A é a do Matheus (15/out): o resultado ("converter mais contatos em pacientes") e "posso te mostrar uma ideia";
    B pergunta se alguém responde na hora o paciente que chama às 22h e oferece mostrar como ficaria."""
    nome = sem_caixa_alta(nome_curto(lead["nome"]))
    pessoa = re.match(r"(Dra?)\.?\s+(\S+)", nome)
    if pessoa:
        abertura = f"Oi, {pessoa[1]}. {pessoa[2]}, tudo bem?"
        onde, aqui, chama, alguem = "o seu consultório", "no seu consultório", "te chama", "você consegue"
    else:
        abertura = f"Oi, tudo bem? É da {nome}?"
        onde, aqui, chama, alguem = "a clínica de vocês", "na clínica de vocês", "chama vocês", "alguém consegue"
    trat = lead["tratamento"]
    assunto = "marcar uma avaliação" if trat in ("dentista", termo) else f"saber de {trat}"
    saida = "Se não for o momento, me avisa que não mando mais."

    if lead["modelo"] == "A":
        partes = [f"Sou {REMETENTE}. Ajudamos {segmento} a converter mais contatos em {publico} "
                  "através de automações com IA.",
                  f"Posso te mostrar uma ideia que pode fazer sentido para {onde}?"]
    else:
        # "Estamos lançando": o assistente ainda não existe, então a oferta é mostrar como ficaria
        partes = [f"Pergunta rápida: quando um paciente {chama} às 22h querendo {assunto}, {alguem} responder na hora?",
                  f"Sou {REMETENTE}. Estamos lançando um assistente de IA que responde na hora e chama de volta "
                  f"quem não agendou. Posso te mostrar como ficaria {aqui}?"]
    return "\n\n".join([abertura, *partes, saida])


def montar_lead(lugar, termo, segmento, publico, cidade):
    if lugar.get("businessStatus") != "OPERATIONAL":
        return None
    telefone = lugar.get("nationalPhoneNumber")
    site = situacao_site(lugar.get("websiteUri"))
    if not telefone or site is None:
        return None
    digitos = re.sub(r"\D", "", telefone)
    lead = {
        "id": lugar["id"],
        "nome": lugar.get("displayName", {}).get("text", ""),
        "categoria": lugar.get("primaryTypeDisplayName", {}).get("text", termo),
        "telefone": telefone,
        "digitos": digitos,
        "tipo_telefone": tipo_telefone(digitos),
        "nota": lugar.get("rating"),
        "avaliacoes": lugar.get("userRatingCount", 0),
        "site": site[0],
        "perfil": site[1],
        "link_atual": lugar.get("websiteUri", ""),
        "endereco": lugar.get("formattedAddress", ""),
        "maps": lugar.get("googleMapsUri", ""),
        "cidade": cidade,
        "bairro": lugar.get("bairro"),
    }
    # Sem avaliação costuma ser ficha parada ou repetida; rede e franquia decidem na matriz
    if lead["tipo_telefone"] == "0800" or not lead["avaliacoes"] or REDES.search(lead["nome"]):
        return None
    nicho = NICHO_POR_TERMO.get(termo)
    if nicho and not nicho.search(f"{lead['nome']} {lead['categoria']}"):
        return None
    lead["tratamento"], lead["ticket_alto"] = tratamento(lead, termo)
    lead["segmento"] = segmento  # filtro da página; fica fora do CSV
    lead["pontos"] = pontos(lead)
    lead["nivel"] = nivel(lead)
    # sorteio fixo pelo id: o lead fica sempre na mesma versão, mesmo gerando a lista de novo
    lead["modelo"] = "AB"[int(hashlib.md5(lead["id"].encode()).hexdigest(), 16) % 2]
    lead["mensagem"] = mensagem(lead, termo, segmento, publico, cidade)
    lead["whatsapp"] = f"https://wa.me/55{digitos}?text={urllib.parse.quote(lead['mensagem'])}"
    return lead


def salvar_csv(leads, arq):
    # As 14 primeiras colunas são as que o CRM lê (CRM/src/lib/csv-leads.ts): não mude a ordem nem os nomes.
    colunas = ["nivel", "nome", "categoria", "telefone", "tipo_telefone", "nota", "avaliacoes", "site",
               "link_atual", "endereco", "cidade", "maps", "whatsapp", "mensagem",
               "modelo", "pontos", "bairro", "tratamento"]
    with open(arq, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, colunas, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for lead in leads:
            w.writerow({**lead, "nota": str(lead["nota"] or "").replace(".", ",")})


def salvar_html(leads, arq, titulo):
    dados = json.dumps(leads, ensure_ascii=False).replace("</", "<\\/")
    # o que o coletar.py já pesquisou, por nicho, para o painel "Buscar mais leads"
    cobertura = {}
    for chave, feita in ler_json(PASTA / "coleta" / "buscas_feitas.json", {}).items():
        c = cobertura.setdefault(chave.split("|")[0], {"pesquisas": 0, "novos": 0})
        c["pesquisas"] += 1
        c["novos"] += feita.get("novos", 0)
    pagina = (MODELO_HTML.replace("{{TITULO}}", html.escape(titulo))
              .replace("{{COBERTURA}}", json.dumps(cobertura, ensure_ascii=False).replace("</", "<\\/"))
              .replace("{{DADOS}}", dados))
    arq.write_text(pagina, encoding="utf-8")


def ler_coletados(arq):
    """Cada linha vira um lugar no formato da API, para passar pelo mesmo filtro e mensagem."""
    info = {termo: (segmento, publico) for termo, segmento, publico in TERMOS}
    for linha in Path(arq).read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        c = json.loads(linha)
        busca = urllib.parse.quote(f"{c['nome']}, {c['endereco']}, {c['cidade']}")
        # o coletar.py já traz o endereço completo (com bairro e cidade) e o link da ficha
        endereco = c["endereco"] if c["cidade"] in c["endereco"] else f"{c['endereco']} - {c['cidade']}"
        lugar = {
            "id": c["id"],
            "displayName": {"text": c["nome"]},
            "formattedAddress": endereco,
            "nationalPhoneNumber": c["telefone"],
            "websiteUri": c["site"],
            "rating": c["nota"],
            "userRatingCount": c["avaliacoes"],
            "businessStatus": c["status"],
            "googleMapsUri": c.get("link") or f"https://www.google.com/maps/search/?api=1&query={busca}",
            "primaryTypeDisplayName": {"text": c["categoria"] or c["termo"]},
            "bairro": c.get("bairro"),
        }
        yield lugar, c["termo"], *info.get(c["termo"], ("negócios como o de vocês", "clientes")), c["cidade"]


def coletar_pela_api(api, cidades, aceitar):
    """Busca cada termo em cada cidade. Devolve (leads, parou_no_limite)."""
    leads = []
    try:
        areas = {cidade.split(",")[0].strip(): area_da_cidade(api, cidade) for cidade in cidades}
        # termo por fora: os nichos prioritários cobrem todas as cidades antes dos outros
        for termo, segmento, publico in TERMOS:
            for nome_cidade, area in areas.items():
                achados = buscar_area(api, termo, area)
                novos = 0
                for lugar in achados:
                    if not aceitar(lugar["id"]):
                        continue
                    lead = montar_lead(lugar, termo, segmento, publico, nome_cidade)
                    if lead:
                        leads.append(lead)
                        novos += 1
                print(f"{nome_cidade} · {termo}: {len(achados)} achados, {novos} sem site próprio")
    except LimiteAtingido:
        print(f"\nParei no limite de {LIMITE_MENSAL} buscas do mês para não sair da cota grátis.")
        return leads, True
    return leads, False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cidade", action="append", help='"Cidade, UF"; repita para várias')
    ap.add_argument("--coletados", help="arquivo .jsonl coletado no navegador (não usa a API)")
    ap.add_argument("--repetir", action="store_true", help="inclui empresas já listadas em rodadas anteriores")
    args = ap.parse_args()
    if not args.cidade and not args.coletados:
        ap.error("use --cidade (API) ou --coletados (arquivo do navegador)")

    vistos = set(ler_json(ARQ_VISTOS, []))
    ids = set()

    def aceitar(id_lugar):
        if id_lugar in ids or (id_lugar in vistos and not args.repetir):
            return False
        ids.add(id_lugar)
        return True

    api, parou = None, False
    if args.coletados:
        # a Aureon atende só clínicas odontológicas: o que a coleta trouxe de outro nicho fica fora da lista
        leads = [lead for lugar, *resto in ler_coletados(args.coletados)
                 if resto[1] == "clínicas odontológicas" and aceitar(lugar["id"]) and (lead := montar_lead(lugar, *resto))]
        cidades = sorted({l["cidade"] for l in leads}) or ["coleta"]
    else:
        api = Api(ler_chave())
        leads, parou = coletar_pela_api(api, args.cidade, aceitar)
        cidades = [c.split(",")[0].strip() for c in args.cidade]

    # o mesmo telefone em duas fichas é a mesma pessoa: fica a ficha com mais avaliações
    por_telefone = {}
    for lead in leads:
        atual = por_telefone.get(lead["digitos"])
        if atual is None or lead["avaliacoes"] > atual["avaliacoes"]:
            por_telefone[lead["digitos"]] = lead
    leads = sorted(por_telefone.values(), key=lambda l: (l["nivel"], -l["pontos"]))
    pasta_saida = PASTA / "saida"
    pasta_saida.mkdir(exist_ok=True)
    # da coleta, o nome vem do arquivo (porto-alegre.jsonl → leads-porto-alegre-AAAA-MM-DD)
    rotulo = (Path(args.coletados).stem if args.coletados
              else "-".join(re.sub(r"\W+", "", c).lower() for c in cidades))
    base = pasta_saida / f"leads-{rotulo}-{dt.date.today():%Y-%m-%d}"
    salvar_csv(leads, base.with_suffix(".csv"))
    salvar_html(leads, base.with_suffix(".html"), ", ".join(cidades))
    ARQ_VISTOS.write_text(json.dumps(sorted(vistos | {l["id"] for l in leads})), encoding="utf-8")

    por_nivel = {n: sum(l["nivel"] == n for l in leads) for n in "ABC"}
    print(f"\n{len(leads)} leads: A {por_nivel['A']}, B {por_nivel['B']}, C {por_nivel['C']}")
    if api:
        print(f"Buscas nesta rodada: {api.feitas}. No mês: {api.uso.get(api.mes, 0)} de {LIMITE_MENSAL}.")
    print(f"Arquivos: {base.with_suffix('.html')}\n          {base.with_suffix('.csv')}")
    if parou:
        print("Rode de novo no mês que vem para continuar de onde parou.")


MODELO_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Leads · {{TITULO}}</title>
<style>
:root{--bg:#100F0D;--card:#1A1916;--line:#2E2B25;--gold:#C9A45C;--text:#F6F2EA;--muted:#A69F92;--ok:#7FB77E}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif;padding:24px 16px}
main{max-width:860px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 2px}
.ab{color:var(--muted);font-size:13px;margin:0 0 16px}
details.mais{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:16px}
details.mais summary{cursor:pointer;font-weight:600;color:var(--gold)}
details.mais p,details.mais li{color:var(--muted);font-size:14px}
details.mais code{color:var(--text);background:var(--bg);padding:1px 6px;border-radius:4px}
.filtros{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.filtros button,.filtros select{background:none;border:1px solid var(--line);color:var(--text);padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit;font-size:14px}
.filtros select{background:var(--bg);border-radius:8px}
.filtros button[aria-pressed=true]{border-color:var(--gold);color:var(--gold)}
.lead{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px}
.lead.enviado{opacity:.5}
.lead.respondeu{border-color:var(--ok)}
.topo{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.topo h2{font-size:17px;margin:0}
.nivel{color:var(--gold);font-weight:700;font-size:14px;white-space:nowrap}
.info{color:var(--muted);font-size:13px;margin:4px 0 10px}
.info a{color:var(--gold)}
textarea{width:100%;min-height:190px;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px;font:inherit;font-size:14px;resize:vertical}
.acoes{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.acoes a,.acoes button{background:var(--gold);color:var(--bg);border:0;border-radius:8px;padding:8px 14px;font:inherit;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer}
.acoes .sec{background:none;color:var(--text);border:1px solid var(--line)}
.marcas{margin-left:auto;display:flex;gap:14px;color:var(--muted);font-size:14px}
footer{color:var(--muted);font-size:12px;margin-top:24px}
@media (max-width:600px){.topo{flex-direction:column;gap:2px}.marcas{margin-left:0}}
</style></head><body><main>
<h1>Leads · {{TITULO}}</h1>
<p class="sub" id="resumo"></p>
<p class="ab" id="ab"></p>
<details class="mais" id="mais"><summary>Buscar mais leads</summary>
<p>Na pasta <code>Leads</code>, dê dois cliques em <code>Buscar mais leads.bat</code> e escolha os nichos e onde buscar. Uma janela do Chrome abre e faz as pesquisas sozinha, uns 20 segundos cada: deixe ela aberta até o fim. Só entram clínicas novas, e esta página é gerada de novo em <code>Leads/saida</code>, com a data de hoje no nome.</p>
<p>Depois, no CRM, vá em Importar e suba o CSV novo: quem já estava lá não se repete.</p>
<ul id="cobertura"></ul>
</details>
<div class="filtros" id="filtros"></div>
<div id="lista"></div>
<footer>Pontos: celular +30, avaliações até +30 (150 ou mais), nota 4,7 ou mais +10, tratamento de valor alto (implante, lentes, harmonização, alinhador, prótese) +15, consultório de Dr. ou Dra. +10, já usa rede social +5.
Nível A: celular e 75 pontos ou mais. B: celular e 60 ou mais. C: o resto, inclusive telefone fixo (melhor ligar).
As versões A e B da mensagem estão em teste: marque "Respondeu" para ver qual funciona melhor. Mande poucas por dia, uma por vez. Dados de lugares: Google Maps.</footer>
</main>
<script>
const LEADS = {{DADOS}};
const COBERTURA = {{COBERTURA}};
const LOTE = 25;
const ler = (tipo, id) => { try { return localStorage.getItem(`lead-${tipo}:${id}`) === "1"; } catch (e) { return false; } };
const gravar = (tipo, id, sim) => { try { sim ? localStorage.setItem(`lead-${tipo}:${id}`, "1") : localStorage.removeItem(`lead-${tipo}:${id}`); } catch (e) {} };
const enviado = id => ler("enviado", id);
const respondeu = id => ler("respondeu", id);
const BOTOES = [["todos", "Todos"], ["lote", `Lote do dia (${LOTE})`], ["A", "Nível A"], ["B", "Nível B"], ["C", "Nível C"], ["pendentes", "Não enviados"], ["respondeu", "Responderam"]];
const SELECTS = [["segmento", "Todos os segmentos"], ["bairro", "Todos os bairros"], ["tratamento", "Todos os tratamentos"], ["modelo", "Versões A e B"]];
const escolha = { segmento: "", bairro: "", tratamento: "", modelo: "" };
const combina = l => Object.entries(escolha).every(([campo, valor]) => !valor || l[campo] === valor);
let filtro = "todos";

function el(tag, props, ...filhos) {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...filhos);
  return n;
}

// Os 25 de mais pontos, com celular, que ainda não receberam mensagem, dentro dos filtros escolhidos
function lote() {
  return LEADS.filter(l => l.tipo_telefone === "celular" && !enviado(l.id) && combina(l))
    .sort((a, b) => b.pontos - a.pontos).slice(0, LOTE);
}

function cartao(l) {
  const texto = el("textarea", { value: l.mensagem, ariaLabel: "Mensagem para " + l.nome });
  const zap = el("a", { textContent: "Abrir no WhatsApp", target: "_blank", rel: "noopener", href: l.whatsapp });
  zap.addEventListener("click", () => { zap.href = "https://wa.me/55" + l.digitos + "?text=" + encodeURIComponent(texto.value); });
  const copiar = el("button", { textContent: "Copiar", className: "sec", type: "button" });
  copiar.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(texto.value); } catch (e) { texto.select(); document.execCommand("copy"); }
    copiar.textContent = "Copiado";
  });
  const caixaEnviado = el("input", { type: "checkbox", checked: enviado(l.id) });
  const caixaRespondeu = el("input", { type: "checkbox", checked: respondeu(l.id) });
  const card = el("article", { className: "lead" });
  const pintar = () => {
    card.classList.toggle("enviado", caixaEnviado.checked && !caixaRespondeu.checked);
    card.classList.toggle("respondeu", caixaRespondeu.checked);
  };
  caixaEnviado.addEventListener("change", () => { gravar("enviado", l.id, caixaEnviado.checked); pintar(); resumo(); });
  caixaRespondeu.addEventListener("change", () => {
    gravar("respondeu", l.id, caixaRespondeu.checked);
    if (caixaRespondeu.checked && !caixaEnviado.checked) { caixaEnviado.checked = true; gravar("enviado", l.id, true); }
    pintar(); resumo();
  });
  pintar();
  const nota = l.nota ? `nota ${String(l.nota).replace(".", ",")} (${l.avaliacoes})` : "sem avaliações";
  const lugar = [l.bairro, l.cidade].filter(Boolean).join(", ");
  card.append(
    el("div", { className: "topo" }, el("h2", { textContent: l.nome }),
      el("span", { className: "nivel", textContent: `Nível ${l.nivel} · ${l.pontos} pts · versão ${l.modelo}` })),
    el("p", { className: "info" },
      `${l.categoria} · ${l.tratamento} · ${nota} · ${l.site} · ${l.telefone} (${l.tipo_telefone}) · ${lugar} · `,
      el("a", { href: l.maps, target: "_blank", rel: "noopener", textContent: "ver no Maps" })),
    texto,
    el("div", { className: "acoes" }, zap, copiar,
      el("a", { className: "sec", href: "tel:+55" + l.digitos, textContent: "Ligar" }),
      el("span", { className: "marcas" }, el("label", {}, caixaEnviado, " Enviado"), el("label", {}, caixaRespondeu, " Respondeu"))));
  return card;
}

function resumo() {
  const env = LEADS.filter(l => enviado(l.id)), resp = LEADS.filter(l => respondeu(l.id));
  document.getElementById("resumo").textContent = `${LEADS.length} leads · ${env.length} enviados · ${resp.length} responderam`;
  const versoes = ["A", "B"].map(m => {
    const e = env.filter(l => l.modelo === m).length, r = resp.filter(l => l.modelo === m).length;
    return `versão ${m}: ${r} de ${e} responderam` + (e ? ` (${Math.round(100 * r / e)}%)` : "");
  });
  const aviso = env.length < 40 ? " · compare depois de uns 20 envios de cada" : "";
  document.getElementById("ab").textContent = "Teste da mensagem: " + versoes.join(" · ") + aviso;
}

function render() {
  const doLote = filtro === "lote" ? lote() : null;
  const base = doLote ?? LEADS.filter(l => filtro === "todos" || l.nivel === filtro ||
    (filtro === "pendentes" && !enviado(l.id)) || (filtro === "respondeu" && respondeu(l.id)));
  const visiveis = base.filter(combina);
  document.getElementById("lista").replaceChildren(...visiveis.map(cartao));
  const botoes = BOTOES.map(([valor, rotulo]) => {
    const b = el("button", { type: "button", textContent: rotulo });
    b.setAttribute("aria-pressed", valor === filtro);
    b.addEventListener("click", () => { filtro = valor; render(); });
    return b;
  });
  const selects = SELECTS.map(([campo, todos]) => {
    const valores = [...new Set(LEADS.map(l => l[campo]).filter(Boolean))].sort();
    const s = el("select", { ariaLabel: todos }, el("option", { value: "", textContent: todos }),
      ...valores.map(v => el("option", { value: v, textContent: campo === "modelo" ? "Versão " + v : v })));
    s.value = escolha[campo];
    s.addEventListener("change", () => { escolha[campo] = s.value; render(); });
    return s;
  });
  document.getElementById("filtros").replaceChildren(...botoes, ...selects);
  resumo();
}

const itens = Object.entries(COBERTURA);
document.getElementById("cobertura").replaceChildren(...(itens.length
  ? itens.map(([termo, c]) => el("li", { textContent: `${termo}: ${c.pesquisas} pesquisas feitas, ${c.novos} leads novos` }))
  : [el("li", { textContent: "Nenhuma pesquisa automática feita ainda." })]));
// O painel ensina a usar o .bat do computador do Matheus: na versão publicada ele não serve
if (location.protocol !== "file:") document.getElementById("mais").remove();
render();
</script></body></html>
"""

if __name__ == "__main__":
    main()
