#!/usr/bin/env python3
"""
Migração de e-mails sem MailStore.

Modos:
  imap2imap  -> copia de um servidor IMAP para outro
  export     -> exporta de um servidor IMAP para pastas com arquivos .eml
  import     -> importa uma árvore de pastas .eml para um servidor IMAP

Preserva: estrutura de pastas, flags (lido, respondido...) e data original.
Evita duplicados (compara Message-ID + Date + Subject), então pode ser
interrompido e executado de novo sem duplicar nada.

Exemplos:
  python migrar_email.py imap2imap \
      --src-host imap.antigo.com --src-user a@x.com --src-pass 'senha1' \
      --dst-host imap.novo.com   --dst-user a@x.com --dst-pass 'senha2'

  python migrar_email.py export --src-host imap.antigo.com \
      --src-user a@x.com --src-pass 'senha1' --dir ./backup

  python migrar_email.py import --dst-host imap.novo.com \
      --dst-user a@x.com --dst-pass 'senha2' --dir ./backup
"""
import argparse
import copy
import csv
import email
import getpass
import hashlib
import imaplib
import json
import os
import re
import smtplib
import socket
import sys
import time
import urllib.request
from email.header import decode_header, make_header
from email.message import EmailMessage

imaplib._MAXLINE = 100_000_000  # respostas grandes

LIST_RE = re.compile(r'\((?P<flags>.*?)\) (?P<delim>"[^"]*"|NIL) (?P<name>.*)')
UID_RE = re.compile(rb"UID (\d+)")


# ---------------------------------------------------------------- IMAP util
def conectar(host, user, pwd, port=993, ssl=True, tentativas=3):
    """Conecta com retentativas em falha de rede; senha errada falha direto."""
    ultimo = None
    for i in range(1, tentativas + 1):
        try:
            c = imaplib.IMAP4_SSL(host, port) if ssl else imaplib.IMAP4(host, port)
        except OSError as e:  # DNS, conexão recusada, timeout, SSL
            ultimo = e
            print(f"    ! conexão com {host} falhou ({e}); "
                  f"tentativa {i}/{tentativas}", file=sys.stderr)
            if i < tentativas:
                time.sleep(5 * i)
            continue
        try:
            c.login(user, pwd)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(f"login recusado em {host} para {user} "
                               f"(usuário/senha errados ou acesso bloqueado): {e}")
        return c
    raise RuntimeError(f"não foi possível conectar em {host}:{port} "
                       f"após {tentativas} tentativas: {ultimo}")


def q(nome):
    """Coloca o nome da pasta entre aspas para o comando IMAP."""
    return '"' + nome.replace("\\", "\\\\").replace('"', '\\"') + '"'


def listar_pastas(c):
    """Retorna [(nome_bruto, delimitador)] das pastas selecionáveis."""
    typ, dados = c.list()
    pastas = []
    for linha in dados:
        if not linha:
            continue
        if isinstance(linha, tuple):  # nome literal
            linha = linha[0] + b' "' + linha[1] + b'"'
        m = LIST_RE.match(linha.decode("utf-8", "replace"))
        if not m or "\\noselect" in m["flags"].lower():
            continue
        delim = m["delim"].strip('"') if m["delim"] != "NIL" else "/"
        nome = m["name"].strip().strip('"')
        pastas.append((nome, delim))
    return pastas


def chave_msg(raw_headers: bytes) -> str:
    msg = email.message_from_bytes(raw_headers)
    partes = [str(msg.get(h, "")).strip() for h in ("Message-ID", "Date", "Subject")]
    return hashlib.sha1("|".join(partes).encode("utf-8", "replace")).hexdigest()


def chaves_existentes(c, pasta):
    """Conjunto de chaves das mensagens que já existem na pasta de destino."""
    chaves = set()
    typ, _ = c.select(q(pasta), readonly=True)
    if typ != "OK":
        return chaves
    typ, d = c.uid("search", None, "ALL")
    uids = d[0].split()
    for i in range(0, len(uids), 500):
        lote = b",".join(uids[i:i + 500]).decode()
        typ, resp = c.uid(
            "fetch", lote,
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE SUBJECT)])")
        for item in resp:
            if isinstance(item, tuple):
                chaves.add(chave_msg(item[1]))
    return chaves


def garantir_pasta(c, nome):
    typ, _ = c.select(q(nome), readonly=True)
    if typ != "OK":
        c.create(q(nome))
        c.subscribe(q(nome))


def converter_nome(nome, delim_origem, delim_destino):
    if delim_origem == delim_destino:
        return nome
    return nome.replace(delim_origem, delim_destino)


def baixar_msgs(c, pasta):
    """Gera (uid, flags, internaldate, raw) para cada mensagem da pasta."""
    typ, _ = c.select(q(pasta), readonly=True)
    if typ != "OK":
        return
    typ, d = c.uid("search", None, "ALL")
    for uid in d[0].split():
        typ, resp = c.uid("fetch", uid, "(FLAGS INTERNALDATE BODY.PEEK[])")
        if typ != "OK" or not resp or not isinstance(resp[0], tuple):
            print(f"    ! falha ao ler UID {uid.decode()}", file=sys.stderr)
            continue
        meta, raw = resp[0]
        flags = [f for f in imaplib.ParseFlags(meta) if f != b"\\Recent"]
        data = imaplib.Internaldate2tuple(meta)
        flags_str = "(" + " ".join(f.decode() for f in flags) + ")" if flags else None
        yield int(uid), flags_str, data, raw


