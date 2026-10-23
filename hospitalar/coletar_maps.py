"""Coleta no Google Maps os compradores de material hospitalar (veterinárias, casas de repouso, estética...).

Usa a janela do Chrome do Leads/coletar.py (mesmo perfil, mesmas pausas, para no aviso de robô),
mas rola a lista até o fim e não descarta quem tem site: para fornecedor, site não importa.
Uso: python coletar_maps.py [--max 15] [--teste]      (grava em dados/maps.jsonl)
--max: quantas pesquisas fazer nesta rodada (a rodada diária usa 15). As do dia vêm do plano_diario.py.
"""

import argparse
import datetime as dt
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # a pasta Leads, onde está o coletar.py
import coletar as co  # noqa: E402
import plano_diario as pd  # noqa: E402

# (termo do Maps, segmento, onde pesquisar): nicho com muita empresa vai bairro por bairro
PLANO = [
    ("hospital veterinário", "veterinária", pd.bairros()),
    ("clínica veterinária", "veterinária", pd.bairros()),
    ("casa de repouso", "casa de repouso", pd.bairros()),
    ("clínica de estética", "estética", pd.bairros()),
    ("residencial geriátrico", "casa de repouso", pd.cidades()),
    ("home care", "home care", pd.cidades()),
    ("remoção de pacientes ambulância", "ambulância e remoção", pd.cidades()),
    ("clínica de cirurgia plástica", "cirurgia e day hospital", pd.cidades()),
    ("pronto atendimento 24 horas", "pronto atendimento", pd.cidades()),
]

LISTA_JS = r"""(async () => {
  const S = ms => new Promise(r => setTimeout(r, ms)), B = () => document.body ? document.body.innerText : "";
  for (let i = 0; i < 40 && !document.querySelector('[role=feed] [role=article]') && !/não encontrou|tráfego incomum|unusual traffic/i.test(B()); i++) await S(400);
  await S(700);
  if (/tráfego incomum|unusual traffic|não sou um robô|recaptcha/i.test(B()) || location.pathname.startsWith('/sorry')) return {bloqueio: true, itens: []};
  const feed = document.querySelector('[role=feed]');
  // rola como uma pessoa até o fim da lista (ou 8 vezes)
  for (let i = 0; feed && i < 8; i++) {
    const antes = feed.querySelectorAll('[role=article]').length;
    feed.scrollTop = feed.scrollHeight;
    await S(1600 + Math.random() * 900);
    if (/chegou ao final da lista|reached the end/i.test(feed.innerText)) break;
    if (feed.querySelectorAll('[role=article]').length === antes) { await S(1500); if (feed.querySelectorAll('[role=article]').length === antes) break; }
  }
  const itens = [...document.querySelectorAll('[role=feed] [role=article]')].flatMap(a => {
    const t = a.innerText, l = a.querySelector('a.hfpxzc');
    if (!l || /Patrocinado/.test(t)) return [];
    const id = (l.href.match(/!1s(0x\w+:0x\w+)/) || [])[1];
    if (!id) return [];
    const m = (a.querySelector('[role=img][aria-label*=estrela]')?.getAttribute('aria-label') || '').match(/([\d,]+) estrelas?(?: ([\d.]+))?/);
    const c = (t.split('\n').find(x => x.includes(' · ') && !/\(\d\d\)|^(Aberto|Fechado|Abre|Fecha)/.test(x)) || '').split(' · ').map(x => x.trim()).filter(Boolean);
    return [{id, nome: l.getAttribute('aria-label'), link: l.href.split('?')[0],
      nota: m ? +m[1].replace(',', '.') : null, avaliacoes: m && m[2] ? +m[2].replace(/\./g, '') : 0,
      categoria: c[0] || '', endereco: c.slice(1).join(' '),
      telefone: (t.match(/\(\d{2}\) \d{4,5}-\d{4}/) || [])[0] || '',
      site: a.querySelector('a[data-value=Website]')?.href || '',
      h24: /Aberto 24 horas|24 horas/i.test(t),
      fechado: /Fechado permanentemente|Fechado temporariamente/.test(t)}];
  });
  return {bloqueio: false, itens};
})()"""

FICHA_JS = co.FICHA_JS.replace(
    "bloqueio:",
    "h24: /Aberto 24 horas|24 horas/i.test(texto), novo: /Inaugurado recentemente|Recém-inaugurado|Newly opened/i.test(texto),\n    bloqueio:")


def pesquisar(chrome, termo, segmento, bairro, cidade, conhecidos):
    lista = chrome.ler(co.url_busca(termo, bairro, cidade), LISTA_JS)
    if lista.get("bloqueio"):
        raise co.Bloqueio
    novos = []
    for item in lista.get("itens", []):
        if item["id"] in conhecidos or item["fechado"] or not item["telefone"]:
            continue
        conhecidos.add(item["id"])
        # sem abrir a ficha: a lista já traz telefone, nota, site e "24 horas"; a cidade é a da pesquisa
        novos.append({**{k: item[k] for k in ("id", "nome", "nota", "avaliacoes", "categoria", "link",
                                               "endereco", "telefone", "site", "h24")},
                      "termo": termo, "segmento": segmento, "cidade": cidade, "bairro": bairro})
    return lista, novos


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=15)
    ap.add_argument("--teste", action="store_true")
    args = ap.parse_args()
    arq = Path(__file__).resolve().parent / "dados" / "maps.jsonl"
    feitas_arq = arq.with_suffix(".buscas.json")
    feitas = co.bl.ler_json(feitas_arq, {})
    conhecidos = co.ler_conhecidos(arq)
    pendentes, nunca = pd.do_dia(PLANO, feitas, 1 if args.teste else args.max)
    print(f"{len(pendentes)} pesquisas nesta rodada ({nunca} ainda nunca feitas).")
    co.manter_acordado(True)
    chrome = None
    try:
        chrome = co.Chrome()
        for n, (termo, segmento, bairro, cidade) in enumerate(pendentes, 1):
            if n % 25 == 0:
                chrome.reiniciar()
            lista, novos = pesquisar(chrome, termo, segmento, bairro, cidade, conhecidos)
            with open(arq, "a", encoding="utf-8") as fh:
                for lead in novos:
                    fh.write(json.dumps(lead, ensure_ascii=False) + "\n")
            feitas[pd.chave((termo, segmento, bairro, cidade))] = {
                "data": dt.date.today().isoformat(), "resultados": len(lista.get("itens", [])), "novos": len(novos)}
            feitas_arq.write_text(json.dumps(feitas, ensure_ascii=False), encoding="utf-8")
            onde = f"{bairro}, {cidade}" if bairro else cidade
            print(f"[{n}/{len(pendentes)}] {termo} · {onde}: {len(lista.get('itens', []))} resultados, {len(novos)} novos", flush=True)
            time.sleep(random.uniform(4, 8))
    except co.Bloqueio:
        print("\nO Google pediu verificação de robô. Parei aqui, sem tentar contornar.")
    except (ConnectionError, OSError) as e:
        print(f"\nA conexão com o Chrome caiu ({e}). O que já foi coletado está salvo.")
    finally:
        if chrome:
            chrome.fechar()
        co.manter_acordado(False)


if __name__ == "__main__":
    main()
