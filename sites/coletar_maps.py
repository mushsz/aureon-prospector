"""Coleta no Google Maps empresas sem site (ou com site quebrado) e lê a ficha de cada uma, para o vendedor de sites.

Usa a janela do Chrome do Leads/coletar.py (mesmo perfil, mesmas pausas, para no aviso de robô).
Da lista: quem tem telefone, está aberto e tem 5 avaliações ou mais. Quem tem site próprio passa por uma checagem
rápida: se o site responde, fica de fora; se está fora do ar, entra ("site quebrado"). Os candidatos têm a ficha
aberta para ler avaliações, tópicos citados, respostas do dono, fotos, horário e atributos.
Uso: python coletar_maps.py [--max 15] [--teste]       (grava em dados/maps.jsonl, ao lado deste arquivo)
--max: quantas pesquisas fazer nesta rodada (a rodada diária usa 15). As do dia vêm do plano_diario.py.
"""

import argparse
import datetime as dt
import json
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PASTA = Path(__file__).resolve().parent
LEADS = PASTA.parent if (PASTA.parent / "coletar.py").exists() else Path(r"C:\Users\mathe\Documents\Aureon\Leads")
sys.path.insert(0, str(LEADS))  # a pasta Leads, onde está o coletar.py
import coletar as co  # noqa: E402
import plano_diario as pd  # noqa: E402

# (termo do Maps, segmento, onde). Odonto fica de fora: é o público da própria Aureon.
# Nicho com muita empresa vai bairro por bairro. Buffet saiu: a lista do Maps não mostra o telefone deles.
PLANO = [
    ("escritório de advocacia", "advocacia", pd.bairros()),
    ("escritório de contabilidade", "contabilidade", pd.bairros()),
    ("clínica de estética", "estética", pd.bairros()),
    ("fisioterapia", "fisioterapia", pd.bairros()),
    ("psicólogo", "psicologia", pd.bairros()),
    ("arquiteto", "arquitetura e interiores", pd.bairros()),
    ("estética automotiva", "estética automotiva", pd.bairros()),
    ("móveis planejados", "móveis planejados", pd.cidades()),
    ("energia solar", "energia solar", pd.cidades()),
    ("dedetizadora", "dedetização", pd.cidades()),
]
# Construtores grátis: a pessoa quis ter site, mas sem domínio próprio
GRATIS = re.compile(r"wixsite\.com|webnode|blogspot|wordpress\.com|sites\.google|business\.site|negocio\.site|"
                    r"ueniweb|weebly|jimdo|site123|comunidades\.net|wix\.com|my\.canva\.site|carrd\.co|"
                    r"godaddysites|webs\.com|yolasite|strikingly", re.I)
ESTACIONADO = re.compile(r"domain (is )?for sale|dom[ií]nio (est[aá] )?(à|a) venda|this domain|parked|"
                         r"em constru[cç][aã]o|under construction|coming soon|site em manuten|account suspended|"
                         r"conta suspensa|registro\.br|hostgator.*default|index of /", re.I)

LISTA_JS = r"""(async () => {
  const S = ms => new Promise(r => setTimeout(r, ms)), B = () => document.body ? document.body.innerText : "";
  for (let i = 0; i < 40 && !document.querySelector('[role=feed] [role=article]') && !/não encontrou|tráfego incomum|unusual traffic/i.test(B()); i++) await S(400);
  await S(700);
  if (/tráfego incomum|unusual traffic|não sou um robô|recaptcha/i.test(B()) || location.pathname.startsWith('/sorry')) return {bloqueio: true, itens: []};
  const feed = document.querySelector('[role=feed]');
  for (let i = 0; feed && i < 5; i++) {
    const antes = feed.querySelectorAll('[role=article]').length;
    feed.scrollTop = feed.scrollHeight;
    await S(1500 + Math.random() * 800);
    if (feed.querySelectorAll('[role=article]').length === antes) break;
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
      fechado: /Fechado permanentemente|Fechado temporariamente/.test(t)}];
  });
  return {bloqueio: false, itens};
})()"""

FICHA_JS = (PASTA / "ficha.js").read_text(encoding="utf-8")


def checar_site(url):
    """Abre o site como um navegador faria. Devolve None se está no ar, ou o motivo de estar quebrado.
    Só conta como quebrado se a página do link e a página inicial do domínio falharem, duas vezes:
    a mensagem vai dizer isso ao dono, então engano aqui custa caro."""
    raiz = re.sub(r"^(https?://[^/?#]+).*", r"\1/", url)
    for tentativa in range(2):
        motivo = _abrir(url)
        if motivo and raiz != url:
            motivo = _abrir(raiz) and motivo
        if not motivo:
            return None
        time.sleep(2)
    return motivo


def _abrir(url):
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36",
                                                  "Accept-Language": "pt-BR"})
    try:
        with urllib.request.urlopen(pedido, timeout=12, context=ssl.create_default_context()) as r:
            corpo = r.read(60000).decode("utf-8", "replace")
            final = r.geturl()
    except ssl.SSLError:
        return "certificado de segurança vencido (o navegador avisa 'não seguro')"
    except urllib.error.HTTPError as e:
        return None if e.code in (401, 403, 429) else f"o site responde com erro {e.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ValueError) as e:
        motivo = str(getattr(e, "reason", e))
        if "CERTIFICATE" in motivo.upper() or "SSL" in motivo.upper():
            return "certificado de segurança vencido (o navegador avisa 'não seguro')"
        if "getaddrinfo" in motivo or "Name or service" in motivo:
            return "o domínio não responde (pode ter vencido)"
        return "o endereço do site não abre"
    texto = re.sub(r"<[^>]+>", " ", corpo)
    if ESTACIONADO.search(texto[:20000]) and len(re.sub(r"\s+", " ", texto)) < 6000:
        return "o domínio está parado (página de 'em construção' ou à venda)"
    if urllib.parse.urlparse(final).hostname and GRATIS.search(final):
        return None  # redirecionou para um construtor grátis: a classificação abaixo cuida
    return None


