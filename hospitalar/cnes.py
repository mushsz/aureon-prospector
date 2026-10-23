"""Hospitais, day hospitals, home cares e clínicas com centro cirúrgico, privados ou filantrópicos, de POA e região (CNES).

Uso: python cnes.py            (grava em dados/estabelecimentos.json)
Fonte: API de dados abertos do Ministério da Saúde. Fica de fora o que é público (compra por licitação,
que já vem do PNCP) e o que está desativado no CNES.
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

CIDADES = {431490: "Porto Alegre", 430460: "Canoas", 430920: "Gravataí", 430310: "Cachoeirinha",
           432300: "Viamão", 430060: "Alvorada", 431340: "Novo Hamburgo", 431870: "São Leopoldo",
           430770: "Esteio", 432000: "Sapucaia do Sul", 430930: "Guaíba"}
# (código do tipo no CNES, como chamar, só os que têm centro cirúrgico?)
TIPOS = [(5, "hospital", False), (7, "hospital especializado", False), (62, "day hospital", False),
         (73, "pronto atendimento", False), (20, "pronto-socorro", False), (36, "clínica com centro cirúrgico", True),
         (77, "home care", False)]
URL = "https://apidadosabertos.saude.gov.br/cnes/estabelecimentos"
ARQ = Path(__file__).resolve().parent / "dados" / "estabelecimentos.json"


def pegar(url):
    for t in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=90) as r:
                return json.load(r).get("estabelecimentos", [])
        except Exception as e:
            if t == 4:
                print("  falhou:", url, e, file=sys.stderr)
                return []
            time.sleep(5 * (t + 1))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    saida = []
    for cod, cidade in CIDADES.items():
        for tipo, rotulo, so_cirurgico in TIPOS:
            offset = 0
            while True:
                extra = "&estabelecimento_possui_centro_cirurgico=1" if so_cirurgico else ""
                lote = pegar(f"{URL}?codigo_municipio={cod}&codigo_tipo_unidade={tipo}{extra}&limit=20&offset={offset}")
                for e in lote:
                    nat = str(e.get("descricao_natureza_juridica_estabelecimento") or "")
                    # desativado no CNES, órgão público, empresa pública (2011: HCPA, GHC) ou de economia mista (2038):
                    # esses compram por licitação, que já aparece na aba de compras abertas
                    if e.get("codigo_motivo_desabilitacao_estabelecimento") or nat.startswith("1") or nat in ("2011", "2038"):
                        continue
                    saida.append({
                        "id": f"cnes-{e['codigo_cnes']}", "cnes": e["codigo_cnes"],
                        "nome": (e.get("nome_fantasia") or e.get("nome_razao_social") or "").strip(),
                        "razao_social": (e.get("nome_razao_social") or "").strip(),
                        "tipo": rotulo, "cidade": cidade,
                        "bairro": (e.get("bairro_estabelecimento") or "").strip().title(),
                        "endereco": f"{(e.get('endereco_estabelecimento') or '').strip().title()}, {e.get('numero_estabelecimento') or 's/n'}",
                        "telefone": (e.get("numero_telefone_estabelecimento") or "").strip(),
                        "email": (e.get("endereco_email_estabelecimento") or "").strip().lower(),
                        "cnpj": e.get("numero_cnpj") or e.get("numero_cnpj_entidade"),
                        "filantropico": nat.startswith("3"),
                        "centro_cirurgico": bool(e.get("estabelecimento_possui_centro_cirurgico")),
                        "internacao": bool(e.get("estabelecimento_possui_atendimento_hospitalar")),
                        "sus": e.get("estabelecimento_faz_atendimento_ambulatorial_sus") == "SIM",
                        "atualizado": e.get("data_atualizacao"),
                    })
                if len(lote) < 20 or offset > 2000:
                    break
                offset += 20
                time.sleep(0.4)
            print(f"{cidade} · {rotulo}: {sum(1 for s in saida if s['cidade'] == cidade and s['tipo'] == rotulo)}", flush=True)
    vistos, unicos = set(), []
    for s in saida:
        if s["cnes"] not in vistos:
            vistos.add(s["cnes"])
            unicos.append(s)
    arq = Path(sys.argv[1]) if len(sys.argv) > 1 else ARQ
    with open(arq, "w", encoding="utf-8") as fh:
        json.dump(unicos, fh, ensure_ascii=False, indent=1)
    print(f"\n{len(unicos)} estabelecimentos -> {arq}")


if __name__ == "__main__":
    main()
