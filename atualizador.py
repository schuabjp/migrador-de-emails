"""
Atualização automática pelo GitHub Releases.

1. buscar(): consulta a última versão publicada; se for mais nova, devolve os links.
2. baixar(): grava o MigradorEmails.exe novo na mesma pasta, como
   MigradorEmails.novo.exe, e confere o SHA-256 publicado junto.
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


def caminho_novo():
    return os.path.join(os.path.dirname(exe_atual()), "MigradorEmails.novo.exe")


def caminho_info():
    return os.path.join(os.path.dirname(exe_atual()), "MigradorEmails.novo.json")


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


def baixar(info, progresso=None):
    """Baixa para MigradorEmails.novo.exe e confere o SHA-256. PermissionError
    significa que a pasta do programa não aceita gravação (ex.: Arquivos de Programas)."""
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


def limpar_sobras(versao_atual=versao.VERSAO):
    """Depois de atualizar, apaga o MigradorEmails.novo.exe que sobrou. Em segundo
    plano e com novas tentativas: ele pode estar terminando de fechar."""
    if not empacotado() or pendente(versao_atual):
        return

    def tarefa():
        for caminho in (caminho_info(), caminho_novo(), caminho_novo() + ".part",
                        exe_atual() + ".instalando"):
            for _ in range(20):
                try:
                    os.remove(caminho)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.5)
    threading.Thread(target=tarefa, daemon=True).start()


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
    for _ in range(120):  # até ~1 min esperando o app antigo fechar e soltar o arquivo
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
        _avisar("Não foi possível concluir a atualização porque o Migrador ainda está "
                "aberto.\nFeche todas as janelas dele e abra de novo: a atualização "
                "baixada será oferecida outra vez.")
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
