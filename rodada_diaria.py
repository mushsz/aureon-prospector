"""Rodada diária dos leads de equipamentos e de sites: busca o que há de novo, gera as duas páginas e publica.

Roda sozinha todo dia às 7h pela tarefa "Aureon - Leads diarios" do Windows (agendar_rodada.ps1),
ou com dois cliques em "Atualizar leads agora.bat". A tarefa "Aureon - Vigia de leads" (agendar_vigia.ps1)
chama de 2 em 2 horas com --so-se-precisar: só trabalha se alguma lista estiver acabando.
Uso: python rodada_diaria.py [--max 15] [--max-reposicao 60] [--limite 50] [--so-se-precisar] [--sem-publicar]

Ordem: licitações abertas (PNCP), hospitais do CNES (uma vez por semana), Google Maps de equipamentos e de sites
(--max pesquisas cada, uma lista depois da outra: as duas usam a mesma janela do Chrome), as duas páginas e o deploy.
Reposição: se uma lista tem --limite leads ou menos ainda sem ver (repor.py), a coleta dela faz --max-reposicao pesquisas.
Uma etapa que falha, ou fica muda por 15 minutos, não derruba as outras; tudo vai para rodada.log. Se o Google pedir
verificação de robô, a coleta para ali, a página sai com o que já tinha e a próxima rodada continua do mesmo ponto.
"""

import argparse
import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import repor

PASTA = Path(__file__).resolve().parent
LOG = PASTA / "rodada.log"
TRAVA = PASTA / "rodada.trava"
ESTADO = PASTA / "reposicao.json"   # quando cada lista foi reposta pela última vez
TRAVA_HORAS = 8   # uma trava mais velha que isso é de uma rodada que morreu
INTERVALO_REPOSICAO_H = 6   # o vigia não repõe a mesma lista duas vezes em menos tempo que isso (coleta que não acha nada novo)


def registrar(texto):
    linha = f"{dt.datetime.now():%d/%m %H:%M:%S}  {texto}"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(linha + "\n")
    try:
        print(linha, flush=True)
    except Exception:
        pass   # a tela pode estar em outra codificação ou fechada (tarefa do Windows): o que vale é o arquivo


