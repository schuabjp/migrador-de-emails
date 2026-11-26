"""
Atualização automática pelo GitHub Releases.

1. buscar(): consulta a última versão publicada; se for mais nova, devolve os links.
2. baixar(): grava o MigradorEmails.exe novo numa pasta interna
   (%LOCALAPPDATA%\\MigradorEmails\\atualizacao) e confere o SHA-256 publicado junto.
   A pasta do programa só vê o próprio MigradorEmails.exe ser trocado.
3. instalar(): abre o novo com --atualizar; o app atual fecha em seguida.
4. concluir(): já no novo, espera o antigo liberar o arquivo, copia a si mesmo por
   cima dele e abre a versão instalada, que apaga a sobra (limpar_sobras()).

O Windows não deixa sobrescrever nem renomear um .exe do PyInstaller em uso, por
isso a troca é feita pelo próprio executável novo. Também não se usa nenhuma API
de processo do Windows (OpenProcess etc.): antivírus como o Norton consideram isso
suspeito e põem o executável em quarentena. A espera é só tentar de novo a troca.
"""
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import versao

ARQUIVO = "MigradorEmails.exe"
PAGINA = "https://github.com/schuabjp/migrador-de-emails/releases"
CABECALHOS = {"User-Agent": "MigradorEmails", "Accept": "application/vnd.github+json"}
LOG = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MigradorEmails",
                   "atualizacao.log")


def registrar(texto):
    """Sem console no .exe: o que acontece na atualização vai para um arquivo."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {texto}\n")
    except OSError:
        pass


def empacotado():
    """Só o .exe se atualiza; rodando pelo Python não há o que trocar."""
    return getattr(sys, "frozen", False)


def exe_atual():
    return os.path.abspath(sys.executable)


def pasta_download():
    """Pasta interna do download: fora da pasta do programa (que pode estar no OneDrive,
    na Área de Trabalho...), para não aparecer um segundo .exe para quem usa."""
    base = os.environ.get("LOCALAPPDATA") or os.path.dirname(exe_atual())
    return os.path.join(base, "MigradorEmails", "atualizacao")


def caminho_novo():
    return os.path.join(pasta_download(), "MigradorEmails.novo.exe")


def caminho_info():
    return os.path.join(pasta_download(), "MigradorEmails.novo.json")


def _sobras_antigas():
    """Onde as versões até a 1.1.0 baixavam a atualização: ao lado do próprio .exe."""
    pasta = os.path.dirname(exe_atual())
    return [os.path.join(pasta, n) for n in ("MigradorEmails.novo.json", "MigradorEmails.novo.exe",
                                             "MigradorEmails.novo.exe.part")]


def versao_tupla(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def _abrir(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=CABECALHOS),
                                  timeout=timeout)


def _iniciar(args):
    """Abre outro .exe do PyInstaller sem herdar a pasta temporária deste."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(args, env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP)


def _sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _completa(rel):
    """Publicada (não rascunho) e com o .exe e o .sha256 já enviados."""
    nomes = {a["name"] for a in rel.get("assets", [])}
    return not rel.get("draft") and ARQUIVO in nomes and ARQUIVO + ".sha256" in nomes


def buscar(versao_atual=versao.VERSAO, timeout=15):
    """A versão publicada mais nova, se for mais nova que a atual (dict); senão None.
    Pré-versões (Alpha/Beta) contam: o app inteiro ainda está nessa fase."""
    try:
        with _abrir(versao.URL_ATUALIZACAO, timeout) as r:
            dados = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # /releases/latest responde 404 quando nada foi publicado ainda
            return None
        raise
    lista = dados if isinstance(dados, list) else [dados]
    candidatas = [rel for rel in lista if _completa(rel)]  # incompleta: ainda subindo
    if not candidatas:
        return None
    rel = max(candidatas, key=lambda r: versao_tupla(r.get("tag_name", "")))
    tag = rel.get("tag_name", "")
    if versao_tupla(tag) <= versao_tupla(versao_atual):
        return None
    arquivos = {a["name"]: a for a in rel.get("assets", [])}
    return {"versao": tag.lstrip("vV"), "notas": (rel.get("body") or "").strip(),
            "pagina": rel.get("html_url", ""), "pre": bool(rel.get("prerelease")),
            "url": arquivos[ARQUIVO]["browser_download_url"],
            "tamanho": arquivos[ARQUIVO].get("size", 0),
            "url_sha": arquivos[ARQUIVO + ".sha256"]["browser_download_url"]}


