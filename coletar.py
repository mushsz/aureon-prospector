"""Busca mais leads no Google Maps pelo Chrome do PC, sem login e sem API, e gera a lista de novo.

Uso:
    python coletar.py                       # menu: escolhe nichos, onde buscar e quantas buscas
    python coletar.py --nicho "clínica odontológica" --onde poa --max-buscas 40

Abre uma janela própria do Chrome (perfil separado em .chrome-coleta/) e faz o que uma pessoa faria:
pesquisa "nicho + bairro", lê a lista e abre a ficha de cada clínica nova para pegar o endereço com
bairro. Sem login, o Maps mostra uns 10 resultados por pesquisa. Uma pesquisa a cada poucos segundos.
Se o Google pedir verificação de robô, para e guarda o que já fez: não tenta contornar.

As pesquisas feitas ficam em coleta/buscas_feitas.json, então cada rodada continua de onde a outra parou.
No fim, roda o buscar_leads.py e a página e o CSV saem atualizados em saida/.
"""

import argparse
import base64
import datetime as dt
import json
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import buscar_leads as bl

PASTA = Path(__file__).resolve().parent
ARQ_COLETA = PASTA / "coleta" / "porto-alegre.jsonl"
ARQ_BUSCAS = PASTA / "coleta" / "buscas_feitas.json"
PERFIL_CHROME = PASTA / ".chrome-coleta"
PORTA = 9223

BAIRROS_POA = [
    "Centro Histórico", "Cidade Baixa", "Bom Fim", "Farroupilha", "Independência", "Floresta", "Moinhos de Vento",
    "Rio Branco", "Mont'Serrat", "Auxiliadora", "Bela Vista", "Petrópolis", "Boa Vista", "Três Figueiras",
    "Chácara das Pedras", "Jardim Europa", "Higienópolis", "Passo d'Areia", "São João", "Navegantes", "Humaitá",
    "Jardim Lindóia", "Jardim São Pedro", "Vila Ipiranga", "Cristo Redentor", "Jardim Itu", "Sarandi",
    "Rubem Berta", "Passo das Pedras", "Protásio Alves", "Jardim Carvalho", "Partenon", "Santo Antônio",
    "Jardim Botânico", "Santana", "Azenha", "Menino Deus", "Praia de Belas", "Medianeira", "Glória",
    "Teresópolis", "Nonoai", "Cristal", "Camaquã", "Cavalhada", "Tristeza", "Vila Assunção", "Ipanema",
    "Vila Nova", "Hípica", "Belém Novo", "Restinga", "Lomba do Pinheiro", "Agronomia", "Bom Jesus", "Vila Jardim",
]
# Cidade vizinha: (cidade, bairros onde vale pesquisar além da cidade inteira)
CIDADES_VIZINHAS = [
    ("Canoas", ["Centro", "Niterói", "Marechal Rondon", "Igara", "Harmonia", "Nossa Senhora das Graças"]),
    ("Novo Hamburgo", ["Centro", "Hamburgo Velho", "Canudos"]),
    ("São Leopoldo", ["Centro", "Morro do Espelho", "Scharlau"]),
    ("Gravataí", ["Centro", "Parque dos Anjos"]),
    ("Viamão", ["Centro"]),
    ("Cachoeirinha", ["Centro"]),
    ("Alvorada", ["Centro"]),
    ("Esteio", ["Centro"]),
    ("Sapucaia do Sul", ["Centro"]),
    ("Guaíba", ["Centro"]),
]
CIDADES_DA_REGIAO = {"Porto Alegre"} | {cidade for cidade, _ in CIDADES_VIZINHAS}

