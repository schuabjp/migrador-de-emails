#!/usr/bin/env python3
"""
Migrador de E-mails — aplicativo desktop.

Interface gráfica para o migrar_email.py: usa o mesmo motor (mesmos modos,
deduplicação, reconexão e notificações); esta camada coleta os dados, testa
as conexões, mostra o andamento e guarda um relatório de cada execução.

    python migrador_gui.py        (ou pythonw migrador_gui.py, sem console)
"""
import base64
import csv
import glob
import json
import math
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import tkinter as tk
import zlib
from tkinter import filedialog, font as tkfont, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atualizador  # noqa: E402
import migrar_email as me  # noqa: E402
from versao import VERSAO  # noqa: E402

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                       "MigradorEmails")
PERFIL = os.path.join(APP_DIR, "perfil.json")
LOG_PADRAO = os.path.join(APP_DIR, "migracao.log")
HIST_DIR = os.path.join(APP_DIR, "historico")
TITULO = "Migrador de E-mails"
LIMITE_LOG = 20_000  # linhas mantidas na tela; o arquivo de log guarda tudo
SERVIDORES = ["imap.gmail.com", "outlook.office365.com", "imap.mail.yahoo.com",
              "imap.mail.me.com", "imap.zoho.com", "imap.hostinger.com"]
CAMPOS_PERFIL = ["modo", "src_host", "src_port", "src_user", "dst_host", "dst_port",
                 "dst_user", "dir", "csv", "timeout", "log", "webhook",
                 "notify_email", "smtp_host", "smtp_port", "smtp_user", "smtp_from"]
MODOS_GUI = {  # modo da tela -> (título, descrição, modo do motor)
    "imap2imap": ("Servidor → Servidor", "Copia direto de um IMAP para outro", "imap2imap"),
    "export": ("Exportar backup", "Salva as mensagens em arquivos .eml", "export"),
    "import": ("Importar backup", "Envia arquivos .eml para um servidor", "import"),
    "lote": ("Várias contas", "Migra uma lista de contas de um CSV", "imap2imap"),
}

COR = {
    "fundo": "#f5f5f3", "superficie": "#ffffff", "borda": "#e4e4df", "borda_campo": "#d6d6d0",
    "cabecalho": "#f3f3f0", "zebra": "#fafaf8", "trilho": "#ebebe8",
    "tinta": "#1b1b1b", "tinta2": "#4d4d4a", "muda": "#8a8a85",
    "grade": "#ececea", "eixo": "#c9c9c3",
    "destaque": "#2a78d6", "destaque_esc": "#1c5cab", "destaque_claro": "#e5effc",
    "area": "#dce9fb", "selecionado": "#f0f6fe", "hover": "#f8fafd",
    "ok": "#1c9f2c", "ok_texto": "#1f7a2b", "aviso": "#d99a00", "aviso_texto": "#94650a",
    "erro": "#c93636", "erro_texto": "#b02a2a",
    "lateral": "#191b20", "lateral_hover": "#22252c", "lateral_ativo": "#2c3039",
    "lateral_texto": "#d2d3d6", "lateral_mudo": "#80848d",
}
PILULAS = {  # estado -> (texto, fundo, tinta)
    "parado": ("Aguardando", "#ededea", COR["tinta2"]),
    "rodando": ("●  Em andamento", COR["destaque_claro"], COR["destaque_esc"]),
    "parando": ("●  Parando…", "#fbf0d9", COR["aviso_texto"]),
    "ok": ("✓  Concluído", "#e4f4e6", COR["ok_texto"]),
    "falha": ("✕  Com problemas", "#fbe7e7", COR["erro_texto"]),
    "cancelado": ("⚠  Interrompido", "#fbf0d9", COR["aviso_texto"]),
}
BANNER = {  # estado -> (ícone, fundo, borda, tinta)
    "ok": ("✓", "#eef8ef", "#cde8d1", COR["ok_texto"]),
    "falha": ("✕", "#fcefef", "#f0cccc", COR["erro_texto"]),
    "cancelado": ("⚠", "#fdf6e6", "#f0dcaa", COR["aviso_texto"]),
}
SITUACAO_PASTA = {  # status -> (texto, ícone, tag da linha)
    "pendente": ("Pendente", "pendente", "muda"), "andamento": ("Copiando", "andamento", "ativa"),
    "ok": ("Concluída", "ok", ""), "erros": ("Com erros", "aviso", ""),
    "parada": ("Interrompida", "parada", ""),
}
SITUACAO_CONTA = {
    "pendente": ("Pendente", "pendente", "muda"), "andamento": ("Migrando", "andamento", "ativa"),
    "OK": ("OK", "ok", ""), "COM ERROS": ("Com erros", "aviso", ""),
    "FALHA": ("Falhou", "falha", ""), "parada": ("Interrompida", "parada", ""),
}
RESULTADO = {"ok": ("Concluída", "ok"), "falha": ("Com problemas", "falha"),
             "cancelado": ("Interrompida", "parada"), "rodando": ("Interrompida", "parada")}
# colunas: (id, título, largura, alinhamento, elástica); "#0" é a coluna com ícone
COLS_PASTAS = [("#0", "Situação", 150, "w", False), ("conta", "Conta", 180, "w", False),
               ("pasta", "Pasta", 260, "w", True), ("msgs", "Mensagens", 100, "e", False),
               ("cop", "Copiadas", 100, "e", False), ("pul", "Já existiam", 110, "e", False),
               ("err", "Erros", 70, "e", False), ("tam", "Tamanho", 90, "e", False),
               ("tempo", "Tempo", 80, "e", False)]
VISIVEIS_ANDAMENTO = ["conta", "pasta", "msgs", "cop", "pul", "err"]
COLS_PROBLEMAS = [("#0", "Tipo", 100, "w", False), ("hora", "Hora", 80, "w", False),
                  ("conta", "Conta", 180, "w", False), ("pasta", "Pasta", 180, "w", False),
                  ("msg", "Mensagem", 420, "w", True)]
COLS_CONTAS = [("#0", "Situação", 130, "w", False), ("n", "#", 40, "w", False),
               ("src", "Origem", 150, "w", True), ("dst", "Destino", 150, "w", True),
               ("cop", "Copiadas", 84, "e", False), ("pul", "Já existiam", 96, "e", False),
               ("err", "Erros", 56, "e", False), ("det", "Detalhe", 160, "w", True)]
COLS_HIST = [("#0", "Resultado", 160, "w", False), ("data", "Data", 130, "w", False),
             ("tipo", "Tipo", 150, "w", False), ("rota", "De → Para", 340, "w", True),
             ("cop", "Copiadas", 90, "e", False), ("pul", "Já existiam", 100, "e", False),
             ("err", "Erros", 64, "e", False), ("dur", "Duração", 80, "e", False)]
COLS_CFG = [("#0", "Pasta", 420, "w", True), ("msgs", "Mensagens", 120, "e", False)]
COLS_CSV = [("n", "#", 50, "w", False), ("src", "Conta de origem", 300, "w", True),
            ("dst", "Conta de destino", 300, "w", True)]
FONTE = "Segoe UI"
FONTE_SEMI = "Segoe UI Semibold"
MONO = "Consolas"


def num(n):
    return f"{n:,}".replace(",", ".")


def plural(n, singular, plural_):
    return f"{num(n)} {singular if n == 1 else plural_}"


def data_hora(ts):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def decodificar_utf7(nome):
    """Nomes de pasta IMAP vêm em UTF-7 modificado (ex.: 'Lixo Eletr&APQ-nico')."""
    def troca(m):
        if not m.group(1):
            return "&"
        b64 = m.group(1).replace(",", "/")
        return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")
    try:
        return re.sub(r"&([A-Za-z0-9+,]*)-", troca, nome)
    except ValueError:
        return nome


def maximo_redondo(v):
    """Topo do eixo: 4 divisões de 1/2/2,5/5 × 10^k."""
    passo = max(v, 1) / 4
    base = 10 ** (len(str(int(passo))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if m * base >= passo:
            return m * base * 4
    return passo * 4


# ------------------------------------------------------- relatórios em disco
def salvar_json(caminho, dados):
    """Grava num temporário e troca: uma queda nunca deixa o arquivo pela metade."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def carregar_historico():
    execucoes = []
    for arq in glob.glob(os.path.join(HIST_DIR, "*.json")):
        try:
            with open(arq, encoding="utf-8") as f:
                execucoes.append(json.load(f))
        except (OSError, ValueError):
            continue
    return sorted(execucoes, key=lambda r: r.get("inicio", 0), reverse=True)


def linha_pasta(p):
    texto, icone, tag = SITUACAO_PASTA[p["status"]]
    return (((icone, texto), p["conta"], decodificar_utf7(p["pasta"]), num(p["mensagens"]),
             num(p["copiadas"]), num(p["puladas"]), num(p["erros"]),
             me.fmt_bytes(p["bytes"]) if p["bytes"] else "—",
             me.fmt_tempo(p["segundos"]) if p["segundos"] >= 1 else
             "<1s" if p["status"] != "pendente" else "—"), (tag,) if tag else ())


def linha_conta(c):
    texto, icone, tag = SITUACAO_CONTA[c["status"]]
    return (((icone, texto), c["n"], c["src"], c["dst"], num(c["copiadas"]), num(c["puladas"]),
             num(c["erros"]), c["detalhe"]), (tag,) if tag else ())


def linha_problema(pr):
    erro = pr["nivel"] == "erro"
    return (((("erro", "Erro") if erro else ("aviso", "Aviso")), pr["hora"], pr["conta"],
             pr["pasta"], pr["texto"]), ())


def exportar_csv(rel, caminho):
    """Relatório completo em CSV (separador ';' abre direto no Excel em português)."""
    t = rel["totais"]
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        for rotulo, valor in (("Início", data_hora(rel["inicio"])),
                              ("Duração", me.fmt_tempo(rel.get("duracao", 0))),
                              ("Tipo", rel["tipo"]), ("Origem", rel["origem"]),
                              ("Destino", rel["destino"]),
                              ("Resultado", RESULTADO.get(rel["estado"], (rel["estado"],))[0]),
                              ("Resumo", rel.get("resumo", "")), ("Copiadas", t["copiadas"]),
                              ("Já existiam", t["puladas"]), ("Erros", t["erros"]),
                              ("Transferido (bytes)", t["bytes"])):
            w.writerow([rotulo, valor])
        if rel["contas"]:
            w.writerow([])
            w.writerow(["Conta de origem", "Conta de destino", "Situação", "Copiadas",
                        "Já existiam", "Erros", "Detalhe"])
            for c in rel["contas"]:
                w.writerow([c["src"], c["dst"], SITUACAO_CONTA[c["status"]][0], c["copiadas"],
                            c["puladas"], c["erros"], c["detalhe"]])
        w.writerow([])
        w.writerow(["Conta", "Pasta", "Situação", "Mensagens", "Copiadas", "Já existiam",
                    "Erros", "Tamanho (bytes)", "Tempo (s)"])
        for p in rel["pastas"]:
            w.writerow([p["conta"], decodificar_utf7(p["pasta"]), SITUACAO_PASTA[p["status"]][0],
                        p["mensagens"], p["copiadas"], p["puladas"], p["erros"], p["bytes"],
                        round(p["segundos"])])
        w.writerow([])
        w.writerow(["Hora", "Tipo", "Conta", "Pasta", "Mensagem"])
        for pr in rel["problemas"]:
            w.writerow([pr["hora"], "Erro" if pr["nivel"] == "erro" else "Aviso", pr["conta"],
                        pr["pasta"], pr["texto"]])


# ------------------------------------------------------------- ponte motor
class Cancelado(BaseException):
    """Pedido de parada vindo da janela (não é capturado como erro comum)."""


class GuiUi(me.Ui):
    """Substitui a saída de terminal do motor: tudo vira evento na fila.
    Roda na thread de trabalho; a janela consome a fila na thread principal."""
    SIMBOLOS = {"info": "ℹ", "ok": "✔", "aviso": "⚠", "erro": "✖"}

    def __init__(self, fila, parar):
        self.log, self.tty, self.cor, self._barra = None, False, False, False
        self.fila, self.parar = fila, parar

    def _checar(self):
        if self.parar.is_set():
            raise Cancelado()

    def _emitir(self, texto, stream=None, nivel="texto"):
        self.fila.put(("log", nivel, texto))
        if self.log:
            simbolo = self.SIMBOLOS.get(nivel)
            self.log.write(f"{time.strftime('%H:%M:%S')} "
                           f"{f'  {simbolo} ' if simbolo else ''}{texto}\n")
            self.log.flush()

    def fechar_log(self):
        if self.log:
            self.log.close()
            self.log = None

    def texto(self, t=""):
        self._emitir(t)

    def info(self, t):
        self._emitir(t, nivel="info")

    def ok(self, t):
        self._emitir(t, nivel="ok")

    def aviso(self, t):
        self._emitir(t, nivel="aviso")

    def erro(self, t):
        self._emitir(t, nivel="erro")

    def titulo(self, t):
        self._emitir(t, nivel="titulo")

    def secao(self, t):
        self._checar()
        self._emitir(t, nivel="secao")
        self.fila.put(("pasta", t))

    def progresso(self, feito, total, inicio, bytes_=0):
        self.fila.put(("progresso", feito, total, bytes_))
        self._checar()

    def conta_iniciada(self, n, total, src_user, dst_user):
        self.fila.put(("conta", n, total, src_user, dst_user))

    def conta_concluida(self, n, status, detalhe):
        self.fila.put(("conta_ok", n, status, detalhe))

    def pasta_iniciada(self, nome, mensagens, ja_existem):
        self.fila.put(("pasta_ini", nome, mensagens, ja_existem))

    def pasta_concluida(self, linha):
        self.fila.put(("pasta_ok", [linha[0]] + [int(x) for x in linha[1:]]))


# ------------------------------------------------------- desenho e ícones
def cor_de_fundo(widget):
    """Cor de fundo do pai, para o canto de fora de um desenho arredondado sumir."""
    try:
        return widget.cget("background")
    except tk.TclError:
        estilo = widget.cget("style") or widget.winfo_class()
        return ttk.Style(widget).lookup(estilo, "background") or COR["fundo"]


def ret_arredondado(c, x0, y0, x1, y1, r, **kw):
    """Retângulo de cantos arredondados (polígono suavizado; pontos repetidos mantêm
    os lados retos)."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    p = [x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r,
         x1, y1 - r, x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1,
         x0, y1, x0, y1 - r, x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0]
    return c.create_polygon(p, smooth=True, **kw)


def _rgb(cor):
    return tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))


def _dist_seg(x, y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def _dentro_arredondado(x, y, x0, y0, x1, y1, r):
    cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _forma(tipo, x, y):
    """Cor do ícone no ponto (x, y), de 0 a 1; None é transparente."""
    if tipo == "ok":
        d = min(_dist_seg(x, y, .18, .52, .41, .75), _dist_seg(x, y, .41, .75, .84, .29))
        return COR["ok_texto"] if d < .085 else None
    if tipo == "aviso":
        a, b, c = (.5, .1), (.95, .88), (.05, .88)
        lado = lambda p, q: (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])  # noqa: E731
        dentro = lado(a, b) >= 0 and lado(b, c) >= 0 and lado(c, a) >= 0
        borda = min(_dist_seg(x, y, *a, *b), _dist_seg(x, y, *b, *c), _dist_seg(x, y, *c, *a))
        exclamacao = _dist_seg(x, y, .5, .4, .5, .6) < .06 or math.hypot(x - .5, y - .74) < .06
        return "#c08a00" if (dentro and borda < .09) or exclamacao else None
    if tipo == "andamento":
        return COR["destaque"] if math.hypot(x - .5, y - .5) < .26 else None
    if tipo == "pendente":
        return COR["muda"] if .18 < math.hypot(x - .5, y - .5) < .28 else None
    if tipo == "parada":
        return COR["aviso"] if _dentro_arredondado(x, y, .27, .27, .73, .73, .08) else None
    if tipo in ("falha", "erro"):
        d = min(_dist_seg(x, y, .25, .25, .75, .75), _dist_seg(x, y, .75, .25, .25, .75))
        return COR["erro"] if d < .085 else None
    if tipo == "check_on":
        if not _dentro_arredondado(x, y, .08, .08, .92, .92, .2):
            return None
        d = min(_dist_seg(x, y, .27, .52, .44, .69), _dist_seg(x, y, .44, .69, .75, .35))
        return "#ffffff" if d < .075 else COR["destaque"]
    if tipo == "check_off":
        if not _dentro_arredondado(x, y, .08, .08, .92, .92, .2):
            return None
        return "#ffffff" if _dentro_arredondado(x, y, .16, .16, .84, .84, .13) else "#a9a9a3"
    return None


