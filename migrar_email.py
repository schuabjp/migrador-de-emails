#!/usr/bin/env python3
"""
Migração de e-mails sem MailStore.

Modos:
  imap2imap  -> copia de um servidor IMAP para outro
  export     -> exporta de um servidor IMAP para pastas com arquivos .eml
  import     -> importa uma árvore de pastas .eml para um servidor IMAP

Preserva: estrutura de pastas, flags (lido, respondido...) e data original.
Evita duplicados (compara Message-ID + Date + Subject + From), então pode ser
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
import shutil
import smtplib
import socket
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from email.message import EmailMessage

imaplib._MAXLINE = 100_000_000  # respostas grandes

LIST_RE = re.compile(r'\((?P<flags>.*?)\) (?P<delim>"[^"]*"|NIL) (?P<name>.*)')
UID_RE = re.compile(rb"UID (\d+)")
SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
CAMPOS_CHAVE = "(UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE SUBJECT FROM)])"
LOTE = 500  # UIDs por FETCH de cabeçalhos
LOTE_MSGS = 50  # mensagens completas por FETCH...
LOTE_BYTES = 20 * 1024 * 1024  # ...limitado a ~20 MB por lote


# ----------------------------------------------------------------------- UI
def fmt_bytes(n):
    for un in ("B", "KB", "MB", "GB"):
        if n < 1024 or un == "GB":
            return f"{n:.0f} {un}" if un == "B" else f"{n:.1f} {un}"
        n /= 1024


def fmt_tempo(s):
    s = int(s)
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


class Ui:
    """Saída colorida no terminal; o arquivo de log recebe texto puro."""
    CORES = {"verde": 32, "vermelho": 31, "amarelo": 33, "azul": 36,
             "cinza": 90, "negrito": 1}

    def __init__(self):
        self.log = None
        self.tty = sys.stdout is not None and sys.stdout.isatty()  # None em pythonw
        self.cor = self.tty and "NO_COLOR" not in os.environ
        self._barra = False  # há uma barra de progresso na linha atual?
        for s in (sys.stdout, sys.stderr):
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace")
        if os.name == "nt" and self.tty:
            os.system("")  # habilita sequências ANSI no console do Windows

    def abrir_log(self, caminho):
        self.log = open(caminho, "a", encoding="utf-8")

    # -- primitivas
    def c(self, cor, texto):
        return f"\x1b[{self.CORES[cor]}m{texto}\x1b[0m" if self.cor else str(texto)

    def _limpar_barra(self):
        if self._barra:
            sys.stdout.write("\r\x1b[2K")
            self._barra = False

    def _emitir(self, texto, stream=None):
        self._limpar_barra()
        print(texto, file=stream or sys.stdout, flush=True)
        if self.log:
            self.log.write(f"{time.strftime('%H:%M:%S')} "
                           f"{ANSI_RE.sub('', texto)}\n")
            self.log.flush()

    # -- mensagens
    def texto(self, t=""):
        self._emitir(t)

    def info(self, t):
        self._emitir(f"  {self.c('azul', 'ℹ')} {t}")

    def ok(self, t):
        self._emitir(f"  {self.c('verde', '✔')} {t}")

    def aviso(self, t):
        self._emitir(f"  {self.c('amarelo', '⚠')} {t}", sys.stderr)

    def erro(self, t):
        self._emitir(f"  {self.c('vermelho', '✖')} {t}", sys.stderr)

    def titulo(self, t):
        barra = "═" * (len(ANSI_RE.sub("", t)) + 2)
        self._emitir(self.c("azul", f"\n╔{barra}╗\n║ ") + self.c("negrito", t)
                     + self.c("azul", f" ║\n╚{barra}╝"))

    def secao(self, t):
        self._emitir(f"\n{self.c('negrito', '▸ ' + t)}")

    # -- progresso
    def progresso(self, feito, total, inicio, bytes_=0):
        """Barra de uma linha (TTY) ou um aviso a cada 100 itens (log/pipe)."""
        if not self.tty:
            if feito % 100 == 0 and feito != total:
                self._emitir(f"    {feito}/{total} processadas")
            return
        pct = feito / total if total else 1
        decorrido = time.time() - inicio
        eta = decorrido / feito * (total - feito) if feito else 0
        largura = max(10, min(30, shutil.get_terminal_size().columns - 70))
        cheio = int(largura * pct)
        barra = self.c("verde", "█" * cheio) + self.c("cinza", "░" * (largura - cheio))
        vel = f" · {fmt_bytes(bytes_ / decorrido)}/s" if bytes_ and decorrido else ""
        sys.stdout.write(f"\r\x1b[2K    {barra} {pct:4.0%}  {feito}/{total}"
                         f"{vel} · ETA {fmt_tempo(eta)}")
        sys.stdout.flush()
        self._barra = feito < total
        if feito >= total:
            sys.stdout.write("\r\x1b[2K")

    # -- tabela
    def tabela(self, cabecalho, linhas):
        """Primeira coluna alinhada à esquerda, as demais à direita."""
        tudo = [cabecalho] + linhas
        larg = [max(len(ANSI_RE.sub("", str(l[i]))) for l in tudo)
                for i in range(len(cabecalho))]

        def celula(i, v):
            pad = " " * (larg[i] - len(ANSI_RE.sub("", str(v))))
            return f"{v}{pad}" if i == 0 else f"{pad}{v}"

        def linha(l):
            return "│ " + " │ ".join(celula(i, v) for i, v in enumerate(l)) + " │"

        sep = lambda a, b, c: a + b.join("─" * (w + 2) for w in larg) + c
        self._emitir(sep("┌", "┬", "┐"))
        self._emitir(self.c("negrito", linha(cabecalho)))
        self._emitir(sep("├", "┼", "┤"))
        for l in linhas:
            self._emitir(linha(l))
        self._emitir(sep("└", "┴", "┘"))

    # -- eventos estruturados (o terminal ignora; a interface gráfica usa)
    def conta_iniciada(self, n, total, src_user, dst_user):
        pass

    def conta_concluida(self, n, status, detalhe):
        pass

    def pasta_iniciada(self, nome, mensagens, ja_existem):
        pass

    def pasta_concluida(self, linha):
        """linha = [pasta, mensagens, copiadas, já existiam, erros]"""
        pass


ui = Ui()


@dataclass
class Stats:
    copiadas: int = 0
    puladas: int = 0
    erros: int = 0
    bytes: int = 0
    pastas: list = field(default_factory=list)  # linhas da tabela por pasta

    def __iadd__(self, o):
        self.copiadas += o.copiadas
        self.puladas += o.puladas
        self.erros += o.erros
        self.bytes += o.bytes
        self.pastas += o.pastas
        return self

    @property
    def descricao(self):
        return (f"{self.copiadas} copiadas, {self.puladas} já existiam, "
                f"{self.erros} erros")


# ---------------------------------------------------------------- IMAP util
@dataclass
class Conta:
    host: str
    user: str
    pwd: str
    port: int = 993

    def conectar(self, tentativas=3):
        """Conecta com retentativas em falha de rede; senha errada falha direto."""
        ultimo = None
        for i in range(1, tentativas + 1):
            try:
                c = imaplib.IMAP4_SSL(self.host, self.port)
            except OSError as e:  # DNS, conexão recusada, timeout, SSL
                ultimo = e
                ui.aviso(f"conexão com {self.host} falhou ({e}); "
                         f"tentativa {i}/{tentativas}")
                if i < tentativas:
                    time.sleep(5 * i)
                continue
            try:
                c.login(self.user, self.pwd)
            except imaplib.IMAP4.error as e:
                raise RuntimeError(f"login recusado em {self.host} para {self.user} "
                                   f"(usuário/senha errados ou acesso bloqueado): {e}")
            return c
        raise RuntimeError(f"não foi possível conectar em {self.host}:{self.port} "
                           f"após {tentativas} tentativas: {ultimo}")


def conta_de(a, lado):
    return Conta(getattr(a, f"{lado}_host"), getattr(a, f"{lado}_user"),
                 getattr(a, f"{lado}_pass"), getattr(a, f"{lado}_port"))


def q(nome):
    """Coloca o nome da pasta entre aspas para o comando IMAP."""
    return '"' + nome.replace("\\", "\\\\").replace('"', '\\"') + '"'


def listar_pastas(c):
    """Retorna [(nome_bruto, delimitador)] das pastas selecionáveis."""
    _, dados = c.list()
    pastas = []
    for linha in dados:
        if not linha:
            continue
        literal = None
        if isinstance(linha, tuple):  # nome literal: vem à parte, já sem aspas
            linha, literal = linha[0], linha[1].decode("utf-8", "replace")
        m = LIST_RE.match(linha.decode("utf-8", "replace"))
        if not m or "\\noselect" in m["flags"].lower():
            continue
        delim = m["delim"].strip('"') if m["delim"] != "NIL" else "/"
        pastas.append((literal if literal is not None else m["name"].strip().strip('"'),
                       delim))
    return pastas


def delimitador(c):
    pastas = listar_pastas(c)
    return pastas[0][1] if pastas else "/"


def chave_msg(raw_headers: bytes) -> str:
    """Identifica a mensagem por Message-ID + Date + Subject + From."""
    msg = email.message_from_bytes(raw_headers)
    partes = [" ".join(str(msg.get(h, "")).split())
              for h in ("Message-ID", "Date", "Subject", "From")]
    return hashlib.sha1("|".join(partes).encode("utf-8", "replace")).hexdigest()


def info_da_resposta(resp, idx):
    """Metadados de um item FETCH (podem vir antes ou depois do literal)."""
    prox = resp[idx + 1] if idx + 1 < len(resp) else None
    return resp[idx][0] + b" " + (prox if isinstance(prox, bytes) else b"")


def numero(regex, info):
    m = regex.search(info)
    return int(m.group(1)) if m else None


def uids_da_pasta(c, pasta):
    typ, _ = c.select(q(pasta), readonly=True)
    if typ != "OK":
        return []
    _, d = c.uid("search", None, "ALL")
    return [int(u) for u in d[0].split()]


def chaves_pasta(c, pasta, rotulo):
    """[(uid, chave, tamanho)] lendo só os cabeçalhos (rápido, sem baixar o corpo).
    Se não conseguir ler TODAS, aborta: melhor parar do que arriscar duplicar."""
    uids = uids_da_pasta(c, pasta)
    saida = []
    for i in range(0, len(uids), LOTE):
        lote = ",".join(map(str, uids[i:i + LOTE]))
        typ, resp = c.uid("fetch", lote, CAMPOS_CHAVE)
        if typ != "OK":
            raise RuntimeError(f"falha ao ler cabeçalhos de {rotulo} ({pasta})")
        for idx, item in enumerate(resp):
            if isinstance(item, tuple):
                info = info_da_resposta(resp, idx)
                saida.append((numero(UID_RE, info), chave_msg(item[1]),
                              numero(SIZE_RE, info) or 0))
    if len(saida) != len(uids) or any(u is None for u, _, _ in saida):
        raise RuntimeError(f"leitura incompleta de {rotulo} ({pasta}); "
                           "abortado para não gerar duplicados")
    return sorted(saida)


def chaves_existentes(c, pasta):
    """Counter das chaves das mensagens já presentes no destino."""
    return Counter(k for _, k, _ in chaves_pasta(c, pasta, "destino"))


def separar_novas(candidatos, existentes):
    """(novas, qtd_ja_existentes) — respeita duplicatas legítimas via contagem."""
    vistas, novas = Counter(), []
    for item, k in candidatos:
        vistas[k] += 1
        if vistas[k] > existentes[k]:
            novas.append(item)
    return novas, len(candidatos) - len(novas)


def garantir_pasta(c, nome):
    typ, _ = c.select(q(nome), readonly=True)
    if typ != "OK":
        typ, d = c.create(q(nome))
        if typ != "OK":
            raise RuntimeError(f"não foi possível criar a pasta {nome}: {d}")
        c.subscribe(q(nome))


def buscar_lote(c, uids):
    """Baixa várias mensagens num único FETCH (economiza ida-e-volta na rede).
    Retorna {uid: (flags, internaldate, bytes)}; quem faltar não pôde ser lida."""
    try:
        typ, resp = c.uid("fetch", ",".join(map(str, uids)),
                          "(UID FLAGS INTERNALDATE BODY.PEEK[])")
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return {}
    msgs = {}
    if typ != "OK":
        return msgs
    for idx, item in enumerate(resp):
        if not isinstance(item, tuple):
            continue
        info = info_da_resposta(resp, idx)
        flags = [f.decode() for f in imaplib.ParseFlags(info) if f != b"\\Recent"]
        msgs[numero(UID_RE, info)] = ("(" + " ".join(flags) + ")" if flags else None,
                                      imaplib.Internaldate2tuple(info), item[1])
    return msgs


def buscar_msg(c, uid):
    """Baixa UMA mensagem completa. Retorna (flags, internaldate, bytes) ou None."""
    return buscar_lote(c, [uid]).get(uid)


def agrupar(uids, tamanhos):
    """Divide os UIDs em lotes de até LOTE_MSGS mensagens / LOTE_BYTES bytes."""
    lote, soma = [], 0
    for uid in uids:
        tam = tamanhos.get(uid, 0)
        if lote and (len(lote) >= LOTE_MSGS or soma + tam > LOTE_BYTES):
            yield lote
            lote, soma = [], 0
        lote.append(uid)
        soma += tam
    if lote:
        yield lote


def anexar(c, pasta, flags, data, raw):
    typ, _ = c.append(q(pasta), flags, time.mktime(data) if data else None, raw)
    return typ == "OK"


def escolhida(a, nome):
    return not a.pasta or nome in a.pasta


# ------------------------------------------------------------------- modos
def enviar(dst, destino, uid, msg, st):
    """Anexa uma mensagem no destino; erros de rede sobem para quem reconecta."""
    try:
        if msg is None:
            raise RuntimeError("não foi possível ler a mensagem na origem")
        if not anexar(dst, destino, *msg):
            raise RuntimeError("o destino recusou a mensagem")
    except (imaplib.IMAP4.abort, OSError):
        raise
    except Exception as e:
        st.erros += 1
        ui.erro(f"UID {uid}: {e}")
        return
    st.copiadas += 1
    st.bytes += len(msg[2])


def copiar_pendentes(a, src, dst, nome, destino, pendentes, tamanhos, st):
    """Baixa em lotes e envia as novas; reconecta (e tenta de novo uma vez)
    se a rede cair, retomando só o que ainda faltava do lote."""
    inicio, feitas = time.time(), 0
    for lote in agrupar(pendentes, tamanhos):
        for tentativa in (1, 2):
            try:
                msgs = buscar_lote(src, lote)
                while lote:
                    enviar(dst, destino, lote[0], msgs.pop(lote[0], None), st)
                    lote.pop(0)
                    feitas += 1
                    ui.progresso(feitas, len(pendentes), inicio, st.bytes)
                break
            except (imaplib.IMAP4.abort, OSError) as e:
                ui.aviso(f"conexão caiu no UID {lote[0]} ({e}); reconectando...")
                src = conta_de(a, "src").conectar()
                src.select(q(nome), readonly=True)
                dst = conta_de(a, "dst").conectar()
                if tentativa == 2:
                    st.erros += len(lote)
                    ui.erro(f"{len(lote)} mensagens não copiadas (UIDs {lote[0]}…{lote[-1]})")
                    feitas += len(lote)
                    ui.progresso(feitas, len(pendentes), inicio, st.bytes)
    return src, dst


def imap2imap(a):
    src = conta_de(a, "src").conectar()
    dst = conta_de(a, "dst").conectar()
    dst_delim = delimitador(dst)
    total = Stats()

    for nome, delim in listar_pastas(src):
        if not escolhida(a, nome):
            continue
        destino = "INBOX" if nome.upper() == "INBOX" else nome.replace(delim, dst_delim)
        ui.secao(f"{nome} → {destino}")
        garantir_pasta(dst, destino)

        # 1) compara só cabeçalhos: o que já está no destino não é baixado
        existentes = chaves_existentes(dst, destino)
        origem = chaves_pasta(src, nome, "origem")
        pendentes, puladas = separar_novas([(u, k) for u, k, _ in origem], existentes)
        tamanhos = {u: t for u, _, t in origem}
        ui.info(f"origem {len(origem)} · já no destino {puladas} · "
                f"{ui.c('negrito', f'novas {len(pendentes)}')}")
        ui.pasta_iniciada(nome, len(origem), puladas)

        # 2) baixa e envia somente as novas
        st = Stats(puladas=puladas)
        src, dst = copiar_pendentes(a, src, dst, nome, destino, pendentes, tamanhos, st)
        if pendentes:
            ui.ok(f"{st.copiadas} copiadas ({fmt_bytes(st.bytes)})"
                  + (f", {ui.c('vermelho', str(st.erros) + ' erros')}" if st.erros else ""))
        ui.pasta_concluida([nome, len(origem), st.copiadas, st.puladas, st.erros])
        st.pastas.append([nome, len(origem), st.copiadas, st.puladas,
                          ui.c("vermelho", st.erros) if st.erros else st.erros])
        total += st
    return total


def nome_seguro(s):
    return re.sub(r'[<>:"|?*]', "_", s)


def exportar(a):
    src = conta_de(a, "src").conectar()
    total = Stats()
    for nome, delim in listar_pastas(src):
        if not escolhida(a, nome):
            continue
        caminho = os.path.join(a.dir, *[nome_seguro(p) for p in nome.split(delim)])
        os.makedirs(caminho, exist_ok=True)
        uids = uids_da_pasta(src, nome)
        ui.secao(f"{nome} ({len(uids)} mensagens)")
        ui.pasta_iniciada(nome, len(uids), 0)
        st, inicio = Stats(), time.time()
        for n, uid in enumerate(uids, 1):
            arq = os.path.join(caminho, f"{uid}.eml")
            if os.path.exists(arq):  # já exportada: nem baixa de novo
                st.puladas += 1
            elif (msg := buscar_msg(src, uid)) is None:
                st.erros += 1
                ui.erro(f"falha ao ler UID {uid}")
            else:
                _, data, raw = msg
                tmp = arq + ".part"  # só vira .eml completo: interrupção não deixa arquivo pela metade
                with open(tmp, "wb") as f:
                    f.write(raw)
                if data:
                    t = time.mktime(data)
                    os.utime(tmp, (t, t))  # data do arquivo = data do e-mail
                os.replace(tmp, arq)
                st.copiadas += 1
                st.bytes += len(raw)
            ui.progresso(n, len(uids), inicio, st.bytes)
        ui.ok(f"{st.copiadas} exportadas ({fmt_bytes(st.bytes)}), "
              f"{st.puladas} já existiam")
        st.pastas.append([nome, len(uids), st.copiadas, st.puladas, st.erros])
        ui.pasta_concluida(st.pastas[-1])
        total += st
    return total


def ler_cabecalho(arq):
    """Lê o .eml só até a linha em branco que separa cabeçalho e corpo."""
    linhas = []
    with open(arq, "rb") as f:
        for linha in f:
            if linha in (b"\r\n", b"\n"):
                break
            linhas.append(linha)
    return b"".join(linhas)


def importar(a):
    dst = conta_de(a, "dst").conectar()
    delim = delimitador(dst)
    total = Stats()
    for raiz, _, arquivos in sorted(os.walk(a.dir)):
        emls = sorted(f for f in arquivos if f.lower().endswith(".eml"))
        if not emls:
            continue
        rel = os.path.relpath(raiz, a.dir)
        pasta = "INBOX" if rel == "." else delim.join(rel.split(os.sep))
        ui.secao(f"{pasta} ({len(emls)} arquivos)")
        garantir_pasta(dst, pasta)
        existentes = chaves_existentes(dst, pasta)

        # só os cabeçalhos agora; o arquivo inteiro é lido apenas na hora de enviar
        candidatos = [(arq, chave_msg(ler_cabecalho(arq)))
                      for arq in (os.path.join(raiz, nome) for nome in emls)]
        novas, puladas = separar_novas(candidatos, existentes)
        ui.pasta_iniciada(pasta, len(emls), puladas)

        st, inicio = Stats(puladas=puladas), time.time()
        for n, arq in enumerate(novas, 1):
            with open(arq, "rb") as f:
                raw = f.read()
            data = time.localtime(os.path.getmtime(arq))
            if anexar(dst, pasta, None, data, raw):
                st.copiadas += 1
                st.bytes += len(raw)
            else:
                st.erros += 1
                ui.erro(f"o destino recusou {os.path.basename(arq)}")
            ui.progresso(n, len(novas), inicio, st.bytes)
        ui.ok(f"{st.copiadas} importadas ({fmt_bytes(st.bytes)}), "
              f"{st.puladas} já existiam")
        st.pastas.append([pasta, len(emls), st.copiadas, st.puladas, st.erros])
        ui.pasta_concluida(st.pastas[-1])
        total += st
    return total


# ------------------------------------------------------------- notificação
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
            ui.erro(f"falha ao enviar webhook: {e}")
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
            ui.erro(f"falha ao enviar e-mail de notificação: {e}")


# --------------------------------------------------------------- execução
MODOS = {"imap2imap": imap2imap, "export": exportar, "import": importar}
COLUNAS = ["Pasta", "Mensagens", "Copiadas", "Já existiam", "Erros"]


def mostrar_stats(st):
    if st.pastas:
        ui.tabela(COLUNAS, st.pastas)
    ui.texto(f"  Total: {ui.c('verde', st.copiadas)} copiadas · "
             f"{st.puladas} já existiam · "
             f"{ui.c('vermelho' if st.erros else 'verde', st.erros)} erros · "
             f"{fmt_bytes(st.bytes)}")


def executar_lote(a):
    """imap2imap para cada conta do CSV. Retorna (resumo_texto, tudo_ok)."""
    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        contas = list(csv.DictReader(f))
    linhas, plano, problemas = [], [], 0
    for n, row in enumerate(contas, 1):
        conta = copy.copy(a)
        conta.src_user, conta.src_pass = row["src_user"].strip(), row["src_pass"]
        conta.dst_user, conta.dst_pass = row["dst_user"].strip(), row["dst_pass"]
        ui.titulo(f"[{n}/{len(contas)}] {conta.src_user} → {conta.dst_user}")
        ui.conta_iniciada(n, len(contas), conta.src_user, conta.dst_user)
        try:
            st = imap2imap(conta)
            mostrar_stats(st)
            falhou = st.erros > 0
            status, detalhe = ("COM ERROS" if falhou else "OK"), st.descricao
        except Exception as e:
            falhou, status, detalhe = True, "FALHA", str(e)
            ui.erro(f"{conta.src_user}: {e}")
        ui.conta_concluida(n, status, detalhe)
        problemas += falhou
        cor = "vermelho" if falhou else "verde"
        linhas.append([conta.src_user, ui.c(cor, status), detalhe])
        plano.append(f"{status:<9} {conta.src_user}: {detalhe}")
    ui.titulo("RESUMO DO LOTE")
    ui.tabela(["Conta", "Status", "Detalhe"], linhas)
    resumo = (f"Lote: {len(contas) - problemas} ok, {problemas} com problema.\n"
              + "\n".join(plano))
    return resumo, problemas == 0


def executar(a):
    """Roda o modo escolhido. Retorna (resumo_texto, tudo_ok)."""
    if a.modo == "imap2imap" and a.csv:
        return executar_lote(a)
    st = MODOS[a.modo](a)
    ui.titulo("RESUMO")
    mostrar_stats(st)
    return f"Modo {a.modo}: {st.descricao}", st.erros == 0


# --------------------------------------------------------------------- CLI
def criar_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("modo", choices=MODOS)
    for lado in ("src", "dst"):
        p.add_argument(f"--{lado}-host")
        p.add_argument(f"--{lado}-user")
        p.add_argument(f"--{lado}-pass")
        p.add_argument(f"--{lado}-port", type=int, default=993)
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
    p.add_argument("--smtp-host")
    p.add_argument("--smtp-port", type=int, default=587)
    p.add_argument("--smtp-user")
    p.add_argument("--smtp-pass")
    p.add_argument("--smtp-from")
    return p


def pedir_senhas(a):
    """Senhas: flag > variável de ambiente (SRC_PASS/DST_PASS) > prompt oculto."""
    for lado, modos, rotulo in (("src", ("imap2imap", "export"), "origem"),
                                ("dst", ("imap2imap", "import"), "destino")):
        user = getattr(a, f"{lado}_user")
        if a.modo in modos and user and not getattr(a, f"{lado}_pass"):
            senha = (os.environ.get(f"{lado.upper()}_PASS")
                     or getpass.getpass(f"Senha de {rotulo} ({user}): "))
            setattr(a, f"{lado}_pass", senha)


def main():
    a = criar_parser().parse_args()
    pedir_senhas(a)
    socket.setdefaulttimeout(a.timeout)
    ui.abrir_log(a.log)

    inicio = time.time()
    ui.titulo(f"Migração de e-mails · modo {a.modo} · "
              f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        resumo, ok = executar(a)
    except KeyboardInterrupt:
        resumo, ok = "Interrompido pelo usuário (pode rodar de novo para continuar).", False
        ui.aviso(resumo)
    except Exception as e:
        resumo, ok = f"ERRO FATAL: {e}", False
        ui.erro(resumo)

    ui.texto(f"\n{ui.c('verde' if ok else 'vermelho', '● ' + ('Concluído' if ok else 'Terminou com problemas'))}"
             f" em {fmt_tempo(time.time() - inicio)}")
    notificar(a, f"[migração] {'OK' if ok else 'COM PROBLEMAS'}", resumo)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