def tipo_link(url):
    """('sem site' | 'perfil' | 'grátis' | 'próprio', rótulo)"""
    if not url:
        return "sem site", ""
    if GRATIS.search(url):
        return "grátis", (urllib.parse.urlparse(url).hostname or "").removeprefix("www.")
    perfil = co.bl.situacao_site(url)
    if perfil is not None:
        return "perfil", perfil[1]
    return "próprio", (urllib.parse.urlparse(url).hostname or "").removeprefix("www.")


def pesquisar(chrome, termo, segmento, bairro, cidade, conhecidos):
    lista = chrome.ler(co.url_busca(termo, bairro, cidade), LISTA_JS)
    if lista.get("bloqueio"):
        raise co.Bloqueio
    novos, pulados = [], 0
    for item in lista.get("itens", []):
        if item["id"] in conhecidos or item["fechado"] or not item["telefone"] or item["avaliacoes"] < 5:
            continue
        conhecidos.add(item["id"])
        tipo, rotulo = tipo_link(item["site"])
        quebrado = None
        if tipo == "próprio":
            quebrado = checar_site(item["site"])
            if not quebrado:
                pulados += 1
                continue  # tem site no ar
        ficha = chrome.ler(item["link"] + "?hl=pt-BR", FICHA_JS)
        if ficha.get("bloqueio"):
            raise co.Bloqueio
        time.sleep(random.uniform(1.5, 3))
        if ficha.get("fechado"):
            continue
        endereco = ficha.get("endereco") or item["endereco"]
        b, c = co.bairro_e_cidade(endereco)
        if c and c not in co.CIDADES_DA_REGIAO:
            continue
        site = ficha.get("site") or item["site"]
        if site != item["site"]:
            # em alguns ramos a lista não mostra o botão de site, e ele só aparece na ficha
            tipo, rotulo = tipo_link(site)
            if tipo == "próprio":
                quebrado = checar_site(site)
                if not quebrado:
                    pulados += 1
                    continue
        novos.append({
            **{k: item[k] for k in ("id", "nome", "nota", "avaliacoes", "categoria", "link")},
            **{k: v for k, v in ficha.items() if k not in ("bloqueio", "fechado", "nome", "endereco", "site") and v not in (None, "", [])},
            "nota": ficha.get("nota") or item["nota"], "avaliacoes": ficha.get("avaliacoes") or item["avaliacoes"],
            "categoria": ficha.get("categoria") or item["categoria"],
            "endereco": endereco, "telefone": ficha.get("telefone") or item["telefone"], "site": site,
            "tipo_link": tipo, "rotulo_link": rotulo, "site_quebrado": quebrado or "",
            "termo": termo, "segmento": segmento, "cidade": c or cidade, "bairro": b or bairro or "",
        })
    return lista, novos, pulados


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=15)
    ap.add_argument("--teste", action="store_true")
    args = ap.parse_args()
    arq = PASTA / "dados" / "maps.jsonl"
    arq.parent.mkdir(exist_ok=True)
    feitas_arq = arq.with_suffix(".buscas.json")
    feitas = co.bl.ler_json(feitas_arq, {})
    conhecidos = co.ler_conhecidos(arq)
    pendentes, nunca = pd.do_dia(PLANO, feitas, 1 if args.teste else args.max)
    print(f"{len(pendentes)} pesquisas nesta rodada ({nunca} ainda nunca feitas).", flush=True)
    co.manter_acordado(True)
    chrome = None
    try:
        chrome = co.Chrome()
        for n, (termo, segmento, bairro, cidade) in enumerate(pendentes, 1):
            if n % 20 == 0:
                chrome.reiniciar()
            lista, novos, pulados = pesquisar(chrome, termo, segmento, bairro, cidade, conhecidos)
            with open(arq, "a", encoding="utf-8") as fh:
                for lead in novos:
                    fh.write(json.dumps(lead, ensure_ascii=False) + "\n")
            feitas[pd.chave((termo, segmento, bairro, cidade))] = {
                "data": dt.date.today().isoformat(), "resultados": len(lista.get("itens", [])), "novos": len(novos), "com_site": pulados}
            feitas_arq.write_text(json.dumps(feitas, ensure_ascii=False), encoding="utf-8")
            onde = f"{bairro}, {cidade}" if bairro else cidade
            print(f"[{n}/{len(pendentes)}] {termo} · {onde}: {len(lista.get('itens', []))} resultados, "
                  f"{pulados} com site no ar, {len(novos)} leads", flush=True)
            time.sleep(random.uniform(4, 8))
    except co.Bloqueio:
        print("\nO Google pediu verificação de robô. Parei aqui, sem tentar contornar.", flush=True)
    except (ConnectionError, OSError) as e:
        print(f"\nA conexão com o Chrome caiu ({e}). O que já foi coletado está salvo.", flush=True)
    finally:
        if chrome:
            chrome.fechar()
        co.manter_acordado(False)


if __name__ == "__main__":
    main()