def _png(tam, tipo, amostras=4):
    """PNG RGBA do ícone, com antisserrilhado por superamostragem."""
    linhas = []
    for py in range(tam):
        linha = bytearray(b"\x00")
        for px_ in range(tam):
            soma, n = [0, 0, 0], 0
            for sy in range(amostras):
                for sx in range(amostras):
                    cor = _forma(tipo, (px_ + (sx + .5) / amostras) / tam,
                                 (py + (sy + .5) / amostras) / tam)
                    if cor:
                        n += 1
                        for i, v in enumerate(_rgb(cor)):
                            soma[i] += v
            linha += bytes([s // n for s in soma] + [255 * n // amostras ** 2]) if n else b"\0" * 4
        linhas.append(bytes(linha))

    def bloco(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", tam, tam, 8, 6, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(b"".join(linhas))) + bloco(b"IEND", b""))


def criar_icones(tam):
    return {t: tk.PhotoImage(data=base64.b64encode(_png(tam, t)), format="png")
            for t in ("ok", "aviso", "andamento", "pendente", "parada", "falha", "erro",
                      "check_on", "check_off")}


def dica(canvas, x, y, texto):
    """Caixa de tooltip que nunca sai da área do canvas."""
    t = canvas.create_text(0, 0, text=texto, anchor="nw", fill=COR["tinta"],
                           font=(FONTE, 9), tags="hover")
    x0, y0, x1, y1 = canvas.bbox(t)
    w, h = x1 - x0 + 16, y1 - y0 + 10
    cw = canvas.winfo_width()
    topo = canvas.canvasy(0)
    x = x + 12 if x + 12 + w < cw else x - 12 - w
    y = max(topo + 4, min(y - h / 2, topo + canvas.winfo_height() - h - 4))
    canvas.coords(t, x + 8, y + 5)
    r = ret_arredondado(canvas, x, y, x + w, y + h, 6, fill=COR["superficie"],
                        outline=COR["eixo"], tags="hover")
    canvas.tag_lower(r, t)


# -------------------------------------------------------------- componentes
class CanvasFluido(tk.Canvas):
    """Canvas que redesenha uma vez só, depois que o redimensionamento assenta.
    Desenhar num canvas sai bem mais barato que vários widgets: no Windows cada
    widget é uma janela do sistema, e todas são refeitas a cada mudança de layout."""

    def __init__(self, pai, px, altura, fundo=COR["superficie"]):
        super().__init__(pai, bg=fundo, highlightthickness=0, height=px(altura))
        self.px = px
        self._pendente = None
        self.bind("<Configure>", self._agendar)

    def _agendar(self, _=None):
        if self._pendente:
            self.after_cancel(self._pendente)
        self._pendente = self.after(40, self._redesenhar)

    def _redesenhar(self):
        self._pendente = None
        self.desenhar()


class Cartao(tk.Frame):
    """Cartão de cantos arredondados: o desenho fica num canvas atrás do conteúdo,
    e o conteúdo (self.corpo) entra recuado o bastante para não cobrir os cantos."""

    def __init__(self, pai, px, padding=16, preenchimento=None, borda=None, raio=10):
        fundo = cor_de_fundo(pai)
        super().__init__(pai, bg=fundo)
        self.raio = px(raio)
        self.cor = preenchimento or COR["superficie"]
        self.cor_borda = borda or COR["borda"]
        self._c = tk.Canvas(self, bg=fundo, highlightthickness=0, bd=0)
        self._c.place(x=0, y=0, relwidth=1, relheight=1)
        recuo = int(self.raio * 0.3) + 2  # fora do arco do canto
        interno = max(0, px(padding) - recuo)
        self.corpo = tk.Frame(self, bg=self.cor, padx=interno, pady=interno)
        self.corpo.pack(fill="both", expand=True, padx=recuo, pady=recuo)
        self._c.bind("<Configure>", lambda e: self._desenhar())

    def _desenhar(self):
        c = self._c
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w > 4 and h > 4:
            ret_arredondado(c, 0.5, 0.5, w - 1, h - 1, self.raio, fill=self.cor,
                            outline=self.cor_borda)

    def recolorir(self, preenchimento, borda):
        self.cor, self.cor_borda = preenchimento, borda
        self.corpo.configure(bg=preenchimento)
        self._desenhar()


class Botao(tk.Canvas):
    """Botão de cantos arredondados (o ttk.Button do Tk não arredonda)."""
    ESTILOS = {  # fundo, hover, pressionado, texto, borda (None = cor do fundo do pai)
        "primario": (COR["destaque"], "#2569bd", COR["destaque_esc"], "#ffffff", None),
        "secundario": (COR["superficie"], "#f2f6fc", "#e6eefa", COR["tinta"], COR["borda_campo"]),
        "perigo": (COR["superficie"], "#fdf0f0", "#fae3e3", COR["erro_texto"], "#ebc7c7"),
        "discreto": (None, "#ebebe7", "#e1e1dc", COR["tinta2"], None),
    }

    def __init__(self, pai, px, texto, comando=None, estilo="secundario", grande=False):
        self.px, self.texto, self.comando, self.estilo = px, texto, comando, estilo
        self._fundo_pai = cor_de_fundo(pai)
        self._hover = self._pressionado = self._desligado = False
        self.fonte = tkfont.Font(family=FONTE_SEMI if estilo == "primario" else FONTE,
                                 size=11 if grande else 9 if estilo == "discreto" else 10)
        self.alt = px(42 if grande else 30 if estilo == "discreto" else 34)
        self.margem = px(22 if grande else 10 if estilo == "discreto" else 15)
        super().__init__(pai, width=self._largura(), height=self.alt, bg=self._fundo_pai,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self._foco = False
        for tecla in ("<Return>", "<KP_Enter>", "<space>"):  # também funciona pelo teclado
            self.bind(tecla, self._tecla)
        self.bind("<FocusIn>", lambda e: self._mudar(foco=True))
        self.bind("<FocusOut>", lambda e: self._mudar(foco=False))
        self.bind("<Enter>", lambda e: self._mudar(hover=True))
        # como no botão do Windows: sair com o mouse apertado não cancela; ao soltar,
        # o clique só vale se o ponteiro estiver em cima do botão
        self.bind("<Leave>", lambda e: self._mudar(hover=False))
        self.bind("<ButtonPress-1>", lambda e: self._mudar(pressionado=True))
        self.bind("<ButtonRelease-1>", self._soltar)
        self._desenhar()

    def _largura(self):
        return self.fonte.measure(self.texto) + 2 * self.margem

    def _mudar(self, **estado):
        if self._desligado:
            return
        for k, v in estado.items():
            setattr(self, f"_{k}", v)
        self._desenhar()

    def _soltar(self, e):
        clicou = self._pressionado and 0 <= e.x < self._largura() and 0 <= e.y < self.alt
        self._pressionado = False
        if clicou:
            self.ativar()
        elif self.winfo_exists():
            self._desenhar()

    def _tecla(self, _):
        self.ativar()
        return "break"  # não repassa o Enter para a janela (senão o comando roda duas vezes)

    def ativar(self):
        if not self._desligado and self.comando:
            self.comando()
        if self.winfo_exists():  # o comando pode ter fechado a janela do botão
            self._desenhar()

    def _desenhar(self):
        self.delete("all")
        fundo, hover, pressionado, texto, borda = self.ESTILOS[self.estilo]
        fundo = fundo or self._fundo_pai
        if self._desligado:
            cor = "#a9c6ec" if self.estilo == "primario" else fundo
            tinta = "#ffffff" if self.estilo == "primario" else COR["muda"]
        else:
            cor = pressionado if self._pressionado else hover if self._hover else fundo
            tinta = texto
        w, h = self._largura(), self.alt
        if self._foco and not self._desligado:  # contorno de foco, para quem usa o teclado
            ret_arredondado(self, 1, 1, w - 2, h - 2, self.px(8), fill=cor,
                            outline=COR["destaque_esc"], width=2)
        else:
            ret_arredondado(self, 0.5, 0.5, w - 1, h - 1, self.px(8), fill=cor,
                            outline=borda or cor)
        self.create_text(w / 2, h / 2, text=self.texto, fill=tinta, font=self.fonte)

    def configure(self, cnf=None, **kw):
        kw.update(cnf or {})
        mudou = "text" in kw
        if mudou:
            self.texto = kw.pop("text")
        if "command" in kw:
            self.comando = kw.pop("command")
        if kw:
            super().configure(**kw)
        if mudou:
            super().configure(width=self._largura())
            self._desenhar()

    config = configure

    def cget(self, chave):
        return self.texto if chave == "text" else super().cget(chave)

    def state(self, estados=()):
        for e in estados:
            if e in ("disabled", "!disabled"):
                self._desligado = e == "disabled"
        self._hover = self._pressionado = False
        super().configure(cursor="" if self._desligado else "hand2")
        self._desenhar()

    def fundo(self, cor):
        self._fundo_pai = cor
        super().configure(bg=cor)
        self._desenhar()


class Campo(tk.Frame):
    """Campo de texto (ou combobox) com moldura arredondada que acende no foco."""

    def __init__(self, pai, px, var, show=None, valores=None, largura=None):
        fundo = cor_de_fundo(pai)
        super().__init__(pai, bg=fundo)
        self.px, self._foco = px, False
        self._c = tk.Canvas(self, bg=fundo, highlightthickness=0, bd=0)
        self._c.place(x=0, y=0, relwidth=1, relheight=1)
        if valores is not None:
            self.entrada = ttk.Combobox(self, textvariable=var, values=valores,
                                        style="Campo.TCombobox", font=(FONTE, 10),
                                        width=largura or 20)
            self.entrada.pack(fill="both", expand=True, padx=px(5), pady=px(4))
        else:
            self.entrada = tk.Entry(self, textvariable=var, show=show or "", bd=0,
                                    relief="flat", highlightthickness=0, bg=COR["superficie"],
                                    fg=COR["tinta"], insertbackground=COR["tinta"],
                                    font=(FONTE, 10), width=largura or 20)
            self.entrada.pack(fill="both", expand=True, padx=px(10), pady=px(7))
        self.entrada.bind("<FocusIn>", lambda e: self._focar(True), add="+")
        self.entrada.bind("<FocusOut>", lambda e: self._focar(False), add="+")
        self._c.bind("<Configure>", lambda e: self._desenhar())
        self._c.bind("<Button-1>", lambda e: self.entrada.focus_set())

    def _focar(self, sim):
        self._foco = sim
        self._desenhar()

    def _desenhar(self):
        c = self._c
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w > 4:
            ret_arredondado(c, 0.5, 0.5, w - 1, h - 1, self.px(7), fill=COR["superficie"],
                            outline=COR["destaque"] if self._foco else COR["borda_campo"],
                            width=2 if self._foco else 1)


class Pilula(tk.Canvas):
    """Selo de status com pontas totalmente arredondadas."""

    def __init__(self, pai, px):
        self.px = px
        self.fonte = tkfont.Font(family=FONTE_SEMI, size=10)
        super().__init__(pai, height=px(32), width=px(100), bg=cor_de_fundo(pai),
                         highlightthickness=0, bd=0)

    def definir(self, texto, fundo, tinta):
        w, h = self.fonte.measure(texto) + self.px(32), self.px(32)
        self.configure(width=w)
        self.delete("all")
        ret_arredondado(self, 0.5, 0.5, w - 1, h - 1, h / 2, fill=fundo, outline=fundo)
        self.create_text(w / 2, h / 2, text=texto, fill=tinta, font=self.fonte)


class Tabela(Cartao):
    """Tabela num cartão arredondado: linhas altas, cabeçalho cinza, ícone colorido
    só na primeira coluna e a linha em andamento destacada."""

    def __init__(self, pai, app, colunas, altura=5, selecao=True):
        super().__init__(pai, app.px, padding=0)
        self.icones = app.icones
        self.com_icone = colunas[0][0] == "#0"
        self.colunas = [c[0] for c in colunas if c[0] != "#0"]
        self.tv = ttk.Treeview(self.corpo, columns=self.colunas, height=altura,
                               show="tree headings" if self.com_icone else "headings",
                               selectmode="browse" if selecao else "none")
        self._larguras = {cid: app.px(larg) for cid, _, larg, _, _ in colunas}
        self._elasticas = {cid for cid, _, _, _, estica in colunas if estica}
        self._minimo, self.px_min = app.px(70), app.px(30)
        for cid, titulo, larg, anc, _ in colunas:
            self.tv.heading(cid, text=titulo, anchor=anc)
            self.tv.column(cid, width=app.px(larg), minwidth=app.px(30), anchor=anc,
                           stretch=False)  # as larguras são distribuídas em _ajustar
        barra = ttk.Scrollbar(self.corpo, orient="vertical", command=self.tv.yview,
                              style="Fino.Vertical.TScrollbar")
        self.tv.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Configure>", lambda e: self._ajustar(), add="+")
        self.tv.tag_configure("par", background=COR["zebra"])
        self.tv.tag_configure("ativa", background=COR["destaque_claro"])
        self.tv.tag_configure("muda", foreground=COR["muda"])
        self.n = 0

    def _item(self, valores):
        if not self.com_icone:
            return {"values": valores}
        (icone, texto), resto = valores[0], valores[1:]
        return {"text": f"  {texto}", "image": self.icones.get(icone, ""), "values": resto}

    def _tags(self, tags, par):
        # a linha ativa tem fundo próprio; a zebra só entra nas outras
        return (("par",) if par and "ativa" not in tags else ()) + tuple(tags)

    def limpar(self):
        self.tv.delete(*self.tv.get_children())
        self.n = 0

    def no_fim(self):
        return self.tv.yview()[1] >= 0.999

    def inserir(self, valores, tags=(), iid=None, rolar=False):
        """rolar=True acompanha o fim só se o usuário já estava no fim (não tira do lugar)."""
        acompanhar = rolar and self.no_fim()
        iid = self.tv.insert("", "end", iid=iid, tags=self._tags(tags, self.n % 2),
                             **self._item(valores))
        self.n += 1
        if acompanhar:
            self.tv.see(iid)
        return iid

    def atualizar(self, iid, valores, tags=()):
        par = self.tv.index(iid) % 2
        self.tv.item(iid, tags=self._tags(tags, par), **self._item(valores))

    def colunas_visiveis(self, ids):
        self.tv.configure(displaycolumns=[i for i in ids if i != "#0"])
        self._ajustar()

    def _ajustar(self):
        """Todas as colunas visíveis cabem na largura da tabela: as de número mantêm a
        largura, a de texto fica com o que sobra; se nem assim couber, tudo encolhe."""
        w = self.tv.winfo_width()
        if w < 50:
            return
        exibidas = self.tv["displaycolumns"]
        exibidas = self.colunas if not exibidas or exibidas[0] == "#all" else list(exibidas)
        visiveis = (["#0"] if self.com_icone else []) + list(exibidas)
        fixas = [c for c in visiveis if c not in self._elasticas]
        elasticas = [c for c in visiveis if c in self._elasticas]
        sobra = w - sum(self._larguras[c] for c in fixas)
        if elasticas and sobra >= self._minimo * len(elasticas):
            base = sum(self._larguras[c] for c in elasticas)
            for c in fixas:
                self.tv.column(c, width=self._larguras[c])
            for c in elasticas:
                self.tv.column(c, width=int(sobra * self._larguras[c] / base))
        else:
            fator = w / (sum(self._larguras[c] for c in visiveis) or 1)
            for c in visiveis:
                self.tv.column(c, width=max(self.px_min, int(self._larguras[c] * fator)))


class Abas(tk.Frame):
    """Abas em formato de pílula; só a aba visível ocupa a tela."""

    def __init__(self, pai, px):
        fundo = cor_de_fundo(pai)
        super().__init__(pai, bg=fundo)
        self.px = px
        self.fonte = tkfont.Font(family=FONTE, size=10)
        self.cab = tk.Canvas(self, height=px(34), bg=fundo, highlightthickness=0)
        self.cab.pack(fill="x")
        self.conteudo = tk.Frame(self, bg=fundo)
        self.conteudo.pack(fill="both", expand=True, pady=(px(8), 0))
        self.textos, self.quadros, self.ocultas = {}, {}, set()
        self.atual = self.hover = None
        self.zonas = []
        self.cab.bind("<Configure>", lambda e: self._desenhar())
        self.cab.bind("<Motion>", self._mover)
        self.cab.bind("<Leave>", lambda e: self._mover(None))
        self.cab.bind("<Button-1>", self._clique)

    def _em(self, x):
        return next((k for a, b, k in self.zonas if a <= x <= b), None)

    def _clique(self, e):
        chave = self._em(e.x)
        if chave:
            self.mostrar(chave)

    def _mover(self, e):
        chave = self._em(e.x) if e else None
        self.cab.configure(cursor="hand2" if chave else "")
        if chave != self.hover:
            self.hover = chave
            self._desenhar()

    def _desenhar(self):
        c, px = self.cab, self.px
        c.delete("all")
        h = c.winfo_height()
        x, self.zonas = 0, []
        for chave, texto in self.textos.items():
            if chave in self.ocultas:
                continue
            w = self.fonte.measure(texto) + px(28)
            sel, hov = chave == self.atual, chave == self.hover
            if sel:
                ret_arredondado(c, x + 0.5, 0.5, x + w, h - 1, px(9), fill=COR["superficie"],
                                outline=COR["borda"])
            elif hov:
                ret_arredondado(c, x, 0, x + w, h - 1, px(9), fill="#ececea", outline="")
            c.create_text(x + w / 2, h / 2, text=texto, font=self.fonte,
                          fill=COR["tinta"] if sel or hov else COR["tinta2"])
            self.zonas.append((x, x + w, chave))
            x += w + px(4)

    def adicionar(self, chave, titulo):
        self.textos[chave] = titulo
        self.quadros[chave] = tk.Frame(self.conteudo, bg=self.conteudo.cget("bg"))
        if self.atual is None:
            self.mostrar(chave)
        else:
            self._desenhar()
        return self.quadros[chave]

    def mostrar(self, chave):
        if self.atual and self.atual != chave:
            self.quadros[self.atual].pack_forget()
        self.atual = chave
        self.quadros[chave].pack(fill="both", expand=True)
        self._desenhar()

    def titulo(self, chave, texto):
        if self.textos[chave] != texto:
            self.textos[chave] = texto
            self._desenhar()

    def visivel(self, chave, sim):
        if sim:
            self.ocultas.discard(chave)
        else:
            self.ocultas.add(chave)
            if self.atual == chave:
                self.mostrar(next(k for k in self.textos if k not in self.ocultas))
        self._desenhar()


class PainelKpi(CanvasFluido):
    """Fileira de indicadores em cartões arredondados, num só canvas."""

    def __init__(self, pai, px, itens):  # itens: [(chave, rótulo)]
        super().__init__(pai, px, 84, fundo=cor_de_fundo(pai))
        self.itens = itens
        self.valores = {chave: ("—", COR["tinta"]) for chave, _ in itens}

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        vao = px(12)
        larg = (w - vao * (len(self.itens) - 1)) / len(self.itens)
        for i, (chave, rotulo) in enumerate(self.itens):
            x0 = i * (larg + vao)
            ret_arredondado(self, x0 + 0.5, 0.5, x0 + larg - 1, h - 1, px(10),
                            fill=COR["superficie"], outline=COR["borda"])
            self.create_text(x0 + px(16), px(23), anchor="w", text=rotulo, fill=COR["muda"],
                             font=(FONTE, 9))
            valor, cor = self.valores[chave]
            self.create_text(x0 + px(16), px(54), anchor="w", text=valor, fill=cor,
                             font=(FONTE_SEMI, 17), tags=f"v_{chave}")

    def set(self, chave, valor, cor=None):
        cor = cor or COR["tinta"]
        if self.valores[chave] == (valor, cor):  # sem redesenho à toa
            return
        self.valores[chave] = (valor, cor)
        self.itemconfigure(f"v_{chave}", text=valor, fill=cor)


class PainelProgresso(CanvasFluido):
    """Barras finas de ponta arredondada que deslizam até o valor novo.
    A animação roda a ~60 quadros/s só enquanto alguma barra está se movendo."""
    SUAVIDADE = 6.0  # por segundo: quanto maior, mais depressa a barra alcança o valor

    def __init__(self, pai, px, linhas, cartao=True):  # linhas: [(chave, cor)]
        self.cartao = cartao
        super().__init__(pai, px, (22 if cartao else 2) + 47 * len(linhas),
                         fundo=cor_de_fundo(pai))
        self.ordem = [chave for chave, _ in linhas]
        self.linhas = {chave: {"cor": cor, "rotulo": "", "direita": "", "valor": 0.0,
                               "alvo": 0.0, "indet": False, "fase": 0.0, "barra": True}
                       for chave, cor in linhas}
        self.geo = {}
        self._loop = None
        self._t = 0.0

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if self.cartao:
            ret_arredondado(self, 0.5, 0.5, w - 1, h - 1, px(10), fill=COR["superficie"],
                            outline=COR["borda"])
        margem = px(18) if self.cartao else 0
        y = px(20) if self.cartao else px(2)
        alt = px(8)
        for chave in self.ordem:
            L = self.linhas[chave]
            self.create_text(margem, y + px(8), anchor="w", text=L["rotulo"], fill=COR["tinta"],
                             font=(FONTE_SEMI, 10), tags=f"r_{chave}")
            self.create_text(w - margem, y + px(8), anchor="e", text=L["direita"],
                             fill=COR["muda"], font=(FONTE, 9), tags=f"d_{chave}")
            yb = y + px(28)
            x0, x1 = margem + alt / 2, w - margem - alt / 2
            self.create_line(x0, yb, x1, yb, width=alt, capstyle="round", fill=COR["trilho"],
                             tags=f"t_{chave}")
            self.create_line(x0, yb, x0, yb, width=alt, capstyle="round", fill=L["cor"],
                             tags=f"f_{chave}")
            self.geo[chave] = (x0, x1, yb)
            self._pintar(chave)
            y += px(47)

    def _pintar(self, chave):
        if chave not in self.geo:
            return
        x0, x1, y = self.geo[chave]
        L = self.linhas[chave]
        self.itemconfigure(f"t_{chave}", state="normal" if L["barra"] else "hidden")
        if L["indet"]:  # vai e volta, desacelerando nas pontas
            seg = 0.3
            p = (math.sin(L["fase"] * 2.4 - math.pi / 2) + 1) / 2 * (1 - seg)
            a, b = x0 + (x1 - x0) * p, x0 + (x1 - x0) * (p + seg)
        else:
            a, b = x0, x0 + (x1 - x0) * min(1.0, L["valor"])
        visivel = L["barra"] and (L["indet"] or L["valor"] > 0.002)
        self.coords(f"f_{chave}", a, y, b, y)
        self.itemconfigure(f"f_{chave}", state="normal" if visivel else "hidden")

    def definir(self, chave, rotulo=None, direita=None):
        L = self.linhas[chave]
        if rotulo is not None and rotulo != L["rotulo"]:
            L["rotulo"] = rotulo
            self.itemconfigure(f"r_{chave}", text=rotulo)
        if direita is not None and direita != L["direita"]:
            L["direita"] = direita
            self.itemconfigure(f"d_{chave}", text=direita)

    def barra_visivel(self, chave, sim):
        self.linhas[chave]["barra"] = sim
        self._pintar(chave)

    def alvo(self, chave, valor, instantaneo=False):
        L = self.linhas[chave]
        valor = max(0.0, min(1.0, valor))
        L["indet"] = False
        if instantaneo or valor < L["valor"] - 0.02:  # recomeço (pasta nova): sem deslizar para trás
            L["valor"] = valor
        L["alvo"] = valor
        self._pintar(chave)
        self._animar()

    def indeterminado(self, chave):
        L = self.linhas[chave]
        L["indet"], L["fase"] = True, 0.0
        self._animar()

    def _animar(self):
        if self._loop is None:
            self._t = time.perf_counter()
            self._loop = self.after(16, self._passo)

    def _passo(self):
        agora = time.perf_counter()
        dt, self._t = min(0.1, agora - self._t), agora
        aproximar = 1 - math.exp(-dt * self.SUAVIDADE)  # igual em qualquer taxa de quadros
        movendo = False
        for chave, L in self.linhas.items():
            if L["indet"]:
                L["fase"] += dt
                movendo = True
            elif abs(L["alvo"] - L["valor"]) > 0.0005:
                L["valor"] += (L["alvo"] - L["valor"]) * aproximar
                movendo = True
            else:
                L["valor"] = L["alvo"]
            self._pintar(chave)
        self._loop = self.after(16, self._passo) if movendo else None


class PainelVelocidade(CanvasFluido):
    """Cartão compacto: velocidade atual, gráfico pequeno e tempos."""
    LIMITE = 600

    def __init__(self, pai, px):
        super().__init__(pai, px, 262, fundo=cor_de_fundo(pai))
        self.pontos = []  # (segundos desde o início, bytes/s)
        self.valor = "—"
        self.info = [("Tempo decorrido", "—"), ("Tempo restante", "—"),
                     ("Pastas concluídas", "—")]
        self.xs = []
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def limpar(self):
        self.pontos, self.valor = [], "—"
        self.desenhar()

    def adicionar(self, t, v):
        self.pontos.append((t, v))
        if len(self.pontos) > self.LIMITE:  # junta pares: o gráfico cobre a execução toda
            p = self.pontos
            self.pontos = [((p[i][0] + p[i + 1][0]) / 2, (p[i][1] + p[i + 1][1]) / 2)
                           for i in range(0, len(p) - 1, 2)] + ([p[-1]] if len(p) % 2 else [])
        self.valor = f"{me.fmt_bytes(v)}/s"
        self.desenhar()

    def definir_info(self, itens):
        if itens == self.info:
            return
        self.info = itens
        for i, (rotulo, valor) in enumerate(itens):
            self.itemconfigure(f"il_{i}", text=rotulo)
            self.itemconfigure(f"iv_{i}", text=valor)

    def desenhar(self):
        px, p = self.px, self.pontos
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        m = px(16)
        ret_arredondado(self, 0.5, 0.5, w - 1, h - 1, px(10), fill=COR["superficie"],
                        outline=COR["borda"])
        self.create_text(m, px(26), anchor="w", text="Velocidade", fill=COR["tinta2"],
                         font=(FONTE, 10))
        self.create_text(w - m, px(26), anchor="e", text=self.valor, fill=COR["tinta"],
                         font=(FONTE_SEMI, 11))
        self.esq, dir_, self.topo, self.base = m + px(24), w - m, px(56), px(152)
        if len(p) < 2:
            self.create_line(self.esq, self.base, dir_, self.base, fill=COR["eixo"])
            self.create_text((self.esq + dir_) / 2, (self.topo + self.base) / 2,
                             text="Aguardando a transferência", fill=COR["muda"],
                             font=(FONTE, 9))
            self.xs = []
        else:
            self.vmax = maximo_redondo(max(v for _, v in p))
            div = 1024 ** 2 if self.vmax >= 1024 ** 2 else 1024 if self.vmax >= 1024 else 1
            for frac in (0, .5, 1):
                y = self.base - (self.base - self.topo) * frac
                self.create_line(self.esq, y, dir_, y, fill=COR["grade"])
                rot = f"{self.vmax * frac / div:.1f}".rstrip("0").rstrip(".").replace(".", ",")
                self.create_text(m, y, anchor="w", text=rot, fill=COR["muda"],
                                 font=(FONTE, 8))
            t0, t1 = p[0][0], p[-1][0]
            self.xs = [self.esq + (t - t0) / ((t1 - t0) or 1) * (dir_ - self.esq) for t, _ in p]
            ys = [self.base - v / self.vmax * (self.base - self.topo) for _, v in p]
            coords = [c for xy in zip(self.xs, ys) for c in xy]
            self.create_polygon(self.xs[0], self.base, *coords, self.xs[-1], self.base,
                                fill=COR["area"], outline="")
            self.create_line(*coords, fill=COR["destaque"], width=2)
            r = px(4)
            self.create_oval(self.xs[-1] - r, ys[-1] - r, self.xs[-1] + r, ys[-1] + r,
                             fill=COR["destaque"], outline=COR["superficie"], width=2)
        for i, (rotulo, valor) in enumerate(self.info):
            y = px(184) + i * px(25)
            self.create_text(m, y, anchor="w", text=rotulo, fill=COR["tinta2"],
                             font=(FONTE, 9), tags=f"il_{i}")
            self.create_text(w - m, y, anchor="e", text=valor, fill=COR["tinta"],
                             font=(FONTE_SEMI, 9), tags=f"iv_{i}")

    def _hover(self, e):
        self.delete("hover")
        if not self.xs or not (self.esq <= e.x <= self.xs[-1] + 4
                               and self.topo - 8 <= e.y <= self.base + 8):
            return
        i = min(range(len(self.xs)), key=lambda k: abs(self.xs[k] - e.x))
        t, v = self.pontos[i]
        x = self.xs[i]
        y = self.base - v / self.vmax * (self.base - self.topo)
        self.create_line(x, self.topo, x, self.base, fill=COR["eixo"], tags="hover")
        r = self.px(4)
        self.create_oval(x - r, y - r, x + r, y + r, fill=COR["destaque"],
                         outline=COR["superficie"], width=2, tags="hover")
        dica(self, x, y, f"{me.fmt_bytes(v)}/s\naos {me.fmt_tempo(t)} de execução")


class CartoesModo(CanvasFluido):
    """Os quatro tipos de migração como cartões arredondados clicáveis, num só canvas."""

    def __init__(self, pai, px, ao_escolher):
        super().__init__(pai, px, 78, fundo=cor_de_fundo(pai))
        self.ao_escolher = ao_escolher
        self.sel = self.hover = None
        self.zonas = []
        self.bind("<Motion>", self._mover)
        self.bind("<Leave>", lambda e: self._mover(None))
        self.bind("<Button-1>", self._clique)

    def selecionar(self, chave):
        self.sel = chave
        self.desenhar()

    def _em(self, x):
        return next((k for a, b, k in self.zonas if a <= x <= b), None)

    def _clique(self, e):
        chave = self._em(e.x)
        if chave:
            self.ao_escolher(chave)

    def _mover(self, e):
        chave = self._em(e.x) if e else None
        self.configure(cursor="hand2" if chave else "")
        if chave != self.hover:
            self.hover = chave
            self.desenhar()

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        vao = px(12)
        larg = (w - vao * (len(MODOS_GUI) - 1)) / len(MODOS_GUI)
        self.zonas = []
        for i, (chave, (titulo, desc, _)) in enumerate(MODOS_GUI.items()):
            x0 = i * (larg + vao)
            sel, hov = chave == self.sel, chave == self.hover
            fundo = COR["selecionado"] if sel else COR["hover"] if hov else COR["superficie"]
            borda = COR["destaque"] if sel else COR["eixo"] if hov else COR["borda"]
            ret_arredondado(self, x0 + 1, 1, x0 + larg - 1, h - 1, px(10), fill=fundo,
                            outline=borda, width=2 if sel else 1)
            self.create_text(x0 + px(16), px(15), anchor="nw", text=titulo,
                             fill=COR["destaque_esc"] if sel else COR["tinta"],
                             font=(FONTE_SEMI, 11))
            self.create_text(x0 + px(16), px(39), anchor="nw", text=desc, fill=COR["tinta2"],
                             font=(FONTE, 9), width=larg - px(32))
            self.zonas.append((x0, x0 + larg, chave))


class BarraLateral(tk.Canvas):
    """Navegação + andamento resumido, sempre visíveis, num só canvas."""
    ITENS = (("config", "+", "Nova migração"), ("progresso", "●", "Andamento"),
             ("historico", "≡", "Histórico"), ("opcoes", "⚙", "Opções"))
    TAMANHO_ICONE = {"+": 13, "●": 7, "≡": 13, "⚙": 11}

    def __init__(self, pai, px, ao_escolher, ao_aviso):
        super().__init__(pai, bg=COR["lateral"], highlightthickness=0, width=px(232))
        self.px, self.ao_escolher, self.ao_aviso = px, ao_escolher, ao_aviso
        self.ativo = self.hover = None
        self.situacao = ("● Pronto", "", False)
        self.aviso = None  # (título, detalhe) da atualização disponível
        self.zonas = []
        self.bind("<Configure>", lambda e: self.desenhar())
        self.bind("<Motion>", self._mover)
        self.bind("<Leave>", lambda e: self._mover(None))
        self.bind("<Button-1>", self._clique)

    def _em(self, y):
        return next((k for a, b, k in self.zonas if a <= y <= b), None)

    def _clique(self, e):
        chave = self._em(e.y)
        if chave == "aviso":
            self.ao_aviso()
        elif chave:
            self.ao_escolher(chave)

    def _mover(self, e):
        chave = self._em(e.y) if e else None
        self.configure(cursor="hand2" if chave else "")
        if chave != self.hover:
            self.hover = chave
            self.desenhar()

    def definir_aviso(self, titulo, detalhe=""):
        self.aviso = (titulo, detalhe) if titulo else None
        self.desenhar()

    def ativar(self, chave):
        self.ativo = chave
        self.desenhar()

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        # logotipo: envelope branco + nome
        x, y = px(24), px(42)
        ret_arredondado(self, x, y - px(7), x + px(18), y + px(7), px(3), fill="#ffffff",
                        outline="#ffffff")
        self.create_line(x + px(2), y - px(5), x + px(9), y + px(1), x + px(16), y - px(5),
                         fill=COR["lateral"], width=max(1, px(1.6)))
        self.create_text(x + px(29), y, anchor="w", text="Migrador", fill="#ffffff",
                         font=(FONTE_SEMI, 14))
        self.create_text(x + px(29), y + px(25), anchor="w", text="de e-mails IMAP",
                         fill=COR["lateral_mudo"], font=(FONTE, 9))
        y, alt, self.zonas = px(104), px(46), []
        for chave, icone, texto in self.ITENS:
            sel, hov = chave == self.ativo, chave == self.hover
            if sel or hov:
                ret_arredondado(self, px(14), y + px(3), w - px(14), y + alt - px(3), px(9),
                                fill=COR["lateral_ativo"] if sel else COR["lateral_hover"],
                                outline=COR["lateral_ativo"] if sel else COR["lateral_hover"])
            self.create_text(px(36), y + alt / 2, text=icone,
                             fill="#ffffff" if sel else COR["lateral_mudo"],
                             font=(FONTE, self.TAMANHO_ICONE[icone]))
            self.create_text(px(58), y + alt / 2, anchor="w", text=texto,
                             fill="#ffffff" if sel else COR["lateral_texto"],
                             font=(FONTE_SEMI if sel else FONTE, 10))
            self.zonas.append((y, y + alt, chave))
            y += alt
        if self.aviso:
            y0, y1 = h - px(132), h - px(80)
            ret_arredondado(self, px(14), y0, w - px(14), y1, px(10), outline="",
                            fill=COR["destaque_esc"] if self.hover == "aviso" else COR["destaque"])
            self.create_text(px(28), y0 + px(17), anchor="w", text=self.aviso[0],
                             fill="#ffffff", font=(FONTE_SEMI, 9))
            self.create_text(px(28), y0 + px(35), anchor="w", text=self.aviso[1],
                             fill="#ffffff", font=(FONTE, 8))
            self.zonas.append((y0, y1, "aviso"))
        estado, detalhe, ativo = self.situacao
        self.create_text(px(24), h - px(60), anchor="w", text=estado, tags="estado",
                         fill="#ffffff" if ativo else COR["lateral_texto"],
                         font=(FONTE_SEMI, 9))
        self.create_text(px(24), h - px(40), anchor="w", text=detalhe, tags="detalhe",
                         fill=COR["lateral_texto"], font=(FONTE, 9))
        self.create_text(px(24), h - px(16), anchor="w", fill=COR["lateral_mudo"],
                         font=(FONTE, 8), text=f"versão {VERSAO}" if atualizador.empacotado()
                         else "versão de desenvolvimento")

    def estado(self, estado, detalhe="", ativo=False):
        if (estado, detalhe, ativo) == self.situacao:
            return
        self.situacao = (estado, detalhe, ativo)
        self.itemconfigure("estado", text=estado,
                           fill="#ffffff" if ativo else COR["lateral_texto"])
        self.itemconfigure("detalhe", text=detalhe)


class CartaoServidor(Cartao):
    """Servidor IMAP + usuário + senha + botão de teste."""

    def __init__(self, app, pai, lado, titulo, subtitulo):
        super().__init__(pai, app.px)
        self.app, self.lado = app, lado
        px, v, corpo = app.px, app.v, self.corpo
        corpo.columnconfigure((0, 1), weight=1, uniform="campo")
        ttk.Label(corpo, text=titulo, style="CartaoTitulo.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(corpo, text=subtitulo, style="CartaoMudo.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w")

        app.rotulo(corpo, "Servidor IMAP", 2, 0, 2)
        app.rotulo(corpo, "Porta", 2, 2)
        Campo(corpo, px, v[f"{lado}_host"], valores=SERVIDORES).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=(0, px(8)))
        Campo(corpo, px, v[f"{lado}_port"], largura=6).grid(row=3, column=2, sticky="ew")

        self.credenciais = [app.rotulo(corpo, "Usuário (e-mail)", 4, 0),
                            app.rotulo(corpo, "Senha (ou senha de app)", 4, 1, 2)]
        usuario = Campo(corpo, px, v[f"{lado}_user"])
        usuario.grid(row=5, column=0, sticky="ew", padx=(0, px(8)))
        linha = tk.Frame(corpo, bg=COR["superficie"])
        linha.grid(row=5, column=1, columnspan=2, sticky="ew")
        linha.columnconfigure(0, weight=1)
        self.senha = Campo(linha, px, v[f"{lado}_pass"], show="•")
        self.senha.grid(row=0, column=0, sticky="ew")
        self.olho = Botao(linha, px, "Mostrar", self._alternar_senha, "discreto")
        self.olho.grid(row=0, column=1, padx=(px(6), 0))
        self.credenciais += [usuario, linha]
        self.nota_lote = ttk.Label(corpo, style="CartaoMudo.TLabel",
                                   text="Usuários e senhas vêm do arquivo CSV.")
        self.nota_lote.grid(row=4, column=0, columnspan=3, sticky="w", pady=(px(10), 0))

        rodape = tk.Frame(corpo, bg=COR["superficie"])
        rodape.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(px(14), 0))
        rodape.columnconfigure(1, weight=1)
        self.botao = Botao(rodape, px, "Testar conexão", lambda: app.testar_conexao(lado))
        self.botao.grid(row=0, column=0, sticky="w")
        self.status = ttk.Label(rodape, text="", style="Cartao.TLabel", wraplength=px(260))
        self.status.grid(row=0, column=1, sticky="w", padx=(px(10), 0))

    def _alternar_senha(self):
        oculta = self.senha.entrada.cget("show") == "•"
        self.senha.entrada.configure(show="" if oculta else "•")
        self.olho.configure(text="Ocultar" if oculta else "Mostrar")

    def modo_lote(self, lote):
        for w in self.credenciais:
            w.grid_remove() if lote else w.grid()
        self.nota_lote.grid() if lote else self.nota_lote.grid_remove()
        self.botao.grid_remove() if lote else self.botao.grid()
        self.mostrar_status("")

    def mostrar_status(self, texto, tipo="info"):
        estilo = {"ok": "CartaoOk.TLabel", "erro": "CartaoErro.TLabel"}.get(tipo, "CartaoMudo.TLabel")
        self.status.configure(text=texto, style=estilo)


def notas_legiveis(texto):
    """Notas geradas pelo GitHub vêm em Markdown; aqui só o essencial, em texto."""
    linhas = []
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("**Full Changelog**"):
            continue
        linha = re.sub(r"^#+\s*", "", linha)
        linha = re.sub(r"^[*-]\s+", "• ", linha)
        linha = re.sub(r" by @\S+ in https://\S+$", "", linha)  # autor e link do PR
        for ingles, portugues in (("What's Changed", "O que mudou"),
                                  ("New Contributors", "Novos colaboradores")):
            linha = linha.replace(ingles, portugues)
        linhas.append(linha.replace("**", ""))
    return "\n".join(linhas) or "Sem notas para esta versão."


class DialogoAtualizacao(tk.Toplevel):
    """Mostra a versão nova, baixa com progresso e oferece reiniciar."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        px = app.px
        self.title("Atualização do Migrador")
        self.configure(bg=COR["superficie"], padx=px(26), pady=px(22))
        self.transient(app)
        self.resizable(False, False)
        self.titulo = tk.Label(self, bg=COR["superficie"], fg=COR["tinta"],
                               font=(FONTE_SEMI, 14), anchor="w")
        self.titulo.pack(fill="x")
        self.sub = tk.Label(self, bg=COR["superficie"], fg=COR["tinta2"], font=(FONTE, 10),
                            anchor="w", justify="left", wraplength=px(480))
        self.sub.pack(fill="x", pady=(px(2), px(14)))
        tk.Label(self, text="Novidades", bg=COR["superficie"], fg=COR["tinta2"],
                 font=(FONTE_SEMI, 9), anchor="w").pack(fill="x")
        caixa = Cartao(self, px, padding=12, preenchimento=COR["fundo"])
        caixa.pack(fill="both", pady=(px(6), px(14)))
        self.notas = tk.Text(caixa.corpo, height=8, width=60, wrap="word", bd=0,
                             bg=COR["fundo"], fg=COR["tinta"], font=(FONTE, 9),
                             highlightthickness=0)
        self.notas.pack(fill="both")
        self.barra = PainelProgresso(self, px, (("download", COR["destaque"]),), cartao=False)
        self.estado = tk.Label(self, bg=COR["superficie"], fg=COR["tinta2"], font=(FONTE, 9),
                               anchor="w", justify="left", wraplength=px(480))
        self.estado.pack(fill="x")
        botoes = tk.Frame(self, bg=COR["superficie"])
        botoes.pack(fill="x", pady=(px(16), 0))
        self.btn_principal = Botao(botoes, px, "Atualizar agora", None, "primario")
        self.btn_principal.pack(side="right")
        self.btn_depois = Botao(botoes, px, "Depois", self.destroy)
        self.btn_depois.pack(side="right", padx=(0, px(8)))
        self.btn_site = Botao(botoes, px, "Abrir página da versão", self._abrir_site,
                              "discreto")
        self.atualizar()
        self.update_idletasks()  # centraliza sobre a janela principal (pelo tamanho pedido:
        # antes de aparecer na tela, a largura real ainda é 1)
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.bind("<Return>", lambda e: self.btn_principal.ativar())  # Enter = botão principal
        self.bind("<Escape>", lambda e: self.destroy())
        self.btn_principal.focus_set()

    def _abrir_site(self):
        import webbrowser
        webbrowser.open((self.app.atualizacao or {}).get("pagina") or atualizador.PAGINA)

    def _notas(self, texto):
        self.notas.configure(state="normal")
        self.notas.delete("1.0", "end")
        self.notas.insert("1.0", texto)
        self.notas.configure(state="disabled")

    def atualizar(self, erro=None):
        app, info, pronta = self.app, self.app.atualizacao, self.app.atualizacao_pronta
        versao_nova = (pronta or info or {}).get("versao", "?")
        self._notas(notas_legiveis(info["notas"]) if info else
                    "A versão nova já foi baixada e conferida.")
        self.btn_site.pack_forget()
        if erro:
            self.barra.pack_forget()
            self.titulo.configure(text="Não foi possível baixar")
            self.estado.configure(text=erro, fg=COR["erro_texto"])
            self.btn_site.pack(side="left")
            self.btn_principal.configure(text="Tentar de novo", command=app.baixar_atualizacao)
            self.btn_principal.state(["!disabled"])
            return
        self.estado.configure(fg=COR["tinta2"])
        if pronta:
            self.barra.pack_forget()
            self.titulo.configure(text="Atualização pronta para instalar")
            self.sub.configure(text=f"A versão {versao_nova} foi baixada. Ao reiniciar, o "
                                    "programa já abre atualizado. Suas configurações e o "
                                    "histórico continuam.")
            self.btn_principal.configure(text="Reiniciar agora",
                                         command=app.reiniciar_para_atualizar)
            if app.rodando:
                self.btn_principal.state(["disabled"])
                self.estado.configure(text="Há uma migração em andamento: a atualização será "
                                           "instalada sozinha quando você fechar o programa.")
            else:
                self.btn_principal.state(["!disabled"])
                self.estado.configure(text="Se preferir, clique em Depois: ela é instalada "
                                           "sozinha quando você fechar o programa.")
        elif app.baixando:
            self.titulo.configure(text=f"Baixando a versão {versao_nova}…")
            self.barra.definir("download", "Download", "")
            self.barra.pack(fill="x", pady=(0, app.px(6)), before=self.estado)
            self.btn_principal.configure(text="Baixando…")
            self.btn_principal.state(["disabled"])
        else:
            self.titulo.configure(text="Nova versão disponível")
            self.sub.configure(text=f"Versão {versao_nova} · você está na {VERSAO}")
            self.estado.configure(text="O download leva poucos segundos e não interrompe "
                                       "uma migração em andamento.")
            self.btn_principal.configure(text="Atualizar agora", command=app.baixar_atualizacao)
            self.btn_principal.state(["!disabled"])

    def progresso(self, feito, total):
        self.barra.alvo("download", feito / total if total else 0)
        self.barra.definir("download", direita=f"{me.fmt_bytes(feito)} de {me.fmt_bytes(total)}"
                           if total else me.fmt_bytes(feito))


# ---------------------------------------------------------------- aplicativo
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.escala = self.winfo_fpixels("1i") / 96
        self.titulo_base = TITULO
        self.title(self.titulo_base)
        self.geometry(f"{self.px(1260)}x{min(self.px(880), self.winfo_screenheight() - 80)}")
        self.minsize(self.px(1060), self.px(700))
        self.configure(bg=COR["fundo"])

        self.fila, self.parar = queue.Queue(), threading.Event()
        self.ui = GuiUi(self.fila, self.parar)
        me.ui = self.ui  # o motor passa a falar com a janela
        self.rodando = False
        self.rel = None  # relatório da execução atual (vai para o histórico)
        self.pastas_origem = {}  # nome bruto -> [selecionada, mensagens]
        self.contas_csv = []  # [(src_user, dst_user)]
        self.pagina_atual = None
        self.atualizacao = None  # versão nova publicada (dict do atualizador)
        self.atualizacao_pronta = None  # já baixada e conferida, esperando reiniciar
        self.baixando = False
        self.dialogo = None
        self.icones = criar_icones(self.px(16))
        try:  # ícone da janela e da barra de tarefas (o do arquivo .exe vem do build)
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            self._icone = tk.PhotoImage(file=os.path.join(base, "icone.png"))
            self.iconphoto(True, self._icone)
        except tk.TclError:
            pass

        self.v = {k: tk.StringVar() for k in CAMPOS_PERFIL + [
            "src_pass", "dst_pass", "smtp_pass"]}
        padroes = {"modo": "imap2imap", "src_port": "993", "dst_port": "993",
                   "timeout": "60", "log": LOG_PADRAO, "smtp_port": "587"}
        for k, val in padroes.items():
            self.v[k].set(val)

        self._estilos()
        self._montar()
        self._carregar_perfil()
        for k in ("src_host", "src_user"):
            self.v[k].trace_add("write", lambda *_: self._invalidar_pastas())
        for k in CAMPOS_PERFIL:
            self.v[k].trace_add("write", lambda *_: self._atualizar_resumo())
        self.v["modo"].trace_add("write", lambda *_: self._aplicar_modo())
        self.v["csv"].trace_add("write", lambda *_: self._ler_csv())
        self.v["dir"].trace_add("write", lambda *_: self._previa_dir())
        self._aplicar_modo()
        self._ler_csv()
        self._previa_dir()
        self.mostrar("config")
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.after(50, self._drenar)
        if atualizador.empacotado():
            self.atualizacao_pronta = atualizador.pendente()
            if self.atualizacao_pronta:  # baixada antes, mas ainda não instalada
                self.lateral.definir_aviso("✔ Atualização pronta", "clique para instalar")
            atualizador.limpar_sobras()
            self.after(3000, self._busca_periodica)

    def px(self, n):
        return int(n * self.escala)

    # ------------------------------------------------------------- estilos
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        px = self.px
        f = (FONTE, 10)
        s.configure(".", font=f, background=COR["fundo"], foreground=COR["tinta"])
        s.configure("TFrame", background=COR["fundo"])
        s.configure("Cartao.TFrame", background=COR["superficie"])
        for nome, cor, fonte in (("Cartao", COR["tinta"], f),
                                 ("CartaoTitulo", COR["tinta"], (FONTE_SEMI, 12)),
                                 ("CartaoMudo", COR["muda"], (FONTE, 9)),
                                 ("Campo", COR["tinta2"], (FONTE, 9)),
                                 ("CartaoOk", COR["ok_texto"], (FONTE, 9)),
                                 ("CartaoErro", COR["erro_texto"], (FONTE, 9))):
            s.configure(f"{nome}.TLabel", background=COR["superficie"], foreground=cor, font=fonte)
        for nome, cor, fonte in (("Pagina", COR["tinta"], (FONTE_SEMI, 17)),
                                 ("PaginaSub", COR["muda"], (FONTE, 10)),
                                 ("PaginaMono", COR["muda"], (MONO, 10)),
                                 ("Passo", COR["tinta2"], (FONTE_SEMI, 10)),
                                 ("Rodape", COR["tinta2"], (FONTE, 10)),
                                 ("Mudo", COR["muda"], (FONTE, 9)),
                                 ("Erro", COR["erro_texto"], (FONTE, 9)),
                                 ("Secao", COR["tinta"], (FONTE_SEMI, 12))):
            s.configure(f"{nome}.TLabel", background=COR["fundo"], foreground=cor, font=fonte)

        # combobox sem moldura própria: a moldura arredondada é desenhada pelo Campo
        branco = COR["superficie"]
        s.configure("Campo.TCombobox", fieldbackground=branco, background=branco,
                    bordercolor=branco, lightcolor=branco, darkcolor=branco,
                    arrowcolor=COR["tinta2"], padding=(px(4), px(2)), insertcolor=COR["tinta"])
        s.map("Campo.TCombobox", fieldbackground=[("readonly", branco), ("focus", branco)],
              background=[("active", branco), ("pressed", branco)],
              bordercolor=[("focus", branco)], lightcolor=[("focus", branco)])
        self.option_add("*TCombobox*Listbox.font", f)
        self.option_add("*TCombobox*Listbox.selectBackground", COR["destaque_claro"])
        self.option_add("*TCombobox*Listbox.selectForeground", COR["tinta"])

        # tabelas: sem moldura, linhas altas, sem a marca de expandir nem o pontilhado de foco
        s.configure("Treeview", background=branco, fieldbackground=branco, foreground=COR["tinta"],
                    borderwidth=0, rowheight=px(38), font=f)
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": ""})]})])
        s.configure("Treeview.Heading", background=COR["cabecalho"], foreground=COR["tinta2"],
                    font=(FONTE_SEMI, 9), relief="flat", borderwidth=0,
                    bordercolor=COR["cabecalho"], lightcolor=COR["cabecalho"],
                    darkcolor=COR["cabecalho"], padding=(px(10), px(9)))
        s.map("Treeview.Heading", background=[("active", "#ebebe7")])
        s.map("Treeview", background=[("selected", COR["destaque_claro"])],
              foreground=[("selected", COR["tinta"])])

        # barra de rolagem fina, sem setas
        s.layout("Fino.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns", "children": [("Vertical.Scrollbar.thumb",
                                          {"expand": "1", "sticky": "nswe"})]})])
        s.configure("Fino.Vertical.TScrollbar", troughcolor=branco, background="#d7d7d2",
                    bordercolor=branco, lightcolor="#d7d7d2", darkcolor="#d7d7d2",
                    gripcount=0, arrowsize=px(9), relief="flat")
        s.map("Fino.Vertical.TScrollbar", background=[("active", "#c3c3bd")],
              lightcolor=[("active", "#c3c3bd")], darkcolor=[("active", "#c3c3bd")])

    def rotulo(self, pai, texto, linha, coluna, colspan=1):
        lb = ttk.Label(pai, text=texto, style="Campo.TLabel")
        lb.grid(row=linha, column=coluna, columnspan=colspan, sticky="w",
                pady=(self.px(10), self.px(4)))
        return lb

    # -------------------------------------------------------------- layout
    def _montar(self):
        # navegação e andamento resumido, sempre visíveis, em qualquer página
        self.lateral = BarraLateral(self, self.px, self.mostrar, self.abrir_atualizacao)
        self.lateral.pack(side="left", fill="y")

        area = ttk.Frame(self)
        area.pack(side="left", fill="both", expand=True)
        self.paginas = {}
        for chave, construtor in (("config", self._pagina_config),
                                  ("progresso", self._pagina_progresso),
                                  ("historico", self._pagina_historico),
                                  ("opcoes", self._pagina_opcoes)):
            pg = ttk.Frame(area, padding=(self.px(28), self.px(24)))
            construtor(pg)
            self.paginas[chave] = pg

    def mostrar(self, chave):
        """Só a página visível fica no layout: redimensionar a janela fica leve."""
        if chave == "historico":
            self._atualizar_historico()
        for c, pg in self.paginas.items():
            if c == chave:
                pg.place(relx=0, rely=0, relwidth=1, relheight=1)
            elif c == self.pagina_atual:
                pg.place_forget()
        self.pagina_atual = chave
        self.lateral.ativar(chave)

    def _cabecalho(self, pg, titulo, subtitulo, mono=False):
        topo = ttk.Frame(pg)
        topo.pack(fill="x", pady=(0, self.px(18)))
        esq = ttk.Frame(topo)
        esq.pack(side="left", fill="x", expand=True)
        ttk.Label(esq, text=titulo, style="Pagina.TLabel").pack(anchor="w")
        sub = ttk.Label(esq, text=subtitulo, style="PaginaMono.TLabel" if mono else "PaginaSub.TLabel")
        sub.pack(anchor="w", pady=(self.px(2), 0))
        return topo, sub

    # ------------------------------------------------- página: configurar
    def _pagina_config(self, pg):
        px = self.px
        self._cabecalho(pg, "Nova migração",
                        "Escolha o tipo, preencha as contas e clique em Iniciar. "
                        "Pode rodar de novo quantas vezes quiser: nada é duplicado.")

        # rodapé fixo com o botão principal (empacotado antes para não sumir)
        rodape = ttk.Frame(pg)
        rodape.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.btn_iniciar = Botao(rodape, px, "▶  Iniciar migração", self.iniciar, "primario",
                                 grande=True)
        self.btn_iniciar.pack(side="right")
        self.resumo = ttk.Label(rodape, text="", style="Rodape.TLabel",
                                wraplength=px(620), justify="left")
        self.resumo.pack(side="left", fill="x")

        ttk.Label(pg, text="1  ·  O que você quer fazer?", style="Passo.TLabel").pack(
            anchor="w", pady=(0, px(8)))
        self.cartoes_modo = CartoesModo(pg, px, self.v["modo"].set)
        self.cartoes_modo.pack(fill="x")

        ttk.Label(pg, text="2  ·  Contas", style="Passo.TLabel").pack(
            anchor="w", pady=(px(18), px(8)))
        self.grade = ttk.Frame(pg)
        self.grade.pack(fill="x")
        self.grade.columnconfigure((0, 1), weight=1, uniform="lado")
        self.card_src = CartaoServidor(self, self.grade, "src", "Origem",
                                       "De onde os e-mails saem")
        self.card_dst = CartaoServidor(self, self.grade, "dst", "Destino",
                                       "Para onde os e-mails vão")
        self.card_dir = Cartao(self.grade, px)
        corpo = self.card_dir.corpo
        corpo.columnconfigure(0, weight=1)
        self.dir_titulo = ttk.Label(corpo, text="", style="CartaoTitulo.TLabel")
        self.dir_titulo.grid(row=0, column=0, columnspan=2, sticky="w")
        self.dir_sub = ttk.Label(corpo, text="", style="CartaoMudo.TLabel")
        self.dir_sub.grid(row=1, column=0, columnspan=2, sticky="w")
        self.rotulo(corpo, "Pasta no computador", 2, 0)
        Campo(corpo, px, self.v["dir"]).grid(row=3, column=0, sticky="ew")
        Botao(corpo, px, "Procurar…", self._escolher_dir).grid(row=3, column=1, padx=(px(8), 0))
        self.dir_previa = ttk.Label(corpo, text="", style="CartaoMudo.TLabel",
                                    wraplength=px(380), justify="left")
        self.dir_previa.grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(12), 0))

        self.passo3 = ttk.Label(pg, text="3  ·  Pastas", style="Passo.TLabel")
        self.passo3.pack(anchor="w", pady=(px(18), px(8)))
        self.area_baixo = ttk.Frame(pg)
        self.area_baixo.pack(fill="both", expand=True)
        self._cartao_pastas()
        self._cartao_csv()

    def _cartao_pastas(self):
        px = self.px
        self.card_pastas = ttk.Frame(self.area_baixo)
        topo = ttk.Frame(self.card_pastas)
        topo.pack(fill="x", pady=(0, px(10)))
        self.btn_pastas = Botao(topo, px, "Carregar pastas da origem", self.carregar_pastas)
        self.btn_pastas.pack(side="left")
        for texto, valor in (("Nenhuma", False), ("Todas", True)):
            Botao(topo, px, texto, lambda x=valor: self._marcar_todas(x), "discreto").pack(
                side="right", padx=(px(4), 0))
        self.pastas_info = ttk.Label(topo, style="Mudo.TLabel",
                                     text="Sem carregar, todas as pastas são migradas.")
        self.pastas_info.pack(side="left", padx=px(12))
        self.tab_cfg = Tabela(self.card_pastas, self, COLS_CFG, altura=4, selecao=False)
        self.tab_cfg.pack(fill="both", expand=True)
        self.tab_cfg.tv.configure(cursor="hand2")
        self.tab_cfg.tv.bind("<Button-1>", self._clique_pasta)

    def _cartao_csv(self):
        px = self.px
        self.card_csv = ttk.Frame(self.area_baixo)
        topo = ttk.Frame(self.card_csv)
        topo.pack(fill="x")
        ttk.Label(topo, text="Arquivo CSV", style="Rodape.TLabel").pack(side="left")
        Botao(topo, px, "Procurar…", self._escolher_csv).pack(side="right")
        Campo(topo, px, self.v["csv"]).pack(side="right", fill="x", expand=True,
                                            padx=(px(12), px(8)))
        self.csv_info = ttk.Label(self.card_csv, style="Mudo.TLabel", text=(
            "Colunas esperadas: src_user, src_pass, dst_user, dst_pass "
            "(uma linha por conta)."))
        self.csv_info.pack(anchor="w", pady=(px(8), px(8)))
        self.tab_csv = Tabela(self.card_csv, self, COLS_CSV, altura=4, selecao=False)
        self.tab_csv.pack(fill="both", expand=True)

    def _aplicar_modo(self):
        modo = self.v["modo"].get()
        if modo not in MODOS_GUI:
            self.v["modo"].set("imap2imap")
            return
        self.cartoes_modo.selecionar(modo)
        for w in (self.card_src, self.card_dst, self.card_dir):
            w.grid_remove()
        esq, dir_ = {"imap2imap": (self.card_src, self.card_dst),
                     "lote": (self.card_src, self.card_dst),
                     "export": (self.card_src, self.card_dir),
                     "import": (self.card_dir, self.card_dst)}[modo]
        esq.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(7)))
        dir_.grid(row=0, column=1, sticky="nsew", padx=(self.px(7), 0))
        self.card_src.modo_lote(modo == "lote")
        self.card_dst.modo_lote(modo == "lote")
        if modo == "export":
            self.dir_titulo.configure(text="Salvar em")
            self.dir_sub.configure(text="Cada pasta do e-mail vira uma pasta com arquivos .eml")
        else:
            self.dir_titulo.configure(text="Ler de")
            self.dir_sub.configure(text="Pasta com o backup (.eml) a importar")

        self.card_pastas.pack_forget()
        self.card_csv.pack_forget()
        if modo in ("imap2imap", "export"):
            self.passo3.configure(text="3  ·  Pastas (opcional)")
            self.card_pastas.pack(fill="both", expand=True)
            self.passo3.pack(anchor="w", pady=(self.px(18), self.px(8)), before=self.area_baixo)
        elif modo == "lote":
            self.passo3.configure(text="3  ·  Lista de contas")
            self.card_csv.pack(fill="both", expand=True)
            self.passo3.pack(anchor="w", pady=(self.px(18), self.px(8)), before=self.area_baixo)
        else:
            self.passo3.pack_forget()
        self._previa_dir()
        verbo = {"imap2imap": "migração", "export": "exportação", "import": "importação",
                 "lote": "migração em lote"}[modo]
        self.btn_iniciar.configure(text=f"▶  Iniciar {verbo}")
        self._atualizar_resumo()

    def _atualizar_resumo(self):
        if not hasattr(self, "resumo"):
            return
        g = lambda k: self.v[k].get().strip() or "?"  # noqa: E731
        modo = self.v["modo"].get()
        if modo not in MODOS_GUI:
            return
        src = f"{g('src_user')} ({g('src_host')})"
        dst = f"{g('dst_user')} ({g('dst_host')})"
        texto = {"imap2imap": f"Copiar de {src} para {dst}",
                 "export": f"Exportar {src} para {g('dir')}",
                 "import": f"Importar {g('dir')} para {dst}",
                 "lote": f"Migrar as contas do CSV de {g('src_host')} para {g('dst_host')}"}[modo]
        if modo in ("imap2imap", "export") and self.pastas_origem:
            sel = sum(1 for s, _ in self.pastas_origem.values() if s)
            texto += f" · {sel} de {len(self.pastas_origem)} pastas"
        self.resumo.configure(text=texto)

    # ----------------------------------------------------- pastas da origem
    def _invalidar_pastas(self):
        if self.pastas_origem:
            self.pastas_origem = {}
            self.tab_cfg.limpar()
            self.pastas_info.configure(text="A origem mudou: carregue as pastas de novo "
                                            "(ou deixe assim para migrar todas).")
            self._atualizar_resumo()

    def carregar_pastas(self):
        try:
            conta = self._conta("src", "origem")
        except ValueError as e:
            messagebox.showwarning("Faltam dados", str(e), parent=self)
            return
        self.btn_pastas.state(["disabled"])
        self.pastas_info.configure(text="Conectando e contando mensagens…")
        timeout = self._timeout()

        def tarefa():
            socket.setdefaulttimeout(timeout)
            try:
                c = conta.conectar(tentativas=1)
                lista = []
                for nome, delim in me.listar_pastas(c):
                    typ, d = c.status(me.q(nome), "(MESSAGES)")
                    resp = b" ".join(x if isinstance(x, bytes) else b" ".join(x)
                                     for x in d if x)  # o nome pode vir como literal
                    m = re.search(rb"MESSAGES (\d+)", resp) if typ == "OK" else None
                    lista.append((nome, delim, int(m.group(1)) if m else 0))
                c.logout()
                self.fila.put(("pastas", lista, None))
            except Exception as e:
                self.fila.put(("pastas", None, str(e)))
        threading.Thread(target=tarefa, daemon=True).start()

    def _mostrar_pastas(self, lista, erro):
        self.btn_pastas.state(["!disabled"])
        if erro:
            self.pastas_info.configure(text=f"Falhou: {erro}")
            return
        self.pastas_origem = {nome: [True, n] for nome, _, n in lista}
        self.tab_cfg.limpar()
        for nome, delim, n in lista:
            rotulo = " › ".join(decodificar_utf7(p) for p in nome.split(delim or "/"))
            self.tab_cfg.inserir((("check_on", rotulo), num(n)), iid=nome)
        self._atualizar_contagem_pastas()

    def _marcar_pasta(self, iid, valor):
        self.pastas_origem[iid][0] = valor
        self.tab_cfg.tv.item(iid, image=self.icones["check_on" if valor else "check_off"])

    def _clique_pasta(self, e):
        iid = self.tab_cfg.tv.identify_row(e.y)
        if iid in self.pastas_origem:
            self._marcar_pasta(iid, not self.pastas_origem[iid][0])
            self._atualizar_contagem_pastas()

    def _marcar_todas(self, valor):
        for iid in self.pastas_origem:
            self._marcar_pasta(iid, valor)
        self._atualizar_contagem_pastas()

    def _atualizar_contagem_pastas(self):
        sel = [n for s, n in self.pastas_origem.values() if s]
        self.pastas_info.configure(
            text=f"{len(sel)} de {len(self.pastas_origem)} pastas selecionadas · "
                 f"{num(sum(sel))} mensagens  (clique na linha para marcar/desmarcar)")
        self._atualizar_resumo()

    def _pastas_escolhidas(self):
        if not self.pastas_origem:
            return None
        sel = [nome for nome, (s, _) in self.pastas_origem.items() if s]
        if not sel:
            raise ValueError("Marque ao menos uma pasta (ou clique em Todas).")
        return None if len(sel) == len(self.pastas_origem) else sel

    # ------------------------------------------------------- csv / diretório
    def _escolher_dir(self):
        d = filedialog.askdirectory(parent=self, title="Escolha a pasta",
                                    initialdir=self.v["dir"].get() or os.path.expanduser("~"))
        if d:
            self.v["dir"].set(os.path.normpath(d))

    def _escolher_csv(self):
        f = filedialog.askopenfilename(parent=self, title="Arquivo de contas",
                                       filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if f:
            self.v["csv"].set(os.path.normpath(f))

    def _ler_csv(self):
        self.tab_csv.limpar()
        self.contas_csv = []
        caminho = self.v["csv"].get().strip()
        if not caminho:
            return
        try:
            with open(caminho, newline="", encoding="utf-8-sig") as f:
                leitor = csv.DictReader(f)
                faltando = {"src_user", "src_pass", "dst_user", "dst_pass"} - set(leitor.fieldnames or [])
                if faltando:
                    raise ValueError(f"faltam as colunas: {', '.join(sorted(faltando))}")
                contas = list(leitor)
        except (OSError, ValueError, csv.Error) as e:
            self.csv_info.configure(text=f"Não foi possível ler o CSV: {e}", style="Erro.TLabel")
            return
        self.contas_csv = [(row["src_user"].strip(), row["dst_user"].strip()) for row in contas]
        for n, (src, dst) in enumerate(self.contas_csv, 1):
            self.tab_csv.inserir((n, src, dst))
        self.csv_info.configure(text=f"{len(contas)} contas encontradas. As senhas não são exibidas.",
                                style="Mudo.TLabel")

    def _previa_dir(self):
        d = self.v["dir"].get().strip()
        if self.v["modo"].get() != "import" or not d:
            self.dir_previa.configure(text="")
            return
        if not os.path.isdir(d):
            self.dir_previa.configure(text="Pasta não encontrada.")
            return
        self.dir_previa.configure(text="Contando arquivos…")

        def tarefa():
            pastas = arquivos = 0
            for _, _, nomes in os.walk(d):
                n = sum(1 for x in nomes if x.lower().endswith(".eml"))
                pastas += n > 0
                arquivos += n
            self.fila.put(("previa_dir", d, f"{num(arquivos)} arquivos .eml em {pastas} pastas. "
                                            "Os da raiz vão para a Caixa de Entrada."))
        threading.Thread(target=tarefa, daemon=True).start()

    # ------------------------------------------------------ página: opções
    def _pagina_opcoes(self, pg):
        px = self.px
        self._cabecalho(pg, "Opções", "Valem para todas as migrações. "
                                      "As senhas nunca são gravadas em disco.")
        col = ttk.Frame(pg)
        col.pack(fill="x")
        col.columnconfigure((0, 1), weight=1, uniform="op")

        c1 = Cartao(col, px)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, px(7)))
        corpo = c1.corpo
        corpo.columnconfigure(0, weight=1)
        ttk.Label(corpo, text="Conexão e registro", style="CartaoTitulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.rotulo(corpo, "Tempo limite sem resposta do servidor (segundos)", 1, 0, 2)
        Campo(corpo, px, self.v["timeout"], largura=8).grid(row=2, column=0, sticky="w")
        self.rotulo(corpo, "Arquivo de log", 3, 0, 2)
        Campo(corpo, px, self.v["log"]).grid(row=4, column=0, sticky="ew")
        Botao(corpo, px, "Procurar…", self._escolher_log).grid(row=4, column=1, padx=(px(8), 0))
        Botao(corpo, px, "Abrir log", self.abrir_log, "discreto").grid(
            row=5, column=0, sticky="w", pady=(px(12), 0))

        c2 = Cartao(col, px)
        c2.grid(row=0, column=1, sticky="nsew", padx=(px(7), 0))
        corpo = c2.corpo
        corpo.columnconfigure((0, 1), weight=1)
        ttk.Label(corpo, text="Avisar quando terminar", style="CartaoTitulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(corpo, text="Opcional. Webhook do Slack/Discord/Mattermost e/ou e-mail.",
                  style="CartaoMudo.TLabel").grid(row=1, column=0, columnspan=2, sticky="w")
        campos = [("Webhook (URL)", "webhook", 2), ("Enviar resultado para (e-mail)", "notify_email", 4)]
        for rotulo, chave, linha in campos:
            self.rotulo(corpo, rotulo, linha, 0, 2)
            Campo(corpo, px, self.v[chave]).grid(row=linha + 1, column=0, columnspan=2,
                                                  sticky="ew")
        pares = [(("Servidor SMTP", "smtp_host"), ("Porta SMTP", "smtp_port")),
                 (("Usuário SMTP", "smtp_user"), ("Senha SMTP", "smtp_pass")),
                 (("Remetente (opcional)", "smtp_from"), None)]
        for i, par in enumerate(pares):
            linha = 6 + i * 2
            for j, item in enumerate(par):
                if not item:
                    continue
                self.rotulo(corpo, item[0], linha, j)
                Campo(corpo, px, self.v[item[1]],
                      show="•" if item[1] == "smtp_pass" else None).grid(
                    row=linha + 1, column=j, sticky="ew", padx=(0, px(8)) if j == 0 else 0)

        c3 = Cartao(pg, px)
        c3.pack(fill="x", pady=(px(16), 0))
        corpo = c3.corpo
        ttk.Label(corpo, text="Versão e atualizações", style="CartaoTitulo.TLabel").pack(
            side="left")
        ttk.Label(corpo, style="CartaoMudo.TLabel",
                  text=f"Versão {VERSAO} · procura atualizações ao abrir e a cada 6 horas"
                  if atualizador.empacotado() else
                  "Versão de desenvolvimento: a atualização automática funciona no .exe").pack(
            side="left", padx=px(12))
        Botao(corpo, px, "Ver todas as versões", self._abrir_versoes).pack(side="right")
        Botao(corpo, px, "Procurar atualizações",
              lambda: self.procurar_atualizacao(manual=True)).pack(side="right", padx=(0, px(8)))

        rodape = ttk.Frame(pg)
        rodape.pack(fill="x", pady=(px(16), 0))
        Botao(rodape, px, "Salvar como padrão",
              lambda: (self._salvar_perfil(), messagebox.showinfo(
                  "Salvo", "Configurações salvas (sem as senhas).", parent=self))).pack(side="left")
        ttk.Label(rodape, text=f"Salvo em {PERFIL}", style="Mudo.TLabel").pack(
            side="left", padx=px(12))

    def _escolher_log(self):
        f = filedialog.asksaveasfilename(parent=self, title="Arquivo de log",
                                         defaultextension=".log", initialfile="migracao.log",
                                         filetypes=[("Log", "*.log"), ("Todos", "*.*")])
        if f:
            self.v["log"].set(os.path.normpath(f))

    def _abrir_versoes(self):
        import webbrowser
        webbrowser.open(atualizador.PAGINA)

    def abrir_log(self):
        caminho = self.v["log"].get().strip() or LOG_PADRAO
        if os.path.exists(caminho):
            os.startfile(caminho)
        else:
            messagebox.showinfo("Log", "O log ainda não existe: ele é criado na primeira execução.",
                                parent=self)

    # --------------------------------------------------- página: andamento
    def _pagina_progresso(self, pg):
        px = self.px
        topo, self.lbl_rota = self._cabecalho(pg, "Andamento", "Nenhuma migração em andamento",
                                              mono=True)
        acoes = ttk.Frame(topo)
        acoes.pack(side="right", anchor="n", pady=(px(4), 0))
        self.pilula = Pilula(acoes, px)
        self.pilula.pack(side="left")
        self.btn_parar = Botao(acoes, px, "■  Parar", self.parar_execucao, "perigo")
        self.btn_abrir_dir = Botao(acoes, px, "Abrir pasta",
                                   lambda: os.startfile(self.v["dir"].get()))

        self.kpis = PainelKpi(pg, px, (("copiadas", "Copiadas"), ("puladas", "Já existiam"),
                                       ("erros", "Erros"), ("bytes", "Transferido")))
        self.kpis.pack(fill="x")

        self.zona = ttk.Frame(pg)
        self.zona.pack(fill="x", pady=(px(14), 0))
        self.progresso = PainelProgresso(self.zona, px, (("pasta", COR["destaque"]),
                                                         ("total", COR["ok"])))
        self.progresso.pack(fill="x")
        self._montar_banner()

        baixo = ttk.Frame(pg)
        baixo.pack(fill="both", expand=True, pady=(px(14), 0))
        baixo.columnconfigure(0, weight=1)
        baixo.rowconfigure(0, weight=1)
        esq = ttk.Frame(baixo)
        esq.grid(row=0, column=0, sticky="nsew")
        self.vel = PainelVelocidade(baixo, px)
        self.vel.configure(width=px(300))
        # alinhado com o topo da tabela, abaixo da fileira de abas
        self.vel.grid(row=0, column=1, sticky="new", padx=(px(14), 0), pady=(px(42), 0))

        self.abas = Abas(esq, px)
        self.abas.pack(fill="both", expand=True)
        f = self.abas.adicionar("pastas", "Pastas")
        self.tab_pastas = Tabela(f, self, COLS_PASTAS, altura=4)
        self.tab_pastas.pack(fill="both", expand=True)
        f = self.abas.adicionar("contas", "Contas")
        self.tab_contas = Tabela(f, self, COLS_CONTAS, altura=4)
        self.tab_contas.pack(fill="both", expand=True)
        f = self.abas.adicionar("problemas", "Problemas (0)")
        self.tab_problemas = Tabela(f, self, COLS_PROBLEMAS, altura=4)
        self.tab_problemas.pack(fill="both", expand=True)
        f = self.abas.adicionar("log", "Registro completo")
        linha = tk.Frame(f, bg=f.cget("bg"))
        linha.pack(fill="x", pady=(0, px(6)))
        self.lbl_log_cortado = ttk.Label(linha, style="Mudo.TLabel", text="")
        self.lbl_log_cortado.pack(side="left")
        Botao(linha, px, "Abrir arquivo de log", self.abrir_log, "discreto").pack(side="right")
        caixa = Cartao(f, px, padding=12)
        caixa.pack(fill="both", expand=True)
        self.txt_log = tk.Text(caixa.corpo, height=5, wrap="none", bd=0, padx=4, pady=2,
                               bg=COR["superficie"], fg=COR["tinta"], font=(MONO, 9),
                               highlightthickness=0, state="disabled")
        barra = ttk.Scrollbar(caixa.corpo, orient="vertical", command=self.txt_log.yview,
                              style="Fino.Vertical.TScrollbar")
        self.txt_log.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True)
        for tag, cor, peso in (("hora", COR["muda"], "normal"), ("info", COR["tinta2"], "normal"),
                               ("ok", COR["ok_texto"], "normal"),
                               ("aviso", COR["aviso_texto"], "normal"),
                               ("erro", COR["erro_texto"], "bold"),
                               ("titulo", COR["destaque_esc"], "bold"),
                               ("secao", COR["tinta"], "bold"), ("texto", COR["tinta"], "normal")):
            self.txt_log.tag_configure(tag, foreground=cor, font=(MONO, 9, peso))
        self.abas.visivel("contas", False)
        self._resetar_painel()
        self._estado("parado")

    def _montar_banner(self):
        px = self.px
        self.banner = Cartao(self.zona, px, padding=18)
        dentro = self.banner.corpo
        dentro.columnconfigure(1, weight=1)
        self.ban_icone = tk.Label(dentro, font=(FONTE_SEMI, 20))
        self.ban_icone.grid(row=0, column=0, rowspan=2, padx=(0, px(14)))
        self.ban_titulo = tk.Label(dentro, font=(FONTE_SEMI, 13), anchor="w")
        self.ban_titulo.grid(row=0, column=1, sticky="w")
        self.ban_texto = tk.Label(dentro, font=(FONTE, 10), anchor="w", justify="left",
                                  wraplength=px(560))
        self.ban_texto.grid(row=1, column=1, sticky="w")
        botoes = tk.Frame(dentro)
        botoes.grid(row=0, column=2, rowspan=2, sticky="e")
        # o texto se ajusta à largura que sobra ao lado dos botões, em vez de ser cortado
        dentro.bind("<Configure>", lambda e: self.ban_texto.configure(
            wraplength=max(px(200), e.width - botoes.winfo_reqwidth() - px(90))))
        self.ban_problemas = Botao(botoes, px, "Ver problemas",
                                   lambda: self.abas.mostrar("problemas"))
        self.ban_problemas.pack(side="left")
        self.ban_exportar = Botao(botoes, px, "Exportar relatório",
                                  lambda: self.exportar_relatorio(self.rel))
        self.ban_exportar.pack(side="left", padx=(px(8), 0))
        self.ban_nova = Botao(botoes, px, "Nova migração", lambda: self.mostrar("config"))
        self.ban_nova.pack(side="left", padx=(px(8), 0))
        self.ban_fundos = [dentro, self.ban_icone, self.ban_titulo, self.ban_texto, botoes]
        self.ban_botoes = [self.ban_problemas, self.ban_exportar, self.ban_nova]

    def _mostrar_banner(self, estado, titulo, texto):
        icone, fundo, borda, tinta = BANNER[estado]
        self.banner.recolorir(fundo, borda)
        for w in self.ban_fundos:
            w.configure(bg=fundo)
        for b in self.ban_botoes:
            b.fundo(fundo)
        self.ban_icone.configure(text=icone, fg=tinta)
        self.ban_titulo.configure(text=titulo, fg=tinta)
        self.ban_texto.configure(text=texto, fg=COR["tinta"])
        problemas = len(self.rel["problemas"])
        if problemas:
            self.ban_problemas.configure(text=f"Ver problemas ({num(problemas)})")
            self.ban_problemas.pack(side="left", before=self.ban_exportar)
        else:
            self.ban_problemas.pack_forget()
        self.progresso.pack_forget()
        self.banner.pack(fill="x")

    def _estado(self, estado):
        texto, fundo, tinta = PILULAS[estado]
        self.pilula.definir(texto, fundo, tinta)
        ativo = estado in ("rodando", "parando")
        detalhe = self.lateral.situacao[1] if ativo else ""
        self.lateral.estado("● Parando…" if estado == "parando" else
                            "● Migrando…" if ativo else "● Pronto", detalhe, ativo)
        if not ativo:
            self.title(self.titulo_base)
        if ativo:
            self.btn_parar.pack(side="left", padx=(self.px(10), 0), after=self.pilula)
            self.btn_parar.state(["!disabled"] if estado == "rodando" else ["disabled"])
        else:
            self.btn_parar.pack_forget()

    @staticmethod
    def _novo_atual():
        return {"feito": 0, "total": 0, "bytes": 0, "ja": 0, "mensagens": 0,
                "inicio": None, "inicio_pasta": None}

    def _resetar_painel(self):
        self.tot = {"copiadas": 0, "puladas": 0, "erros": 0, "bytes": 0}
        self.atual = self._novo_atual()
        self.linhas = {}  # iid da tabela -> registro da pasta (o mesmo do relatório)
        self.linha_atual = None
        self.erros_atual = 0
        self.conta_n, self.conta_atual, self.contas_total, self.contas_feitas = 0, "", 0, 0
        self.pasta_ctx = ""
        self.proc_concluidas = 0  # mensagens das pastas já terminadas
        self.pastas_feitas = 0
        self.total_geral = self.total_pastas = 0
        self.amostras = []  # (tempo, bytes acumulados) para a média móvel
        self.inicio = None
        self._prog_mudou = self._sujo = False
        self._salvo_em = 0
        self.vel.limpar()
        self.progresso.definir("pasta", "Nenhuma migração em andamento", "")
        self.progresso.definir("total", "Total", "")
        for chave in ("pasta", "total"):
            self.progresso.alvo(chave, 0, instantaneo=True)
            self.progresso.barra_visivel(chave, True)
        for t in (self.tab_pastas, self.tab_contas, self.tab_problemas):
            t.limpar()
        self.abas.titulo("problemas", "Problemas (0)")
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")
        self.lbl_log_cortado.configure(text="")
        self.banner.pack_forget()
        self.progresso.pack(fill="x")
        self._kpis()

    def _totais(self):
        a = self.atual
        return {"copiadas": self.tot["copiadas"] + max(0, a["feito"] - self.erros_atual),
                "puladas": self.tot["puladas"] + a["ja"],
                "erros": self.tot["erros"] + self.erros_atual,
                "bytes": self.tot["bytes"] + a["bytes"]}

    def _processadas(self):
        return self.proc_concluidas + self.atual["ja"] + self.atual["feito"]

    def _eta(self):
        """Tempo restante estimado: pelo total geral, se conhecido; senão, da pasta atual."""
        agora = time.time()
        feitas = self._processadas()
        if self.total_geral and feitas and self.inicio:
            return (agora - self.inicio) / feitas * max(0, self.total_geral - feitas)
        a = self.atual
        if a["inicio"] and a["feito"]:
            return (agora - a["inicio"]) / a["feito"] * (a["total"] - a["feito"])
        return 0

    def _kpis(self):
        if self.contas_total:
            rotulo_feitas = "Contas concluídas"
            feitas = f"{self.contas_feitas} de {self.contas_total}"
        else:
            rotulo_feitas = "Pastas concluídas"
            feitas = (f"{self.pastas_feitas} de {self.total_pastas}" if self.total_pastas
                      else num(self.pastas_feitas))
        if not self.inicio:
            for chave, _ in self.kpis.itens:
                self.kpis.set(chave, "—")
            self.vel.definir_info([("Tempo decorrido", "—"), ("Tempo restante", "—"),
                                   (rotulo_feitas, "—")])
            return
        t = self._totais()
        self.kpis.set("copiadas", num(t["copiadas"]), COR["destaque"])
        self.kpis.set("puladas", num(t["puladas"]))
        self.kpis.set("erros", num(t["erros"]), COR["erro"] if t["erros"] else None)
        self.kpis.set("bytes", me.fmt_bytes(t["bytes"]))
        eta = self._eta() if self.rodando else 0
        self.vel.definir_info([
            ("Tempo decorrido", me.fmt_tempo(time.time() - self.inicio)),
            ("Tempo restante", me.fmt_tempo(eta) if eta else "—" if self.rodando else "0s"),
            (rotulo_feitas, feitas)])

    def _atualizar_progresso(self):
        a = self.atual
        if a["inicio"] is not None:
            self.progresso.definir("pasta", direita=f"{num(a['feito'])} / {num(a['total'])}")
            self.progresso.alvo("pasta", a["feito"] / a["total"] if a["total"] else 1)
        pct = None
        if self.total_geral:
            feitas = min(self._processadas(), self.total_geral)
            pct = feitas / self.total_geral
            self.progresso.definir("total", "Total",
                                   f"{pct:.0%} · {num(feitas)} / {num(self.total_geral)}")
        elif self.contas_total:
            pct = self.contas_feitas / self.contas_total
            self.progresso.definir("total", "Contas",
                                   f"{pct:.0%} · {self.contas_feitas} / {self.contas_total}")
        else:
            self.progresso.definir("total", "Total", plural(self.pastas_feitas, "pasta concluída",
                                                            "pastas concluídas"))
        if pct is not None:
            self.progresso.barra_visivel("total", True)
            self.progresso.alvo("total", pct)
            self.title(f"{pct:.0%} · {self.titulo_base}")
            self.lateral.estado(self.lateral.situacao[0], f"{pct:.0%} concluído", True)
        else:
            self.progresso.barra_visivel("total", False)  # total desconhecido: sem barra que engane
            self.lateral.estado(self.lateral.situacao[0],
                                f"{self.pastas_feitas} pastas concluídas", True)

    def _escrever_logs(self, itens):
        """Escreve várias linhas de uma vez (uma única operação no widget)."""
        args = []
        for nivel, texto in itens:
            texto = decodificar_utf7(texto)
            if nivel == "titulo":
                args += [f"\n{texto}\n", "titulo"]
                continue
            if nivel == "secao":
                args += ["\n", "texto"]
            simbolo = GuiUi.SIMBOLOS.get(nivel)
            args += [time.strftime("%H:%M:%S "), "hora",
                     f"{'▸ ' if nivel == 'secao' else ''}{simbolo + ' ' if simbolo else ''}"
                     f"{texto}\n", nivel]
        t = self.txt_log
        acompanhar = t.yview()[1] >= 0.999  # só rola se você já estava no fim
        t.configure(state="normal")
        t.insert("end", *args)
        linhas = int(t.index("end-1c").split(".")[0])
        if linhas > LIMITE_LOG:
            t.delete("1.0", f"{linhas - LIMITE_LOG + 1}.0")
            self.lbl_log_cortado.configure(
                text=f"Mostrando as últimas {num(LIMITE_LOG)} linhas. O log completo está em "
                     f"{self.rel['log'] if self.rel else LOG_PADRAO}")
        t.configure(state="disabled")
        if acompanhar:
            t.see("end")

    # ------------------------------------------------------ página: histórico
    def _pagina_historico(self, pg):
        px = self.px
        topo, _ = self._cabecalho(pg, "Histórico", "Toda execução fica registrada com o resultado "
                                                   "de cada pasta e cada problema, mesmo depois "
                                                   "de fechar o app.")
        acoes = ttk.Frame(topo)
        acoes.pack(side="right", anchor="n", pady=(px(4), 0))
        Botao(acoes, px, "Abrir pasta dos relatórios", self._abrir_pasta_historico).pack(
            side="left")

        self.tab_hist = Tabela(pg, self, COLS_HIST, altura=6)
        self.tab_hist.pack(fill="x")
        self.tab_hist.tv.bind("<<TreeviewSelect>>", lambda e: self._mostrar_execucao())

        det = ttk.Frame(pg)
        det.pack(fill="both", expand=True, pady=(px(20), 0))
        linha = ttk.Frame(det)
        linha.pack(fill="x")
        self.hist_titulo = ttk.Label(linha, text="Nenhuma migração registrada ainda.",
                                     style="Secao.TLabel")
        self.hist_titulo.pack(side="left")
        self.hist_exportar = Botao(linha, px, "Exportar relatório (CSV)",
                                   lambda: self.exportar_relatorio(self.hist_sel))
        self.hist_exportar.pack(side="right")
        self.hist_exportar.state(["disabled"])
        self.hist_resumo = ttk.Label(det, text="", style="Mudo.TLabel",
                                     wraplength=px(900), justify="left")
        self.hist_resumo.pack(anchor="w", pady=(px(2), px(10)))
        self.hist_abas = Abas(det, px)
        self.hist_abas.pack(fill="both", expand=True)
        f = self.hist_abas.adicionar("pastas", "Pastas")
        self.hist_pastas = Tabela(f, self, COLS_PASTAS, altura=4)
        self.hist_pastas.pack(fill="both", expand=True)
        f = self.hist_abas.adicionar("contas", "Contas")
        self.hist_contas = Tabela(f, self, COLS_CONTAS, altura=4)
        self.hist_contas.pack(fill="both", expand=True)
        f = self.hist_abas.adicionar("problemas", "Problemas")
        self.hist_problemas = Tabela(f, self, COLS_PROBLEMAS, altura=4)
        self.hist_problemas.pack(fill="both", expand=True)
        self.historico, self.hist_sel = [], None

    def _atualizar_historico(self):
        anterior = self.hist_sel["id"] if self.hist_sel else None
        self.historico = carregar_historico()
        self.tab_hist.limpar()
        for rel in self.historico:
            em_curso = self.rodando and self.rel and rel["id"] == self.rel["id"]
            t = rel["totais"]
            texto, icone = ("Em andamento", "andamento") if em_curso else \
                RESULTADO.get(rel["estado"], (rel["estado"], "pendente"))
            self.tab_hist.inserir(
                ((icone, texto), data_hora(rel["inicio"]), rel["tipo"],
                 f"{rel['origem']} → {rel['destino']}",
                 num(t["copiadas"]), num(t["puladas"]), num(t["erros"]),
                 me.fmt_tempo(rel.get("duracao", 0))),
                ("ativa",) if em_curso else (), iid=rel["id"])
        if self.historico:
            alvo = anterior if any(r["id"] == anterior for r in self.historico) else self.historico[0]["id"]
            self.tab_hist.tv.selection_set(alvo)
            self.tab_hist.tv.see(alvo)
        else:
            self._mostrar_execucao()

    def _mostrar_execucao(self):
        sel = self.tab_hist.tv.selection()
        rel = next((r for r in self.historico if sel and r["id"] == sel[0]), None)
        self.hist_sel = rel
        for t in (self.hist_pastas, self.hist_contas, self.hist_problemas):
            t.limpar()
        if not rel:
            self.hist_titulo.configure(text="Nenhuma migração registrada ainda.")
            self.hist_resumo.configure(text="")
            self.hist_exportar.state(["disabled"])
            return
        lote = bool(rel["contas"])
        self.hist_titulo.configure(text=f"{rel['tipo']} · {data_hora(rel['inicio'])}")
        self.hist_resumo.configure(
            text=f"{rel['origem']} → {rel['destino']}\n"
                 f"{RESULTADO.get(rel['estado'], (rel['estado'],))[0]} em "
                 f"{me.fmt_tempo(rel.get('duracao', 0))}"
                 + (f" · {rel['resumo'].splitlines()[0]}" if rel.get("resumo") else ""))
        cols = [c[0] for c in COLS_PASTAS if lote or c[0] != "conta"]
        self.hist_pastas.colunas_visiveis(cols)
        self.hist_problemas.colunas_visiveis([c[0] for c in COLS_PROBLEMAS if lote or c[0] != "conta"])
        for p in rel["pastas"]:
            self.hist_pastas.inserir(*linha_pasta(p))
        for c in rel["contas"]:
            self.hist_contas.inserir(*linha_conta(c))
        for pr in rel["problemas"]:
            self.hist_problemas.inserir(*linha_problema(pr))
        self.hist_abas.titulo("problemas", f"Problemas ({num(len(rel['problemas']))})")
        self.hist_abas.visivel("contas", lote)
        self.hist_exportar.state(["!disabled"])

    def _abrir_pasta_historico(self):
        os.makedirs(HIST_DIR, exist_ok=True)
        os.startfile(HIST_DIR)

    def exportar_relatorio(self, rel):
        if not rel:
            return
        if rel is self.rel:
            self._sincronizar_relatorio()
        caminho = filedialog.asksaveasfilename(
            parent=self, title="Salvar relatório", defaultextension=".csv",
            initialfile=f"relatorio_migracao_{rel['id']}.csv",
            filetypes=[("CSV (abre no Excel)", "*.csv")])
        if not caminho:
            return
        try:
            exportar_csv(rel, caminho)
        except OSError as e:
            messagebox.showerror("Relatório", f"Não foi possível salvar: {e}", parent=self)
            return
        if messagebox.askyesno("Relatório salvo", f"Relatório salvo em:\n{caminho}\n\nAbrir agora?",
                               parent=self):
            os.startfile(caminho)

    # ------------------------------------------------------------ execução
    def _inteiro(self, chave, nome):
        try:
            n = int(self.v[chave].get().strip())
            if n <= 0:
                raise ValueError
            return n
        except ValueError:
            raise ValueError(f"{nome} precisa ser um número inteiro positivo.")

    def _timeout(self):
        try:
            return self._inteiro("timeout", "Tempo limite")
        except ValueError:
            return 60

    def _conta(self, lado, nome, credenciais=True):
        host = self.v[f"{lado}_host"].get().strip()
        user = self.v[f"{lado}_user"].get().strip()
        pwd = self.v[f"{lado}_pass"].get()
        if not host:
            raise ValueError(f"Informe o servidor IMAP de {nome}.")
        if credenciais and not (user and pwd):
            raise ValueError(f"Informe usuário e senha de {nome}.")
        return me.Conta(host, user, pwd, self._inteiro(f"{lado}_port", f"A porta de {nome}"))

    def testar_conexao(self, lado):
        card = self.card_src if lado == "src" else self.card_dst
        try:
            conta = self._conta(lado, "origem" if lado == "src" else "destino")
        except ValueError as e:
            card.mostrar_status(str(e), "erro")
            return
        card.mostrar_status("Conectando…")
        card.botao.state(["disabled"])
        timeout = self._timeout()

        def tarefa():
            socket.setdefaulttimeout(timeout)
            try:
                c = conta.conectar(tentativas=1)
                n = len(me.listar_pastas(c))
                c.logout()
                self.fila.put(("teste", lado, True, f"✔ Conectado · {n} pastas"))
            except Exception as e:
                self.fila.put(("teste", lado, False, f"✖ {e}"))
        threading.Thread(target=tarefa, daemon=True).start()

    def _montar_args(self):
        g = lambda k: self.v[k].get().strip()  # noqa: E731
        modo = g("modo")
        a = me.criar_parser().parse_args([MODOS_GUI[modo][2]])  # padrões do CLI
        lote = modo == "lote"
        precisa = {"imap2imap": ("src", "dst"), "lote": ("src", "dst"),
                   "export": ("src",), "import": ("dst",)}[modo]
        for lado in precisa:
            c = self._conta(lado, "origem" if lado == "src" else "destino", not lote)
            setattr(a, f"{lado}_host", c.host)
            setattr(a, f"{lado}_port", c.port)
            setattr(a, f"{lado}_user", c.user or None)
            setattr(a, f"{lado}_pass", c.pwd or None)
        if modo in ("export", "import"):
            if not g("dir"):
                raise ValueError("Escolha a pasta no computador.")
            if modo == "import" and not os.path.isdir(g("dir")):
                raise ValueError("A pasta do backup não existe.")
            a.dir = g("dir")
        if lote:
            if not os.path.isfile(g("csv")):
                raise ValueError("Escolha um arquivo CSV de contas válido.")
            if not self.contas_csv:
                raise ValueError("O CSV não tem contas válidas.")
            a.csv = g("csv")
        a.pasta = self._pastas_escolhidas() if modo in ("imap2imap", "export") else None
        a.timeout = self._inteiro("timeout", "Tempo limite")
        a.log = g("log") or LOG_PADRAO
        a.webhook = g("webhook") or None
        a.notify_email = g("notify_email") or None
        a.smtp_host = g("smtp_host") or None
        a.smtp_port = self._inteiro("smtp_port", "A porta SMTP")
        a.smtp_user = g("smtp_user") or None
        a.smtp_pass = self.v["smtp_pass"].get() or None
        a.smtp_from = g("smtp_from") or None
        return a

    def _novo_relatorio(self, a, modo):
        src = f"{a.src_user} ({a.src_host})" if a.src_user else a.src_host
        dst = f"{a.dst_user} ({a.dst_host})" if a.dst_user else a.dst_host
        origem = {"import": a.dir, "lote": f"{a.src_host} (contas do CSV)"}.get(modo, src)
        destino = {"export": a.dir, "lote": a.dst_host}.get(modo, dst)
        return {"id": time.strftime("%Y%m%d-%H%M%S"), "inicio": time.time(), "fim": None,
                "duracao": 0, "modo": modo, "tipo": MODOS_GUI[modo][0], "origem": origem,
                "destino": destino, "estado": "rodando", "resumo": "",
                "totais": {"copiadas": 0, "puladas": 0, "erros": 0, "bytes": 0},
                "pastas": [], "problemas": [], "contas": [], "log": a.log}

    @staticmethod
    def _rota(a, modo, contas):
        """Linha em fonte monoespaçada sob o título do Andamento."""
        return {"imap2imap": f"{a.src_user} · {a.src_host} → {a.dst_host}",
                "export": f"{a.src_user} · {a.src_host} → {a.dir}",
                "import": f"{a.dir} → {a.dst_user} · {a.dst_host}",
                "lote": f"{plural(contas, 'conta', 'contas')} · {a.src_host} → {a.dst_host}"}[modo]

    def _sincronizar_relatorio(self):
        self.rel["totais"] = self._totais()
        self.rel["duracao"] = (self.rel["fim"] or time.time()) - self.rel["inicio"]

    def _salvar_relatorio(self):
        """Grava o relatório a cada pasta concluída: se o PC desligar, nada se perde."""
        if not self.rel:
            return
        self._sincronizar_relatorio()
        try:
            salvar_json(os.path.join(HIST_DIR, f"{self.rel['id']}.json"), self.rel)
        except OSError:
            pass
        self._sujo = False
        self._salvo_em = time.time()

    def iniciar(self):
        if self.rodando:
            return
        try:
            a = self._montar_args()
        except ValueError as e:
            messagebox.showwarning("Faltam dados", str(e), parent=self)
            return
        self._salvar_perfil()
        self.parar.clear()
        self._resetar_painel()
        modo = self.v["modo"].get()
        lote = modo == "lote"
        self.rel = self._novo_relatorio(a, modo)
        self.lbl_rota.configure(text=self._rota(a, modo, len(self.contas_csv)))
        self.tab_pastas.colunas_visiveis([c for c in VISIVEIS_ANDAMENTO if lote or c != "conta"])
        self.tab_problemas.colunas_visiveis([c[0] for c in COLS_PROBLEMAS if lote or c[0] != "conta"])
        self.abas.visivel("contas", lote)
        self.abas.mostrar("contas" if lote else "pastas")
        if modo in ("imap2imap", "export") and self.pastas_origem:
            for nome, (sel, n) in self.pastas_origem.items():
                if sel:  # já aparecem como pendentes: dá para ver o que falta
                    self._nova_linha(nome, n, "pendente")
                    self.total_geral += n
                    self.total_pastas += 1
        if lote:
            self.contas_total = len(self.contas_csv)
            for n, (src, dst) in enumerate(self.contas_csv, 1):
                c = {"n": n, "src": src, "dst": dst, "status": "pendente", "detalhe": "",
                     "copiadas": 0, "puladas": 0, "erros": 0}
                self.rel["contas"].append(c)
                self.tab_contas.inserir(*linha_conta(c), iid=f"c{n}")
        self.rodando = True
        self.inicio = time.time()
        self._salvar_relatorio()
        self._estado("rodando")
        self.btn_iniciar.state(["disabled"])
        if a.modo in ("export", "import"):
            self.btn_abrir_dir.pack(side="left", padx=(self.px(8), 0))
        else:
            self.btn_abrir_dir.pack_forget()
        self.progresso.definir("pasta", "Conectando…", "")
        self.progresso.indeterminado("pasta")
        self._atualizar_progresso()
        self._kpis()
        self.mostrar("progresso")
        threading.Thread(target=self._trabalhar, args=(a,), daemon=True).start()
        self.after(1000, self._relogio)

    def _trabalhar(self, a):
        """Thread de trabalho: o mesmo fluxo do main() do CLI."""
        ui = self.ui
        socket.setdefaulttimeout(a.timeout)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
            ui.abrir_log(a.log)
        except OSError as e:
            ui.aviso(f"não foi possível abrir o log {a.log}: {e}")
        inicio = time.time()
        ui.titulo(f"Migração de e-mails · modo {a.modo} · {time.strftime('%Y-%m-%d %H:%M:%S')}")
        estado = "falha"
        try:
            resumo, ok = me.executar(a)
            estado = "ok" if ok else "falha"
        except Cancelado:
            resumo, ok, estado = ("Interrompido pelo usuário (pode rodar de novo para "
                                  "continuar de onde parou)."), False, "cancelado"
            ui.aviso(resumo)
        except Exception as e:
            resumo, ok = f"ERRO FATAL: {e}", False
            ui.erro(resumo)
        ui.texto(f"{'Concluído' if ok else 'Terminou com problemas'} "
                 f"em {me.fmt_tempo(time.time() - inicio)}")
        me.notificar(a, f"[migração] {'OK' if ok else 'COM PROBLEMAS'}", resumo)
        ui.fechar_log()
        self.fila.put(("fim", estado, resumo))

    def parar_execucao(self):
        if self.rodando and messagebox.askyesno(
                "Parar migração", "Parar agora? O que já foi copiado fica no destino e, "
                "ao rodar de novo, a migração continua de onde parou.", parent=self):
            self.parar.set()
            self._estado("parando")

    def _relogio(self):
        if not self.rodando:
            return
        agora = time.time()
        total = self._totais()["bytes"]
        self.amostras.append((agora, total))
        self.amostras = [s for s in self.amostras if agora - s[0] <= 5.5]
        t0, b0 = self.amostras[0]
        if agora - t0 >= 1:
            self.vel.adicionar(agora - self.inicio, (total - b0) / (agora - t0))
        self._kpis()
        if self._sujo and agora - self._salvo_em > 3:
            self._salvar_relatorio()
        self.after(1000, self._relogio)

    # ------------------------------------------------------ fila de eventos
    def _drenar(self):
        """Consome os eventos do motor em lote, 20 vezes por segundo."""
        logs, progresso = [], None
        try:
            for _ in range(5000):
                ev = self.fila.get_nowait()
                if ev[0] == "progresso":
                    progresso = ev  # só o último importa
                elif ev[0] == "log":
                    logs.append(ev[1:])
                    self._registrar_problema(*ev[1:])
                else:
                    if progresso:
                        self._tratar(progresso)
                        progresso = None
                    self._tratar(ev)
        except queue.Empty:
            pass
        if progresso:
            self._tratar(progresso)
        if logs:
            self._escrever_logs(logs)
        if self.rodando and self._prog_mudou:
            self._prog_mudou = False
            self._atualizar_linha_viva()
            self._atualizar_progresso()
            self._kpis()
        self.after(50, self._drenar)

    def _registrar_problema(self, nivel, texto):
        if nivel == "info" and self.rodando and self.atual["inicio"] is None:
            # antes de começar a copiar, o resumo da comparação aparece ao lado da pasta
            self.progresso.definir("pasta", direita=decodificar_utf7(texto))
        if not self.rodando or nivel not in ("erro", "aviso"):
            return
        pr = {"hora": time.strftime("%H:%M:%S"), "nivel": nivel, "conta": self.conta_atual,
              "pasta": self.pasta_ctx, "texto": decodificar_utf7(texto)}
        self.rel["problemas"].append(pr)
        self.tab_problemas.inserir(*linha_problema(pr), rolar=True)
        self.abas.titulo("problemas", f"Problemas ({num(len(self.rel['problemas']))})")
        if nivel == "erro" and self.linha_atual:
            self.erros_atual += 1
            self._prog_mudou = True
        self._sujo = True

    def _nova_linha(self, nome, mensagens, status, ja=0):
        iid = f"{self.conta_n}:{nome}"
        p = {"conta": self.conta_atual, "pasta": nome, "status": status, "mensagens": mensagens,
             "copiadas": 0, "puladas": ja, "erros": 0, "bytes": 0, "segundos": 0}
        self.linhas[iid] = p
        self.rel["pastas"].append(p)
        self.tab_pastas.inserir(*linha_pasta(p), iid=iid, rolar=True)
        return iid

    def _atualizar_linha(self, iid):
        self.tab_pastas.atualizar(iid, *linha_pasta(self.linhas[iid]))

    def _atualizar_linha_viva(self):
        if not self.linha_atual:
            return
        p, a = self.linhas[self.linha_atual], self.atual
        p.update(copiadas=max(0, a["feito"] - self.erros_atual), erros=self.erros_atual,
                 bytes=a["bytes"], segundos=time.time() - (a["inicio_pasta"] or time.time()))
        self._atualizar_linha(self.linha_atual)

    def _atualizar_conta(self, n):
        c = self.rel["contas"][n - 1]
        self.tab_contas.atualizar(f"c{n}", *linha_conta(c))

    def _fechar_pasta_parcial(self):
        """Pasta interrompida no meio: registra o que chegou a ser feito."""
        if not self.linha_atual:
            return
        a, agora = self.atual, time.time()
        p = self.linhas[self.linha_atual]
        cop = max(0, a["feito"] - self.erros_atual)
        p.update(status="parada", copiadas=cop, puladas=a["ja"], erros=self.erros_atual,
                 bytes=a["bytes"], segundos=agora - (a["inicio_pasta"] or agora))
        self._atualizar_linha(self.linha_atual)
        self.tot["copiadas"] += cop
        self.tot["puladas"] += a["ja"]
        self.tot["erros"] += self.erros_atual
        self.tot["bytes"] += a["bytes"]
        self.proc_concluidas += a["ja"] + a["feito"]
        if self.contas_total and self.conta_n:
            c = self.rel["contas"][self.conta_n - 1]
            c["copiadas"] += cop
            c["puladas"] += a["ja"]
            c["erros"] += self.erros_atual
        self.linha_atual = None
        self.atual = self._novo_atual()
        self.erros_atual = 0

    def _tratar(self, ev):
        tipo = ev[0]
        if tipo == "pasta":
            self._fechar_pasta_parcial()
            texto = decodificar_utf7(ev[1])
            self.pasta_ctx = re.sub(r" \(\d+ \w+\)$", "", texto).split(" → ")[0]
            self.atual = self._novo_atual()
            self.atual["inicio_pasta"] = time.time()
            self.erros_atual = 0
            de = f" · pasta {self.pastas_feitas + 1} de {self.total_pastas}" if self.total_pastas else ""
            self.progresso.definir("pasta", self.pasta_ctx, f"comparando com o destino…{de}")
            self.progresso.indeterminado("pasta")
            self._prog_mudou = True
        elif tipo == "pasta_ini":
            _, nome, mensagens, ja = ev
            self.atual.update(mensagens=mensagens, ja=ja)
            iid = f"{self.conta_n}:{nome}"
            if iid in self.linhas:
                self.linhas[iid].update(status="andamento", mensagens=mensagens, puladas=ja)
            else:
                self._nova_linha(nome, mensagens, "andamento", ja)
            self.linha_atual = iid
            self.pasta_ctx = decodificar_utf7(nome)
            self._atualizar_linha(iid)
            self.tab_pastas.tv.see(iid)
            self._prog_mudou = True
        elif tipo == "progresso":
            _, feito, total, bytes_ = ev
            a = self.atual
            if a["inicio"] is None:
                a["inicio"] = time.time()
                self.progresso.alvo("pasta", 0, instantaneo=True)
            a.update(feito=feito, total=total, bytes=bytes_)
            self._prog_mudou = True
        elif tipo == "pasta_ok":
            nome, total, cop, pul, err = ev[1]
            iid = self.linha_atual or self._nova_linha(nome, total, "andamento")
            p = self.linhas[iid]
            p.update(mensagens=total, copiadas=cop, puladas=pul, erros=err,
                     bytes=self.atual["bytes"], status="erros" if err else "ok",
                     segundos=time.time() - (self.atual["inicio_pasta"] or time.time()))
            self._atualizar_linha(iid)
            for k, v in (("copiadas", cop), ("puladas", pul), ("erros", err),
                         ("bytes", self.atual["bytes"])):
                self.tot[k] += v
            if self.contas_total and self.conta_n:
                c = self.rel["contas"][self.conta_n - 1]
                for k, v in (("copiadas", cop), ("puladas", pul), ("erros", err)):
                    c[k] += v
                self._atualizar_conta(self.conta_n)
            self.proc_concluidas += total
            self.pastas_feitas += 1
            self.linha_atual = None
            self.atual = self._novo_atual()
            self.erros_atual = 0
            self.progresso.definir("pasta", direita=f"{num(total)} / {num(total)}")
            self.progresso.alvo("pasta", 1)
            self._prog_mudou = True
            self._salvar_relatorio()
        elif tipo == "conta":
            _, n, total, src, dst = ev
            self._fechar_pasta_parcial()
            self.conta_n, self.conta_atual, self.pasta_ctx = n, src, ""
            self.lbl_rota.configure(text=f"conta {n} de {total} · {src} → {dst}")
            if n <= len(self.rel["contas"]):
                self.rel["contas"][n - 1]["status"] = "andamento"
                self._atualizar_conta(n)
                self.tab_contas.tv.see(f"c{n}")
            self._prog_mudou = True
        elif tipo == "conta_ok":
            _, n, status, detalhe = ev
            self._fechar_pasta_parcial()
            if n <= len(self.rel["contas"]):
                self.rel["contas"][n - 1].update(status=status, detalhe=detalhe)
                self._atualizar_conta(n)
            self.contas_feitas = n
            self._prog_mudou = True
            self._salvar_relatorio()
        elif tipo == "fim":
            self._fim(ev[1], ev[2])
        elif tipo == "teste":
            _, lado, ok, msg = ev
            card = self.card_src if lado == "src" else self.card_dst
            card.botao.state(["!disabled"])
            card.mostrar_status(msg, "ok" if ok else "erro")
        elif tipo == "pastas":
            self._mostrar_pastas(ev[1], ev[2])
        elif tipo == "previa_dir":
            if ev[1] == self.v["dir"].get().strip():
                self.dir_previa.configure(text=ev[2])
        elif tipo == "atualizacao":
            self._resultado_busca(*ev[1:])
        elif tipo == "download":
            if self.dialogo and self.dialogo.winfo_exists():
                self.dialogo.progresso(ev[1], ev[2])
        elif tipo == "download_fim":
            self._fim_download(ev[1], ev[2])

    # --------------------------------------------------------- atualização
    def _busca_periodica(self):
        self.procurar_atualizacao()
        self.after(6 * 3600 * 1000, self._busca_periodica)

    def procurar_atualizacao(self, manual=False):
        if not atualizador.empacotado():
            if manual:
                messagebox.showinfo("Atualizações", "Rodando pelo Python não há atualização "
                                    "automática: ela funciona no MigradorEmails.exe.", parent=self)
            return
        if self.atualizacao_pronta or self.baixando:
            if manual:
                self.abrir_atualizacao()
            return

        def tarefa():
            try:
                self.fila.put(("atualizacao", atualizador.buscar(), None, manual))
            except Exception as e:  # sem internet, GitHub fora do ar...
                atualizador.registrar(f"falha ao procurar atualização: {e!r}")
                self.fila.put(("atualizacao", None, str(e), manual))
        threading.Thread(target=tarefa, daemon=True).start()

    def _resultado_busca(self, info, erro, manual):
        if info:
            nova = not self.atualizacao or self.atualizacao["versao"] != info["versao"]
            self.atualizacao = info
            self.lateral.definir_aviso(f"⬆ Nova versão {info['versao']}", "clique para atualizar")
            if manual or (nova and not self.rodando):  # durante migração, só o aviso discreto
                self.abrir_atualizacao()
        elif manual and erro:
            messagebox.showwarning("Atualizações", f"Não foi possível verificar agora:\n{erro}",
                                   parent=self)
        elif manual:
            messagebox.showinfo("Atualizações",
                                f"Você já está na versão mais recente ({VERSAO}).", parent=self)

    def abrir_atualizacao(self):
        if not (self.atualizacao or self.atualizacao_pronta):
            return
        if self.dialogo and self.dialogo.winfo_exists():
            self.dialogo.atualizar()
            self.dialogo.lift()
        else:
            self.dialogo = DialogoAtualizacao(self)

    def baixar_atualizacao(self):
        if self.baixando or not self.atualizacao:
            return
        self.baixando = True
        self.abrir_atualizacao()
        info = self.atualizacao

        def tarefa():
            try:
                dados = atualizador.baixar(info, lambda f, t: self.fila.put(("download", f, t)))
                self.fila.put(("download_fim", dados, None))
            except PermissionError:
                self.fila.put(("download_fim", None, (
                    "Sem permissão para gravar na pasta onde está o MigradorEmails.exe. Mova o "
                    "programa para uma pasta sua (ex.: Documentos ou Área de Trabalho) e tente "
                    "de novo, ou baixe a versão nova pela página.")))
            except Exception as e:
                atualizador.registrar(f"falha no download: {e!r}")
                self.fila.put(("download_fim", None, f"Falha no download: {e}"))
        threading.Thread(target=tarefa, daemon=True).start()

    def _fim_download(self, dados, erro):
        self.baixando = False
        if dados:
            self.atualizacao_pronta = dados
            self.lateral.definir_aviso("✔ Atualização pronta", "clique para instalar")
        if self.dialogo and self.dialogo.winfo_exists():
            self.dialogo.atualizar(erro)
        elif erro:
            messagebox.showwarning("Atualização", erro, parent=self)

    def reiniciar_para_atualizar(self):
        self._fechar(reiniciar=True)

    def _fim(self, estado, resumo):
        self._fechar_pasta_parcial()
        self.rodando = False
        for c in self.rel["contas"]:
            if c["status"] == "andamento":
                c["status"] = "parada"
                self._atualizar_conta(c["n"])
        self.rel.update(estado=estado, resumo=resumo, fim=time.time())
        self._salvar_relatorio()
        self._kpis()
        self._atualizar_progresso()
        self._estado(estado)
        self.btn_iniciar.state(["!disabled"])

        t = self.rel["totais"]
        numeros = (f"{plural(t['copiadas'], 'copiada', 'copiadas')} · "
                   f"{num(t['puladas'])} já existiam · {plural(t['erros'], 'erro', 'erros')} · "
                   f"{me.fmt_bytes(t['bytes'])} em {me.fmt_tempo(self.rel['duracao'])}")
        falhas = sum(1 for c in self.rel["contas"] if c["status"] == "FALHA")
        if estado == "ok":
            titulo, texto = "Migração concluída sem erros", numeros
        elif estado == "cancelado":
            titulo = "Migração interrompida"
            texto = (f"{numeros}\nRode de novo para continuar de onde parou: "
                     "o que já foi copiado não será duplicado.")
        elif resumo.startswith("ERRO FATAL"):
            titulo, texto = "A migração falhou", f"{resumo}\n{numeros}"
        else:
            titulo = (plural(falhas, "conta falhou", "contas falharam") if falhas else
                      f"Terminou com {plural(t['erros'], 'erro', 'erros')}" if t["erros"] else
                      "Terminou com problemas")
            onde = "as abas Problemas e Contas" if falhas else "a aba Problemas"
            texto = (f"{numeros}\nVeja {onde}. Rode de novo para tentar só o que "
                     "faltou: nada será duplicado.")
        self._mostrar_banner(estado, titulo, texto)
        if self.rel["problemas"] and estado != "ok":
            self.abas.mostrar("problemas")
        self._chamar_atencao()

    def _chamar_atencao(self):
        self.bell()
        try:  # pisca na barra de tarefas se a janela não estiver em foco
            if self.focus_displayof() is None:
                import ctypes
                ctypes.windll.user32.FlashWindow(int(self.wm_frame(), 16), True)
        except Exception:
            pass

    # -------------------------------------------------------------- perfil
    def _carregar_perfil(self):
        try:
            with open(PERFIL, encoding="utf-8") as f:
                dados = json.load(f)
        except (OSError, ValueError):
            return
        for k in CAMPOS_PERFIL:
            if isinstance(dados.get(k), str):
                self.v[k].set(dados[k])

    def _salvar_perfil(self):
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(PERFIL, "w", encoding="utf-8") as f:
                json.dump({k: self.v[k].get() for k in CAMPOS_PERFIL}, f, indent=2,
                          ensure_ascii=False)
        except OSError:
            pass

    def _fechar(self, reiniciar=False):
        if self.rodando:
            if not messagebox.askyesno(
                    "Sair", "Uma migração está em andamento. Parar e sair?\n"
                            "Ao abrir de novo, ela continua de onde parou.", parent=self):
                return
            self.parar.set()
            self._fechar_pasta_parcial()
            self.rel.update(estado="cancelado", fim=time.time(),
                            resumo="O aplicativo foi fechado durante a migração.")
            self._salvar_relatorio()
        self._salvar_perfil()
        if self.atualizacao_pronta and atualizador.empacotado():
            try:  # ao fechar, a versão baixada é instalada; ao reiniciar, já abre nela
                atualizador.instalar(reabrir=reiniciar)
            except OSError as e:
                atualizador.registrar(f"falha ao iniciar a instalação: {e!r}")
        self.destroy()


def main():
    if atualizador.concluir(sys.argv):  # executável novo terminando a própria instalação
        return
    if "--verificar" in sys.argv:  # usado no build: o .exe abre a interface e fecha (código 0)
        App().destroy()
        return
    if os.name == "nt":
        try:  # textos nítidos em telas com escala (125%, 150%...)
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MigradorEmails")
        except (AttributeError, OSError):
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