def matar(proc):
    """Encerra o passo e os filhos dele (a coleta deixa o Chrome aberto)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def etapa(nome, comando, pasta, limite_min, silencio_min=15):
    """Roda um passo, copia a saída para a tela e para o log. Devolve True se terminou bem.
    Para o passo se passar de limite_min ou se ficar silencio_min sem escrever nada (a conferência não depende de chegar uma linha nova)."""
    registrar(f"== {nome}")
    inicio = ultimo = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    try:
        proc = subprocess.Popen(comando, cwd=pasta, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
    except OSError as e:
        registrar(f"   não consegui rodar: {e}")
        return False
    linhas = queue.Queue()

    def ler():
        for linha in proc.stdout:
            linhas.put(linha)
        linhas.put(None)

    threading.Thread(target=ler, daemon=True).start()
    while True:
        try:
            linha = linhas.get(timeout=5)
        except queue.Empty:
            linha = ""
        if linha is None:
            break
        if linha.strip():
            registrar("   " + linha.rstrip())
            ultimo = time.time()
        agora = time.time()
        if agora - inicio > limite_min * 60:
            matar(proc)
            registrar(f"   passou de {limite_min} min: parei esta etapa")
            return False
        if agora - ultimo > silencio_min * 60:
            matar(proc)
            registrar(f"   {silencio_min} min sem escrever nada: parei esta etapa")
            return False
    ok = proc.wait() == 0
    registrar(f"   {'ok' if ok else 'terminou com erro'} em {round((time.time() - inicio) / 60, 1)} min")
    return ok


def processo_vivo(pid):
    if os.name == "nt":
        saida = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True).stdout
        return f'"{pid}"' in saida
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def adquirir_trava():
    """True se esta rodada ficou com a trava. Cria o arquivo de uma vez só (duas tarefas que começam juntas, como a das 7h e o vigia
    quando o PC liga às 9h, não passam as duas) e descarta a trava de uma rodada que morreu: o processo não existe mais,
    ou ela passou de TRAVA_HORAS."""
    for _ in range(2):
        try:
            with open(TRAVA, "x") as fh:
                fh.write(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                idade, pid = time.time() - TRAVA.stat().st_mtime, int(TRAVA.read_text().strip() or 0)
            except (OSError, ValueError):
                idade, pid = 0, 0   # recém-criada por outra rodada, que ainda não escreveu o número
            if idade > TRAVA_HORAS * 3600 or (pid and not processo_vivo(pid)):
                TRAVA.unlink(missing_ok=True)
                registrar("Tirei a trava de uma rodada que não existe mais.")
                continue
            return False
    return False


def ler_estado():
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def situacao_segura(site):
    """A fila de cada lista, ou None onde não deu para contar. Nada aqui derruba a rodada."""
    try:
        return repor.situacao(site)
    except Exception as e:
        registrar(f"Não consegui contar a fila ({type(e).__name__}: {e}). Sigo sem reposição.")
        return {"sites": None, "hosp": None}


def aparar_log(maximo=5000):
    linhas = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    if len(linhas) > maximo:
        LOG.write_text("\n".join(linhas[-maximo:]) + "\n", encoding="utf-8")


def site_id():
    """O id do site na Netlify, que o deploy de uma pasta fora de publicar/ precisa receber."""
    try:
        return json.loads((PASTA / "publicar" / ".netlify" / "state.json").read_text(encoding="utf-8")).get("siteId")
    except (OSError, ValueError):
        return None


def publicar():
    """Sobe publicar/ (as duas páginas, _redirects e _headers) e a função das marcas. Tenta 3 vezes."""
    cmd = os.environ.get("COMSPEC", "cmd.exe")
    if not (PASTA / "node_modules" / "@netlify" / "blobs").exists():
        etapa("Instalar a dependência da função das marcas (npm install)", [cmd, "/c", "npm", "install", "--no-audit", "--no-fund"], PASTA, 10)
    comando = [cmd, "/c", "npx", "-y", "netlify-cli@latest", "deploy", "--prod", "--no-build",
               "--dir", "publicar", "--functions", "netlify/functions"]
    if site_id():
        comando += ["--site", site_id()]
    for tentativa in (1, 2, 3):
        if etapa(f"Publicar na Netlify (tentativa {tentativa} de 3)", comando, PASTA, 15):
            return True
        if tentativa < 3:
            time.sleep(60)
    registrar("Não consegui publicar. As páginas novas estão em publicar/p/; rode 'Atualizar leads agora.bat' ou o deploy à mão.")
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=15, help="pesquisas no Google Maps por lista")
    ap.add_argument("--max-reposicao", type=int, default=60, help="pesquisas por lista quando ela está acabando")
    ap.add_argument("--limite", type=int, default=repor.LIMITE, help="quantos leads sem ver contam como 'acabando'")
    ap.add_argument("--so-se-precisar", action="store_true", help="só trabalha se alguma lista estiver acabando (o vigia usa)")
    ap.add_argument("--sem-publicar", action="store_true")
    ap.add_argument("--site", default=repor.SITE, help="endereço do site, de onde vêm as marcas")
    args = ap.parse_args()
    vigia = args.so_se_precisar

    # uma rodada por vez: a das 7h, a do vigia e a do botão não podem usar a janela do Chrome juntas
    if not adquirir_trava():
        registrar("Já tem uma rodada em andamento. Saí sem fazer nada.")
        return
    try:
        falta = situacao_segura(args.site)
        acabando = {lista: repor.precisa(n, args.limite) for lista, n in falta.items()}
        fila = ", ".join(f"{nome} {'?' if falta[lista] is None else falta[lista]}" for lista, nome in (("sites", "sites"), ("hosp", "equipamentos")))
        estado, agora = ler_estado(), time.time()
        if vigia:
            if all(n is None for n in falta.values()):
                registrar("Vigia: não consegui ler as marcas (o site está fora do ar ou a função das marcas ainda não foi publicada).")
                return
            # lista que acabou de ser reposta fica de folga: se a coleta não achou nada novo, abrir o Chrome de novo não adianta
            acabando = {lista: a and agora - estado.get(lista, 0) > INTERVALO_REPOSICAO_H * 3600 for lista, a in acabando.items()}
            if not any(acabando.values()):
                registrar(f"Vigia: fila com {fila}. Nada a repor.")
                return

        registrar(f"{'Reposição' if vigia else 'Rodada diária'} começou ({args.max} pesquisas por lista; fila com {fila})")
        py = sys.executable
        hosp, sites = PASTA / "hospitalar", PASTA / "sites"
        if not vigia:
            etapa("Licitações abertas (PNCP)", [py, "pncp.py"], hosp, 60)
            cnes = hosp / "dados" / "estabelecimentos.json"
            if not cnes.exists() or time.time() - cnes.stat().st_mtime > 6 * 86400:
                etapa("Hospitais e home cares (CNES)", [py, "cnes.py"], hosp, 30)
        for lista, nome, pasta, por_pesquisa in (("hosp", "clínicas para equipamentos", hosp, 2), ("sites", "empresas para sites", sites, 4)):
            if vigia and not acabando[lista]:
                continue
            n = args.max_reposicao if acabando[lista] else args.max
            if acabando[lista]:
                registrar(f"A fila de {nome.split(' para ')[1]} tem só {falta[lista]}: {n} pesquisas em vez de {args.max}.")
                estado[lista] = time.time()
                try:
                    ESTADO.write_text(json.dumps(estado), encoding="utf-8")
                except OSError:
                    pass
            etapa(f"Google Maps: {nome}", [py, "coletar_maps.py", "--max", str(n)], pasta, max(90 if lista == "hosp" else 150, n * por_pesquisa))
        paginas = [etapa("Página de equipamentos", [py, "gerar.py"], hosp, 10),
                   etapa("Página de sites", [py, "gerar.py"], sites, 10)]
        if args.sem_publicar:
            registrar("Sem publicar (--sem-publicar).")
        elif any(paginas):
            publicar()
        else:
            registrar("Nenhuma página foi gerada: não publiquei.")
        registrar(f"{'Reposição' if vigia else 'Rodada diária'} terminou")
    finally:
        TRAVA.unlink(missing_ok=True)
        aparar_log()


if __name__ == "__main__":
    main()