def anexar(c, pasta, flags, data, raw):
    date_time = time.mktime(data) if data else None
    typ, r = c.append(q(pasta), flags, date_time, raw)
    return typ == "OK"


# ------------------------------------------------------------------- modos
def imap2imap(a):
    src = conectar(a.src_host, a.src_user, a.src_pass, a.src_port)
    dst = conectar(a.dst_host, a.dst_user, a.dst_pass, a.dst_port)
    dst_delim = listar_pastas(dst)[0][1] if listar_pastas(dst) else "/"
    total = pulados = erros = 0

    for nome, delim in listar_pastas(src):
        if a.pasta and nome not in a.pasta:
            continue
        destino = converter_nome(nome, delim, dst_delim)
        if nome.upper() == "INBOX":
            destino = "INBOX"
        print(f"[PASTA] {nome} -> {destino}")
        garantir_pasta(dst, destino)
        existentes = chaves_existentes(dst, destino)

        for uid, flags, data, raw in baixar_msgs(src, nome):
            cab = raw.split(b"\r\n\r\n", 1)[0]
            k = chave_msg(cab)
            if k in existentes:
                pulados += 1
                continue
            try:
                if anexar(dst, destino, flags, data, raw):
                    existentes.add(k)
                    total += 1
                    if total % 100 == 0:
                        print(f"    {total} mensagens copiadas...")
                else:
                    erros += 1
            except Exception as e:  # reconecta e segue
                erros += 1
                print(f"    ! erro UID {uid}: {e}", file=sys.stderr)
                try:
                    dst = conectar(a.dst_host, a.dst_user, a.dst_pass, a.dst_port)
                except Exception:
                    pass
    print(f"\nConcluído: {total} copiadas, {pulados} já existiam, {erros} erros.")
    return {"copiadas": total, "puladas": pulados, "erros": erros}


def nome_seguro(s):
    return re.sub(r'[<>:"|?*]', "_", s)


def exportar(a):
    src = conectar(a.src_host, a.src_user, a.src_pass, a.src_port)
    total = 0
    for nome, delim in listar_pastas(src):
        if a.pasta and nome not in a.pasta:
            continue
        caminho = os.path.join(a.dir, *[nome_seguro(p) for p in nome.split(delim)])
        os.makedirs(caminho, exist_ok=True)
        print(f"[PASTA] {nome}")
        for uid, flags, data, raw in baixar_msgs(src, nome):
            arq = os.path.join(caminho, f"{uid}.eml")
            if os.path.exists(arq):
                continue
            with open(arq, "wb") as f:
                f.write(raw)
            if data:
                t = time.mktime(data)
                os.utime(arq, (t, t))  # data do arquivo = data do e-mail
            total += 1
    print(f"\nExportadas {total} mensagens para {a.dir}")


def importar(a):
    dst = conectar(a.dst_host, a.dst_user, a.dst_pass, a.dst_port)
    delim = listar_pastas(dst)[0][1] if listar_pastas(dst) else "/"
    total = pulados = 0
    for raiz, _, arquivos in os.walk(a.dir):
        emls = [f for f in arquivos if f.lower().endswith(".eml")]
        if not emls:
            continue
        rel = os.path.relpath(raiz, a.dir)
        pasta = "INBOX" if rel == "." else delim.join(rel.split(os.sep))
        print(f"[PASTA] {pasta} ({len(emls)} arquivos)")
        garantir_pasta(dst, pasta)
        existentes = chaves_existentes(dst, pasta)
        for nome in emls:
            arq = os.path.join(raiz, nome)
            raw = open(arq, "rb").read()
            k = chave_msg(raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0])
            if k in existentes:
                pulados += 1
                continue
            data = time.localtime(os.path.getmtime(arq))
            if anexar(dst, pasta, None, data, raw):
                existentes.add(k)
                total += 1
    print(f"\nImportadas {total} mensagens ({pulados} já existiam).")


# ------------------------------------------------------- log e notificação
class Tee:
    """Escreve na tela e no arquivo de log ao mesmo tempo."""
    def __init__(self, *fs):
        self.fs = fs

    def write(self, texto):
        for f in self.fs:
            f.write(texto)
            f.flush()

    def flush(self):
        for f in self.fs:
            f.flush()


