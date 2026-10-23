"""Compras públicas abertas no RS (PNCP) com itens de material e equipamento médico-hospitalar.

Uso: python pncp.py            (grava em dados/compras_abertas.json)
Lê /api/consulta/v1/contratacoes/proposta (proposta em aberto) por modalidade, filtra pelo objeto
e confere os itens de cada compra para dizer o que ali é da linha do fornecedor.
"""

import datetime as dt
import json
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

UF = "RS"
MODALIDADES = {4: "Concorrência eletrônica", 6: "Pregão eletrônico", 7: "Pregão presencial", 8: "Dispensa"}
BASE = "https://pncp.gov.br/api"
ARQ = Path(__file__).resolve().parent / "dados" / "compras_abertas.json"


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


# Linhas do fornecedor: (rótulo, padrão no texto já sem acento)
LINHAS = [
    # fio de sutura vem descrito com a agulha; "fio de nylon" sozinho é de artesanato
    ("fio de sutura", r"sutura|fio cirurgic|\b(nylon|nailon|poliglactina|catgut|categute|polipropileno|mononylon|poliglecaprona|polidioxanona|seda)\b.{0,80}\bagulha"),
    ("maca", r"\bmacas?\b (hospitala|de transporte|para (transporte|exame|remocao)|de (exame|procedimento|remocao|resgate)|tipo|fixa|retratil|com |em |\d|inox|ginecolog|obstetr|estofad|articulad|pediatric|veterin)"),
    ("poltrona", r"poltronas? (hospitala|reclinave|do paciente|para (coleta|acompanhante|hemodialise|quimio|doacao|pacientes?)|de (coleta|acompanhante|hemodialise|quimioterapia|descanso|repouso|doacao))"),
    ("cama hospitalar", r"camas? (hospitala|fawler|fowler|motorizad)|cama hospitalar"),
    ("cadeira de rodas/banho", r"cadeiras? (de rodas|de banho|higienica|para banho|de coleta)"),
    ("glicosímetro e tiras", r"glicosimetr|glicemi|tiras? (reagente|para (teste|medicao) de glicose)|lancetas?"),
    ("torre de vídeo", r"torre de video|videolaparoscop|videocirurg|(video)?endoscopio\b|colonoscopio|histeroscopio|artroscopio|microcamera|processadora de video|fonte de luz (fria|de led|xenon)"),
    ("monitor/DEA", r"monitor multiparametr|desfibrilad|cardioversor|\bdea\b|eletrocardiograf|oximetr"),
    ("foco e mesa cirúrgica", r"foco cirurgic|mesa cirurgic|mesa ginecolog|mesa de exame|bisturi eletric|eletrocauter"),
    ("autoclave", r"autoclave"),
    ("aspirador", r"aspirador (cirurgic|de secrec|portatil)"),
    ("carro de emergência", r"carro (de emergencia|de curativo|de medicac)|carrinho (de emergencia|de curativo)"),
    ("mobiliário hospitalar", r"mobiliario hospitalar|suporte (de|para) soro|escada (de |com )?2 degraus|biombo (hospitala|plumbifero|triplo|duplo|com rodizio)|bracadeira (para|de) injecao|mesa de (cabeceira|refeicao) hospitala|mesa (auxiliar|de instrumental) (inox|hospitala|em aco inox)|mesa de mayo|hamper"),
    ("aparelhos de exame", r"esfigmomanometr|estetoscop|otoscop|oftalmoscop|laringoscop|detector fetal|sonar fetal|termometro (clinico|digital|infravermelho|de testa)|negatoscop|balanca antropometr|nebulizador|\bambu\b|reanimador manual"),
    ("descartáveis", r"seringa|agulha hipoderm|cateter (intravenoso|periferico|venoso|nasal)|equipo (macro|micro|para|de soro|fotossens)|luvas? (de procedimento|cirurgic)|compressa|atadura|gaze|esparadrapo|micropore|sonda (vesical|nasogastr|uretral|foley|de aspiracao)|scalp|lamina de bisturi"),
]
# o que não é da linha dele mesmo quando cita seringa, agulha etc.
NAO_E_DA_LINHA = re.compile(r"vacina|pre-?preenchid|enoxaparina|medicament|comprimid|ampola|injetavel|\bmg\b|resina|odontolog|gengival|"
                            r"angioplastia|marcapasso|stent|opme|consignacao|bateria|papel termo|reagente para|laboratori")