def explicar(erro):
    """Motivo de uma consulta que falhou, em palavras para quem usa o programa."""
    if isinstance(erro, urllib.error.HTTPError):
        if erro.code in (403, 429):
            return ("O GitHub limitou as consultas deste computador por enquanto. "
                    "Tente de novo daqui a alguns minutos.")
        if erro.code >= 500:
            return "O GitHub está com problemas agora. Tente de novo mais tarde."
        return f"O GitHub respondeu com o código {erro.code}. Tente de novo mais tarde."
    if isinstance(erro, (urllib.error.URLError, OSError, http.client.HTTPException)):
        return "Sem conexão com o GitHub. Confira a internet e tente de novo."
    return f"Erro inesperado: {erro}"


def _pasta_gravavel(pasta):
    """Levanta PermissionError se não der para criar arquivos na pasta."""
    teste = os.path.join(pasta, f".migrador-teste-{os.getpid()}")
    with open(teste, "wb"):
        pass
    os.remove(teste)


def baixar(info, progresso=None):
    """Baixa para a pasta interna e confere o SHA-256. PermissionError significa que a
    pasta do programa não aceita gravação (ex.: Arquivos de Programas): a troca do .exe
    não daria certo, então nem vale baixar."""
    _pasta_gravavel(os.path.dirname(exe_atual()))
    os.makedirs(pasta_download(), exist_ok=True)
    with _abrir(info["url_sha"], 30) as r:
        esperado = r.read().decode("ascii", "replace").split()[0].lower()
    destino = caminho_novo()
    parcial = destino + ".part"
    for tentativa in range(1, 4):  # conexão que cai no meio (rede, antivírus) tenta de novo
        try:
            h, feito = hashlib.sha256(), 0
            with _abrir(info["url"], 60) as r, open(parcial, "wb") as f:
                total = int(r.headers.get("Content-Length") or info.get("tamanho") or 0)
                while bloco := r.read(256 * 1024):
                    f.write(bloco)
                    h.update(bloco)
                    feito += len(bloco)
                    if progresso:
                        progresso(feito, total)
            break
        except PermissionError:
            raise  # pasta sem permissão de gravação: tentar de novo não resolve
        except (OSError, http.client.HTTPException) as e:
            registrar(f"download interrompido (tentativa {tentativa} de 3): {e!r}")
            if tentativa == 3:
                raise
            time.sleep(2 * tentativa)
    if h.hexdigest() != esperado:
        os.remove(parcial)
        raise RuntimeError("o arquivo baixado não confere com o publicado (SHA-256); "
                           "tente de novo")
    os.replace(parcial, destino)
    with open(caminho_info(), "w", encoding="utf-8") as f:
        json.dump({"versao": info["versao"], "sha256": esperado}, f)
    registrar(f"baixada a versão {info['versao']} em {destino}")
    return {"versao": info["versao"], "sha256": esperado}


def pendente(versao_atual=versao.VERSAO):
    """Atualização já baixada e íntegra, esperando ser instalada (dict) ou None."""
    try:
        with open(caminho_info(), encoding="utf-8") as f:
            dados = json.load(f)
        if versao_tupla(dados["versao"]) <= versao_tupla(versao_atual):
            return None
        if _sha256(caminho_novo()) != dados["sha256"]:
            return None
        return dados
    except (OSError, ValueError, KeyError):
        return None


def limpar_sobras(versao_atual=versao.VERSAO, espera=120):
    """Depois de atualizar, apaga a cópia baixada que sobrou. Em segundo plano e com
    paciência: o .exe novo pode estar terminando de fechar, e antivírus ou OneDrive
    podem segurar o arquivo por um tempo. O que não sair agora sai na próxima abertura."""
    if not empacotado():
        return
    caminhos = _sobras_antigas() + [exe_atual() + ".instalando"]
    if not pendente(versao_atual):  # uma versão baixada esperando instalação fica
        caminhos += [caminho_info(), caminho_novo(), caminho_novo() + ".part"]

    def tarefa():
        restantes = [c for c in caminhos if os.path.exists(c)]
        fim = time.time() + espera
        while restantes:
            for c in list(restantes):
                try:
                    os.remove(c)
                    restantes.remove(c)
                except FileNotFoundError:
                    restantes.remove(c)
                except OSError:
                    pass
            if not restantes or time.time() > fim:
                break
            time.sleep(1)
        for c in restantes:
            registrar(f"não foi possível apagar {c} agora (em uso); nova tentativa ao abrir")
    threading.Thread(target=tarefa, daemon=True).start()