def notificar(a, assunto, corpo):
    """Envia o resultado por webhook (Slack/Discord/Mattermost) e/ou e-mail."""
    if a.webhook:
        try:
            txt = f"{assunto}\n{corpo}"
            dados = json.dumps({"text": txt, "content": txt[:1900]}).encode()
            req = urllib.request.Request(
                a.webhook, dados, {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            print(f"!!! falha ao enviar webhook: {e}", file=sys.stderr)
    if a.notify_email and a.smtp_host:
        try:
            m = EmailMessage()
            m["Subject"] = assunto
            m["From"] = a.smtp_from or a.smtp_user or a.notify_email
            m["To"] = a.notify_email
            m.set_content(corpo)
            if a.smtp_port == 465:
                s = smtplib.SMTP_SSL(a.smtp_host, 465, timeout=30)
            else:
                s = smtplib.SMTP(a.smtp_host, a.smtp_port, timeout=30)
                s.starttls()
            with s:
                if a.smtp_user:
                    s.login(a.smtp_user, a.smtp_pass or os.environ.get("SMTP_PASS", ""))
                s.send_message(m)
        except Exception as e:
            print(f"!!! falha ao enviar e-mail de notificação: {e}", file=sys.stderr)


def executar(a):
    """Roda o modo escolhido. Retorna (resumo_texto, tudo_ok)."""
    if a.modo == "imap2imap" and a.csv:
        problemas, linhas = [], []
        with open(a.csv, newline="", encoding="utf-8-sig") as f:
            contas = list(csv.DictReader(f))
        for n, row in enumerate(contas, 1):
            conta = copy.copy(a)
            conta.src_user = row["src_user"].strip()
            conta.src_pass = row["src_pass"]
            conta.dst_user = row["dst_user"].strip()
            conta.dst_pass = row["dst_pass"]
            print(f"\n===== [{n}/{len(contas)}] {conta.src_user} -> "
                  f"{conta.dst_user} =====")
            try:
                r = imap2imap(conta)
                linhas.append(f"OK    {conta.src_user}: {r['copiadas']} copiadas, "
                              f"{r['puladas']} já existiam, {r['erros']} erros")
                if r["erros"]:
                    problemas.append(conta.src_user)
            except Exception as e:
                linhas.append(f"FALHA {conta.src_user}: {e}")
                problemas.append(conta.src_user)
                print(f"!!! FALHOU {conta.src_user}: {e}", file=sys.stderr)
        resumo = (f"Lote: {len(contas) - len(problemas)} ok, "
                  f"{len(problemas)} com problema.\n" + "\n".join(linhas))
        return resumo, not problemas
    if a.modo == "imap2imap":
        r = imap2imap(a)
        return (f"{a.src_user}: {r['copiadas']} copiadas, {r['puladas']} já "
                f"existiam, {r['erros']} erros", r["erros"] == 0)
    if a.modo == "export":
        exportar(a)
    else:
        importar(a)
    return f"Modo {a.modo} concluído.", True


# --------------------------------------------------------------------- CLI
def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("modo", choices=["imap2imap", "export", "import"])
    p.add_argument("--src-host"); p.add_argument("--src-user")
    p.add_argument("--src-pass"); p.add_argument("--src-port", type=int, default=993)
    p.add_argument("--dst-host"); p.add_argument("--dst-user")
    p.add_argument("--dst-pass"); p.add_argument("--dst-port", type=int, default=993)
    p.add_argument("--csv", help="arquivo CSV para migrar várias contas "
                   "(colunas: src_user,src_pass,dst_user,dst_pass)")
    p.add_argument("--dir", help="pasta local (export/import)")
    p.add_argument("--pasta", action="append",
                   help="migrar só esta pasta (pode repetir)")
    p.add_argument("--timeout", type=int, default=60,
                   help="segundos sem resposta do servidor antes de dar erro (60)")
    p.add_argument("--log", default="migracao.log", help="arquivo de log")
    p.add_argument("--webhook", help="URL de webhook (Slack/Discord/Mattermost)")
    p.add_argument("--notify-email", help="e-mail que recebe o resultado")
    p.add_argument("--smtp-host"); p.add_argument("--smtp-port", type=int, default=587)
    p.add_argument("--smtp-user"); p.add_argument("--smtp-pass")
    p.add_argument("--smtp-from")
    a = p.parse_args()

    # Senhas: flag > variável de ambiente (SRC_PASS/DST_PASS) > prompt oculto
    if a.modo in ("imap2imap", "export") and a.src_user and not a.src_pass:
        a.src_pass = os.environ.get("SRC_PASS") or getpass.getpass(
            f"Senha de origem ({a.src_user}): ")
    if a.modo in ("imap2imap", "import") and a.dst_user and not a.dst_pass:
        a.dst_pass = os.environ.get("DST_PASS") or getpass.getpass(
            f"Senha de destino ({a.dst_user}): ")

    socket.setdefaulttimeout(a.timeout)
    log = open(a.log, "a", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)
    print(f"\n##### Início {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"- modo {a.modo} #####")

    try:
        resumo, ok = executar(a)
    except Exception as e:
        resumo, ok = f"ERRO FATAL: {e}", False
        print(resumo, file=sys.stderr)

    print("\n----- RESUMO -----\n" + resumo)
    print(f"\n##### Fim {time.strftime('%Y-%m-%d %H:%M:%S')} #####")
    notificar(a, f"[migração] {'OK' if ok else 'COM PROBLEMAS'}", resumo)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()