PRINCIPAIS = {rot for rot, _ in LINHAS} - {"descartáveis"}
COMPRA_ITEM = re.compile("|".join(f"(?:{p})" for _, p in LINHAS))

OBJETO_SIM = re.compile(
    r"material(is)? (medic|hospitala|ambulatori|de enfermagem|cirurgic|de penso|de curativo|de consumo hospital)|"
    r"medico.?hospitala|equipamentos? (medic|hospitala|de saude|odonto-?medic|para (a )?(ubs|upa|unidade|hospital|saude|secretaria (municipal )?de saude))|"
    r"insumos? (de saude|hospitala|medic|de enfermagem|para (a )?saude)|mobiliario hospitala|veterinari|"
    + COMPRA_ITEM.pattern)
OBJETO_NAO = re.compile(
    r"prestacao de servico|servicos? (de|terceiriz)|locacao de (veiculo|ambulancia|imovel)|horas medicas|plantao|"
    r"exames|transporte|software|sistema|solucao tecnologica|manutencao|gases medicinais|oxigenio|alimenta|dieta|"
    r"nutricao|obra|reforma|construcao|uniforme|combustivel|limpeza|vigilancia|consulta|cirurgias eletivas|"
    r"leilao|veiculos?\b")
# listas grandes de outra coisa (escola, cozinha, oficina) que citam um termômetro ou uma maca no meio
OBJETO_OUTRA_COISA = re.compile(r"escola|expediente|brinquedo|jogos|alimentic|generos|oficinas? terapeutic|talheres|copos|"
                                r"auditorio|escritorio|informatica|esportiv|cozinha|epi\b|equipamentos de protecao")
EXCLUSIVA = re.compile(r"exclusiv\w*.{0,40}\b(me|epp|microempresas?|beneficiarias da lc)|lc ?123", re.I)
ORGAO_SAUDE = re.compile(r"hospital|saude|upa\b|pronto atend|consorcio|fundacao", re.I)


def pegar(url, tentativas=6):
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                corpo = r.read()
                return json.loads(corpo) if corpo else None
        except Exception as e:  # o PNCP às vezes devolve 5xx, 429 (muitas consultas) ou demora
            if t == tentativas - 1:
                print("  falhou:", url, e, file=sys.stderr)
                return None
            time.sleep((20 if "429" in str(e) else 3) * (t + 1))


def abertas():
    fim = (dt.date.today() + dt.timedelta(days=200)).strftime("%Y%m%d")
    for mod in MODALIDADES:
        pagina = 1
        while True:
            d = pegar(f"{BASE}/consulta/v1/contratacoes/proposta?dataFinal={fim}&uf={UF}"
                      f"&codigoModalidadeContratacao={mod}&pagina={pagina}&tamanhoPagina=50")
            if not d or not d.get("data"):
                break
            yield from d["data"]
            if not d.get("paginasRestantes"):
                break
            pagina += 1
            time.sleep(1)