# Lê a lista de resultados. Mesmo filtro do coleta/extrator.js, mas devolve tudo e o Python decide.
LISTA_JS = r"""(async () => {
  const S = ms => new Promise(r => setTimeout(r, ms)), B = () => document.body ? document.body.innerText : "";
  for (let i = 0; i < 40 && !document.querySelector('[role=feed] [role=article]') && !/não encontrou|tráfego incomum|unusual traffic/i.test(B()); i++) await S(400);
  await S(700);
  if (/tráfego incomum|unusual traffic|não sou um robô|recaptcha/i.test(B()) || location.pathname.startsWith('/sorry')) return {bloqueio: true, itens: []};
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

# Lê a ficha de uma clínica: o endereço completo traz o bairro.
FICHA_JS = r"""(async () => {
  const S = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 30 && !document.querySelector('[data-item-id="address"]'); i++) await S(400);
  await S(500);
  const rotulo = sel => document.querySelector(sel)?.getAttribute('aria-label') || '';
  const texto = document.querySelector('[role=main]')?.innerText || '';
  return {
    endereco: rotulo('[data-item-id="address"]').replace(/^Endereço:\s*/, '').trim(),
    telefone: rotulo('[data-item-id^="phone"]').replace(/^Telefone:\s*/, '').trim(),
    site: document.querySelector('a[data-item-id="authority"]')?.href || '',
    fechado: /Fechado permanentemente|Fechado temporariamente/.test(texto),
    bloqueio: /tráfego incomum|unusual traffic|recaptcha/i.test(document.body.innerText),
  };
})()"""


class Bloqueio(Exception):
    pass


def manter_acordado(ligar):
    """Impede o Windows de dormir no meio da coleta: se o PC dorme, a conexão com o Chrome morre."""
    if sys.platform == "win32":
        import ctypes
        continuo, sistema = 0x80000000, 0x00000001  # ES_CONTINUOUS, ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(continuo | (sistema if ligar else 0))


def fechar_chrome_preso():
    """Fecha o Chrome que ficou preso com o perfil da coleta (depois de travar ou de o PC dormir).
    Só mexe nos processos deste perfil: o Chrome normal do usuário não é tocado."""
    if sys.platform == "win32":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | "
                        "Where-Object { $_.CommandLine -like '*chrome-coleta*' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                       capture_output=True)
    elif shutil.which("pkill"):  # Linux (a coleta na nuvem)
        subprocess.run(["pkill", "-f", "chrome-coleta"], capture_output=True)


class Chrome:
    """Abre um Chrome com perfil próprio e conversa com ele pelo protocolo de depuração (WebSocket)."""

    def __init__(self):
        exe = os.environ.get("CHROME") or next(
            (p for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                         r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                         os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]
             if Path(p).exists()), None) or next(
            (shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(n)), None)
        if not exe:
            sys.exit("Não achei o Chrome. Instale o Google Chrome ou ponha o caminho na variável CHROME.")
        fechar_chrome_preso()
        # Linux (a coleta na nuvem, com janela virtual): o Chrome só abre sem o sandbox e com /dev/shm pequeno
        linux = ["--no-sandbox", "--disable-dev-shm-usage"] if sys.platform.startswith("linux") else []
        # As 3 opções de "background" impedem o Chrome de desacelerar a janela quando ela fica atrás de outras
        self.processo = subprocess.Popen([
            exe, f"--remote-debugging-port={PORTA}", f"--user-data-dir={PERFIL_CHROME}", "--no-first-run",
            "--no-default-browser-check", "--lang=pt-BR", "--window-size=1280,900", *linux,
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", "about:blank"],
            **({"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if sys.platform.startswith("linux") else {}))
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORTA}/json", timeout=2) as r:
                    alvo = next(t for t in json.load(r) if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(0.3)
        else:
            self.processo.terminate()
            raise ConnectionError("o Chrome abriu, mas não respondeu")
        self._conectar(alvo["webSocketDebuggerUrl"])
        self.seq = 0

    def _conectar(self, url):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=90)
        chave = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {chave}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resposta = b""
        while b"\r\n\r\n" not in resposta:
            resposta += self.sock.recv(4096)
        cabecalho, self.buf = resposta.split(b"\r\n\r\n", 1)
        if b" 101 " not in cabecalho.split(b"\r\n")[0]:
            sys.exit("O Chrome recusou a conexão de depuração.")

    def _ler(self, n):
        while len(self.buf) < n:
            dados = self.sock.recv(65536)
            if not dados:
                raise ConnectionError("o Chrome fechou")
            self.buf += dados
        parte, self.buf = self.buf[:n], self.buf[n:]
        return parte

    def _enviar(self, texto):
        dados = texto.encode()
        n = len(dados)
        cab = bytearray([0x81])
        if n < 126:
            cab.append(0x80 | n)
        elif n < 65536:
            cab += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            cab += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mascara = os.urandom(4)
        self.sock.sendall(bytes(cab) + mascara + bytes(b ^ mascara[i % 4] for i, b in enumerate(dados)))

    def _receber(self):
        partes = []
        while True:
            b1, b2 = self._ler(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._ler(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._ler(8))[0]
            dados = self._ler(n)
            op = b1 & 0x0F
            if op == 0x8:
                raise ConnectionError("o Chrome fechou")
            if op >= 0x8:  # ping/pong: nada a fazer
                continue
            partes.append(dados)
            if b1 & 0x80:
                return b"".join(partes).decode()

    def comando(self, metodo, **params):
        self.seq += 1
        self._enviar(json.dumps({"id": self.seq, "method": metodo, "params": params}))
        while True:
            msg = json.loads(self._receber())
            if msg.get("id") == self.seq:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message"))
                return msg.get("result", {})

    def abrir_e_ler(self, url, js):
        self.comando("Page.navigate", url=url)
        time.sleep(1.5)
        for tentativa in range(4):
            try:
                r = self.comando("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
            except RuntimeError:  # a página ainda estava trocando
                time.sleep(1)
                continue
            if "exceptionDetails" not in r:
                return r["result"].get("value") or {}
            time.sleep(1)
        return {}

    def ler(self, url, js):
        """abrir_e_ler com uma segunda chance: se o Chrome caiu, abre de novo e repete a página."""
        try:
            return self.abrir_e_ler(url, js)
        except (ConnectionError, OSError) as e:
            print(f"   O Chrome fechou ({e}). Abrindo de novo...")
            self.reiniciar()
            return self.abrir_e_ler(url, js)

    def reiniciar(self):
        self.fechar()
        self.__init__()

    def fechar(self):
        try:
            self.comando("Browser.close")
        except (OSError, RuntimeError, ConnectionError):
            self.processo.terminate()
        time.sleep(2)
        fechar_chrome_preso()  # processos auxiliares que o Chrome às vezes deixa para trás


def bairro_e_cidade(endereco):
    """'Av. X, 10 - Sl 2 - Cavalhada, Porto Alegre - RS, 91740-001' → ('Cavalhada', 'Porto Alegre')."""
    m = re.search(r" - ([^,]+), ([^,]+?) - [A-Z]{2}\b", endereco)
    if m:
        return m.group(1).split(" - ")[-1].strip(), m.group(2).strip()
    m = re.search(r", ([^,]+?) - [A-Z]{2}\b", endereco)
    return None, (m.group(1).strip() if m else None)


def plano(nichos, onde):
    """Lista de (termo, bairro ou None, cidade) na ordem de prioridade: nicho por fora."""
    lugares = []
    if onde in ("poa", "tudo"):
        lugares += [(b, "Porto Alegre") for b in BAIRROS_POA]
    if onde in ("vizinhas", "tudo"):
        for cidade, bairros in CIDADES_VIZINHAS:
            lugares += [(None, cidade)] + [(b, cidade) for b in bairros]
    return [(termo, bairro, cidade) for termo in nichos for bairro, cidade in lugares]


def chave_busca(termo, bairro, cidade):
    return f"{termo}|{bairro or ''}|{cidade}"


def url_busca(termo, bairro, cidade):
    q = " ".join(p for p in (termo, bairro, cidade, "RS") if p)
    return f"https://www.google.com/maps/search/{urllib.parse.quote_plus(q)}?hl=pt-BR"


def ler_conhecidos(arq):
    if not arq.exists():
        return set()
    return {json.loads(l)["id"] for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()}


def pesquisar(chrome, termo, bairro, cidade, conhecidos):
    """Uma pesquisa: lê a lista e abre a ficha de cada lead novo. Devolve (lista, novos)."""
    lista = chrome.ler(url_busca(termo, bairro, cidade), LISTA_JS)
    if lista.get("bloqueio"):
        raise Bloqueio
    novos = []
    for item in lista.get("itens", []):
        if item["id"] in conhecidos or item["fechado"] or not item["telefone"]:
            continue
        if bl.situacao_site(item["site"]) is None:  # tem site próprio
            continue
        ficha = chrome.ler(item["link"], FICHA_JS)
        if ficha.get("bloqueio"):
            raise Bloqueio
        time.sleep(random.uniform(1.5, 3))
        conhecidos.add(item["id"])
        if ficha.get("fechado") or (ficha.get("site") and bl.situacao_site(ficha["site"]) is None):
            continue
        endereco = ficha.get("endereco") or item["endereco"]
        b, c = bairro_e_cidade(endereco)
        if c and c not in CIDADES_DA_REGIAO:  # ex.: o bairro Camaquã de POA traz clínicas da cidade de Camaquã
            continue
        novos.append({
            "id": item["id"], "nome": item["nome"], "nota": item["nota"], "avaliacoes": item["avaliacoes"],
            "categoria": item["categoria"], "endereco": endereco, "telefone": ficha.get("telefone") or item["telefone"],
            "site": item["site"], "status": "OPERATIONAL", "termo": termo,
            "cidade": c or cidade, "bairro": b or bairro, "link": item["link"],
        })
    return lista, novos


def coletar(nichos, onde, max_buscas, arq):
    feitas = bl.ler_json(ARQ_BUSCAS, {})
    pendentes = [p for p in plano(nichos, onde) if chave_busca(*p) not in feitas]
    if not pendentes:
        print("Todas as pesquisas desses nichos e lugares já foram feitas. Escolha outro nicho ou lugar.")
        return 0
    pendentes = pendentes[:max_buscas]
    conhecidos = ler_conhecidos(arq)
    print(f"{len(pendentes)} pesquisas nesta rodada. Uma janela do Chrome vai abrir: deixe-a aberta até o fim.\n")
    manter_acordado(True)
    chrome, novos_total = None, 0
    try:
        chrome = Chrome()
        for n, (termo, bairro, cidade) in enumerate(pendentes, 1):
            if n % 30 == 0:  # Chrome novo de tempos em tempos: aberto por muito tempo, ele trava
                chrome.reiniciar()
            lista, novos = pesquisar(chrome, termo, bairro, cidade, conhecidos)
            with open(arq, "a", encoding="utf-8") as fh:
                for lead in novos:
                    fh.write(json.dumps(lead, ensure_ascii=False) + "\n")
            feitas[chave_busca(termo, bairro, cidade)] = {
                "data": dt.date.today().isoformat(), "resultados": len(lista.get("itens", [])), "novos": len(novos)}
            ARQ_BUSCAS.write_text(json.dumps(feitas, ensure_ascii=False, indent=0), encoding="utf-8")
            novos_total += len(novos)
            onde_txt = f"{bairro}, {cidade}" if bairro else cidade
            print(f"[{n}/{len(pendentes)}] {termo} · {onde_txt}: {len(lista.get('itens', []))} resultados, {len(novos)} novos")
            time.sleep(random.uniform(4, 8))
    except Bloqueio:
        print("\nO Google pediu verificação de robô. Parei aqui, sem tentar contornar.")
        print("Espere algumas horas antes de rodar de novo. O que já foi coletado está salvo.")
    except (ConnectionError, OSError) as e:
        print(f"\nA conexão com o Chrome caiu ({e}). O que já foi coletado está salvo: rode de novo para continuar.")
    finally:
        if chrome:
            chrome.fechar()
        manter_acordado(False)
    print(f"\n{novos_total} leads novos nesta rodada.")
    return novos_total


def completar(arq):
    """Abre a ficha dos leads sem bairro (os coletados antes deste script) e completa endereço e bairro.
    Se a ficha mostrar um site próprio, o lead sai da lista na próxima geração."""
    linhas = [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()]
    faltam = [d for d in linhas if not d.get("bairro") and not d.get("completo")]
    if not faltam:
        print("Todos os leads já têm bairro.")
        return 0
    print(f"{len(faltam)} leads sem bairro. Uma janela do Chrome vai abrir: deixe-a aberta até o fim.\n")
    manter_acordado(True)
    chrome, feitos = None, 0
    try:
        chrome = Chrome()
        for n, d in enumerate(faltam, 1):
            if n % 60 == 0:
                chrome.reiniciar()
            link = d.get("link") or f"https://www.google.com/maps/place/data=!4m2!3m1!1s{d['id']}?hl=pt-BR"
            ficha = chrome.ler(link, FICHA_JS)
            if ficha.get("bloqueio"):
                raise Bloqueio
            if ficha.get("endereco"):
                bairro, cidade = bairro_e_cidade(ficha["endereco"])
                d.update(endereco=ficha["endereco"], bairro=bairro, link=link, completo=True)
                d["cidade"] = cidade or d["cidade"]
                if ficha.get("site"):
                    d["site"] = ficha["site"]
                if ficha.get("fechado"):
                    d["status"] = "CLOSED_TEMPORARILY"
                feitos += 1
            print(f"[{n}/{len(faltam)}] {d['nome'][:50]}: {d.get('bairro') or 'sem bairro no endereço'}")
            time.sleep(random.uniform(2, 4))
    except Bloqueio:
        print("\nO Google pediu verificação de robô. Parei aqui, sem tentar contornar.")
    except (ConnectionError, OSError) as e:
        print(f"\nA conexão com o Chrome caiu ({e}). O que já foi completado está salvo.")
    finally:
        if chrome:
            chrome.fechar()
        manter_acordado(False)
        arq.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in linhas), encoding="utf-8")
    print(f"\n{feitos} leads completados.")
    return feitos


def escolher(pergunta, padrao):
    resposta = input(f"{pergunta} [{padrao}]: ").strip()
    return resposta or padrao


def menu():
    feitas = bl.ler_json(ARQ_BUSCAS, {})
    print("Buscar mais leads · Aureon\n\nNichos:")
    for i, (termo, _, _) in enumerate(bl.TERMOS, 1):
        n = sum(1 for k in feitas if k.startswith(termo + "|"))
        print(f"  {i:2}. {termo}" + (f"  ({n} pesquisas já feitas)" if n else ""))
    nums = escolher("\nNúmeros dos nichos, separados por vírgula", "1,2,3")
    nichos = [bl.TERMOS[int(x) - 1][0] for x in re.findall(r"\d+", nums) if 0 < int(x) <= len(bl.TERMOS)]
    print("\nOnde buscar:\n  1. Bairros de Porto Alegre\n  2. Cidades vizinhas\n  3. Os dois")
    onde = {"1": "poa", "2": "vizinhas", "3": "tudo"}.get(escolher("Escolha", "1"), "poa")
    max_buscas = int(escolher("Quantas pesquisas no máximo nesta rodada (cada uma leva uns 20 segundos)", "40"))
    return nichos, onde, max_buscas


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nicho", action="append", help="termo do TERMOS do buscar_leads.py; repita para vários")
    ap.add_argument("--onde", choices=["poa", "vizinhas", "tudo"], default="poa")
    ap.add_argument("--max-buscas", type=int, default=40)
    ap.add_argument("--arquivo", default=str(ARQ_COLETA))
    ap.add_argument("--completar", action="store_true", help="completa o bairro dos leads antigos abrindo a ficha")
    args = ap.parse_args()
    arq = Path(args.arquivo)
    if args.completar:
        if completar(arq):
            subprocess.run([sys.executable, str(PASTA / "buscar_leads.py"), "--coletados", str(arq), "--repetir"], check=False)
        return
    if args.nicho:
        desconhecidos = set(args.nicho) - {t for t, _, _ in bl.TERMOS}
        if desconhecidos:
            sys.exit(f"Nicho fora do TERMOS do buscar_leads.py: {', '.join(desconhecidos)}")
        nichos, onde, max_buscas = args.nicho, args.onde, args.max_buscas
    else:
        nichos, onde, max_buscas = menu()
    if coletar(nichos, onde, max_buscas, arq):
        print("\nGerando a lista atualizada...")
        subprocess.run([sys.executable, str(PASTA / "buscar_leads.py"), "--coletados", str(arq), "--repetir"], check=False)
        print("\nNo CRM, vá em Importar e suba o CSV novo: quem já estava lá não se repete.")


if __name__ == "__main__":
    main()