_instancia = None  # (arquivo aberto e travado, caminho) desta janela


def _pasta_instancias():
    return os.path.join(os.path.dirname(LOG), "instancias")


def _prefixo_instancia():
    """Só contam as janelas abertas a partir do mesmo .exe (outra cópia em outra pasta,
    não atrapalha a troca deste)."""
    return hashlib.sha256(exe_atual().lower().encode()).hexdigest()[:12]


def registrar_instancia():
    """Marca esta janela como aberta: um arquivo travado enquanto o processo existir
    (o Windows solta a trava sozinho quando ele fecha, mesmo se travar ou for morto)."""
    global _instancia
    if not empacotado() or _instancia:
        return
    import msvcrt
    try:
        os.makedirs(_pasta_instancias(), exist_ok=True)
        caminho = os.path.join(_pasta_instancias(), f"{_prefixo_instancia()}-{os.getpid()}.lock")
        f = open(caminho, "w+b")
        f.write(b"1")
        f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        _instancia = (f, caminho)
    except OSError as e:
        registrar(f"não foi possível registrar esta janela: {e!r}")


def outras_instancias():
    """Quantas outras janelas do Migrador estão abertas a partir deste mesmo .exe."""
    if not empacotado():
        return 0
    import glob
    import msvcrt
    abertas = 0
    for caminho in glob.glob(os.path.join(_pasta_instancias(), f"{_prefixo_instancia()}-*.lock")):
        if _instancia and os.path.normcase(caminho) == os.path.normcase(_instancia[1]):
            continue
        try:
            with open(caminho, "r+b") as f:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # consegui travar: já fechou
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            os.remove(caminho)  # sobra de uma janela que já fechou
        except FileNotFoundError:
            pass
        except OSError:
            abertas += 1
    return abertas


def instalar(reabrir=True):
    """Passa a troca para o executável novo; quem chama deve fechar o app logo depois."""
    _iniciar([caminho_novo(), "--atualizar", exe_atual()] + (["--reabrir"] if reabrir else []))
    registrar(f"instalação iniciada (reabrir={reabrir})")


def _avisar(texto):
    """Caixa de aviso nativa do Windows (aqui ainda não há interface carregada)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texto, "Migrador de E-mails", 0x30)  # ícone de aviso
    except Exception:
        pass


def concluir(argv):
    """Modo --atualizar, rodando no executável novo. Devolve True se era esse o modo."""
    if len(argv) < 3 or argv[1] != "--atualizar":
        return False
    destino, reabrir = argv[2], "--reabrir" in argv[3:]
    temporario = destino + ".instalando"
    try:
        shutil.copyfile(exe_atual(), temporario)
    except OSError as e:
        registrar(f"falha ao preparar a instalação: {e!r}")
        _avisar(f"Não foi possível instalar a atualização:\n{e}")
        return True
    # até ~3 min esperando o arquivo ser solto: o app antigo terminando de fechar, outra
    # janela do Migrador aberta, ou antivírus/OneDrive inspecionando o .exe
    for _ in range(360):
        try:
            os.replace(temporario, destino)  # troca inteira de uma vez: nunca fica pela metade
            break
        except OSError as e:
            erro = e
            time.sleep(0.5)
    else:
        registrar(f"falha ao instalar em {destino}: {erro!r}")
        try:
            os.remove(temporario)
        except OSError:
            pass
        _avisar("Não foi possível concluir a atualização: o MigradorEmails.exe continua em "
                "uso (outra janela do Migrador aberta, ou o antivírus examinando o arquivo).\n\n"
                "Feche todas as janelas do Migrador e abra de novo: a versão baixada é "
                "instalada quando você fechar o programa.")
        return True
    registrar(f"versão instalada em {destino}")
    if reabrir:
        for _ in range(20):  # o antivírus pode estar inspecionando o arquivo recém-gravado
            try:
                _iniciar([destino])
                break
            except OSError:
                time.sleep(0.5)
    return True