def itens(c):
    cnpj, ano, seq = c["orgaoEntidade"]["cnpj"], c["anoCompra"], c["sequencialCompra"]
    todos, pagina = [], 1
    while pagina <= 20:
        d = pegar(f"{BASE}/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens?pagina={pagina}&tamanhoPagina=500")
        if not d:
            break
        todos += d
        if len(d) < 500:
            break
        pagina += 1
    return todos


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    hoje = dt.datetime.now()
    candidatos, vistos = [], set()
    for c in abertas():
        if c["numeroControlePNCP"] in vistos:
            continue
        vistos.add(c["numeroControlePNCP"])
        fim = c.get("dataEncerramentoProposta")
        if not fim or dt.datetime.fromisoformat(fim) < hoje:
            continue
        obj = sem_acento(c["objetoCompra"])
        orgao = f"{c['orgaoEntidade']['razaoSocial']} {c['unidadeOrgao']['nomeUnidade']}"
        if OBJETO_NAO.search(obj) and not re.search(r"aquisic|fornecimento|registro de preco", obj):
            continue
        if OBJETO_OUTRA_COISA.search(obj) and not re.search(r"hospitala|medic|ambulatori|enfermagem|cirurgic", obj):
            continue
        # a mesma compra às vezes aparece duas vezes (uma com o prefixo do Portal de Compras Públicas)
        chave = (c["orgaoEntidade"]["cnpj"], fim[:16], obj.replace("[portal de compras publicas] - ", "")[:80])
        if chave in vistos:
            continue
        vistos.add(chave)
        if OBJETO_SIM.search(obj) or (ORGAO_SAUDE.search(sem_acento(orgao)) and re.search(r"aquisic|registro de preco|fornecimento", obj)):
            candidatos.append(c)
    print(f"{len(vistos)} compras abertas no {UF}; {len(candidatos)} candidatas pelo objeto. Conferindo itens...")

    saida = []
    for n, c in enumerate(candidatos, 1):
        lista = itens(c)
        time.sleep(0.6)
        bate = []
        for it in lista:
            desc = sem_acento(it.get("descricao"))
            if it.get("materialOuServico") == "S":
                continue
            if NAO_E_DA_LINHA.search(desc):
                continue
            linhas = [rot for rot, p in LINHAS if re.search(p, desc)]
            if "maca" in linhas and re.search(r"ma[cç][aã]\b.{0,20}(fuji|gala|nacional|kg|fruta)|maçã", (it.get("descricao") or "").lower()):
                linhas.remove("maca")  # "maçã" sem acento vira "maca"
            if linhas:
                bate.append({"descricao": (it.get("descricao") or "")[:400], "quantidade": it.get("quantidade"),
                             "unidade": it.get("unidadeMedida"), "valor_total": it.get("valorTotal"),
                             "linhas": linhas, "situacao": it.get("situacaoCompraItemNome"),
                             "exclusiva_me": "exclusiva" in sem_acento(it.get("tipoBeneficioNome"))})
        if not bate:
            continue
        cnpj, ano, seq = c["orgaoEntidade"]["cnpj"], c["anoCompra"], c["sequencialCompra"]
        saida.append({
            "id": c["numeroControlePNCP"],
            "orgao": c["orgaoEntidade"]["razaoSocial"],
            "unidade": c["unidadeOrgao"]["nomeUnidade"],
            "cidade": c["unidadeOrgao"]["municipioNome"],
            "objeto": re.sub(r"\s+", " ", c["objetoCompra"]).strip(),
            "modalidade": c["modalidadeNome"],
            "srp": c.get("srp"),
            "valor_estimado": c.get("valorTotalEstimado"),
            "abertura": c.get("dataAberturaProposta"),
            "encerramento": c.get("dataEncerramentoProposta"),
            "link_pncp": f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}",
            "link_sistema": c.get("linkSistemaOrigem"),
            "total_itens": len(lista),
            "itens_da_linha": bate,
            "valor_itens_da_linha": round(sum(i["valor_total"] or 0 for i in bate), 2),
            "itens_principais": sum(1 for i in bate if set(i["linhas"]) & PRINCIPAIS),
            "exclusiva_me": bool(EXCLUSIVA.search(sem_acento(c["objetoCompra"]))) or all(i["exclusiva_me"] for i in bate),
        })
        print(f"[{n}/{len(candidatos)}] {c['unidadeOrgao']['municipioNome']}: {len(bate)} de {len(lista)} itens · {c['objetoCompra'][:70]!r}")
    saida.sort(key=lambda s: s["encerramento"])
    arq = Path(sys.argv[1]) if len(sys.argv) > 1 else ARQ
    with open(arq, "w", encoding="utf-8") as fh:
        json.dump(saida, fh, ensure_ascii=False, indent=1)
    print(f"\n{len(saida)} compras com itens da linha dele -> {arq}")


if __name__ == "__main__":
    main()
