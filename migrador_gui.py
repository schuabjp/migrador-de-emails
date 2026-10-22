#!/usr/bin/env python3
"""
Migrador de E-mails — aplicativo desktop.

Interface gráfica para o migrar_email.py: usa o mesmo motor (mesmos modos,
deduplicação, reconexão e notificações); esta camada só coleta os dados,
testa as conexões e mostra o andamento com gráficos.

    python migrador_gui.py        (ou pythonw migrador_gui.py, sem console)
"""
import base64
import csv
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrar_email as me  # noqa: E402

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                       "MigradorEmails")
PERFIL = os.path.join(APP_DIR, "perfil.json")
LOG_PADRAO = os.path.join(APP_DIR, "migracao.log")
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
    "fundo": "#f4f4f1", "superficie": "#fcfcfb", "borda": "#e1e0d9",
    "tinta": "#0b0b0b", "tinta2": "#52514e", "muda": "#898781",
    "grade": "#e1e0d9", "eixo": "#c3c2b7",
    "destaque": "#2a78d6", "destaque_esc": "#1c5cab", "destaque_claro": "#e3eefc",
    "area": "#cde2fb", "selecionado": "#eef4fc",
    "ok": "#0ca30c", "ok_texto": "#006300", "aviso": "#fab219", "aviso_texto": "#7a5200",
    "erro": "#d03b3b", "erro_texto": "#a32626",
    "lateral": "#16181d", "lateral_hover": "#22252d", "lateral_ativo": "#2a2e38",
    "lateral_texto": "#c3c2b7",
}
SERIES = {"copiadas": COR["destaque"], "puladas": COR["eixo"], "erros": COR["erro"]}
PILULAS = {  # estado -> (texto, fundo, tinta)
    "parado": ("Aguardando", "#efeeea", COR["tinta2"]),
    "rodando": ("● Em andamento", COR["destaque_claro"], COR["destaque_esc"]),
    "parando": ("● Parando…", "#fdf0d5", COR["aviso_texto"]),
    "ok": ("✔ Concluído", "#e3f4e3", COR["ok_texto"]),
    "falha": ("✖ Terminou com problemas", "#fbe5e5", COR["erro_texto"]),
    "cancelado": ("⚠ Interrompido", "#fdf0d5", COR["aviso_texto"]),
}
FONTE = "Segoe UI"


def num(n):
    return f"{n:,}".replace(",", ".")


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

    def pasta_concluida(self, linha):
        self.fila.put(("pasta_ok", [linha[0]] + [int(x) for x in linha[1:]]))


# ----------------------------------------------------------------- gráficos
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
    r = canvas.create_rectangle(x, y, x + w, y + h, fill=COR["superficie"],
                                outline=COR["eixo"], tags="hover")
    canvas.tag_lower(r, t)


class GraficoVelocidade(tk.Canvas):
    """Velocidade de transferência (média móvel de 5 s) desde o início."""
    LIMITE = 600

    def __init__(self, pai, px):
        super().__init__(pai, bg=COR["superficie"], highlightthickness=0, height=px(170))
        self.px = px
        self.pontos = []  # (segundos desde o início, bytes/s)
        self.xs = []
        self.bind("<Configure>", lambda e: self.desenhar())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def limpar(self):
        self.pontos = []
        self.desenhar()

    def adicionar(self, t, v):
        self.pontos.append((t, v))
        if len(self.pontos) > self.LIMITE:  # junta pares: o gráfico cobre a execução toda
            p = self.pontos
            self.pontos = [((p[i][0] + p[i + 1][0]) / 2, (p[i][1] + p[i + 1][1]) / 2)
                           for i in range(0, len(p) - 1, 2)] + ([p[-1]] if len(p) % 2 else [])
        self.desenhar()

    def desenhar(self):
        px, p = self.px, self.pontos
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        self.esq, dir_, self.topo, self.base = px(70), px(14), px(10), h - px(24)
        if len(p) < 2:
            self.create_line(self.esq, self.base, w - dir_, self.base, fill=COR["eixo"])
            self.create_text(w / 2, h / 2, text="O gráfico aparece quando a transferência começar",
                             fill=COR["muda"], font=(FONTE, 9))
            self.xs = []
            return
        self.vmax = maximo_redondo(max(v for _, v in p))
        for i in range(5):
            y = self.base - (self.base - self.topo) * i / 4
            self.create_line(self.esq, y, w - dir_, y,
                             fill=COR["eixo"] if i == 0 else COR["grade"])
            self.create_text(self.esq - px(8), y, anchor="e", fill=COR["muda"],
                             font=(FONTE, 8), text=f"{me.fmt_bytes(self.vmax * i / 4)}/s")
        t0, t1 = p[0][0], p[-1][0]
        larg = w - dir_ - self.esq
        self.xs = [self.esq + (t - t0) / ((t1 - t0) or 1) * larg for t, _ in p]
        ys = [self.base - v / self.vmax * (self.base - self.topo) for _, v in p]
        coords = [c for xy in zip(self.xs, ys) for c in xy]
        self.create_polygon(self.xs[0], self.base, *coords, self.xs[-1], self.base,
                            fill=COR["area"], outline="")
        self.create_line(*coords, fill=COR["destaque"], width=2)
        for t, anc, x in ((t0, "nw", self.esq), (t1, "ne", w - dir_)):
            self.create_text(x, self.base + px(5), anchor=anc, fill=COR["muda"],
                             font=(FONTE, 8), text=me.fmt_tempo(t))

    def _hover(self, e):
        self.delete("hover")
        if not self.xs or not self.esq <= e.x <= self.xs[-1] + 4:
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


class GraficoPastas(tk.Canvas):
    """Barras horizontais empilhadas: copiadas / já existiam / erros por pasta."""

    def __init__(self, pai, px):
        super().__init__(pai, bg=COR["superficie"], highlightthickness=0, height=px(170))
        self.px = px
        self.linhas = []  # [nome, total, copiadas, puladas, erros]
        self.fonte = tkfont.Font(family=FONTE, size=9)
        self.bind("<Configure>", lambda e: self.desenhar())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))
        self.bind("<MouseWheel>", self._rolar)

    def limpar(self):
        self.linhas = []
        self.desenhar()

    def adicionar(self, linha):
        self.linhas.append(linha)
        self.desenhar()
        self.yview_moveto(1)

    def _rolar(self, e):
        if self.bbox("barras") and self.bbox("barras")[3] > self.winfo_height():
            self.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _cortar(self, texto, largura):
        if self.fonte.measure(texto) <= largura:
            return texto
        while texto and self.fonte.measure(texto + "…") > largura:
            texto = texto[:-1]
        return texto + "…"

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        self.altura = px(26)
        self.topo = px(34)
        # legenda
        x = px(4)
        for chave, rotulo in (("copiadas", "Copiadas"), ("puladas", "Já existiam"),
                              ("erros", "Erros")):
            self.create_rectangle(x, px(8), x + px(10), px(18), fill=SERIES[chave], outline="")
            t = self.create_text(x + px(15), px(13), anchor="w", text=rotulo,
                                 fill=COR["tinta2"], font=(FONTE, 9))
            x = self.bbox(t)[2] + px(16)
        if not self.linhas:
            self.create_text(w / 2, (h + self.topo) / 2, fill=COR["muda"], font=(FONTE, 9),
                             text="Cada pasta aparece aqui quando termina")
            self.configure(scrollregion=(0, 0, w, h))
            return
        self.esq = min(px(170), int(w * 0.35))
        larg = w - self.esq - px(64)
        maior = max(max(l[1], l[2] + l[3] + l[4]) for l in self.linhas) or 1
        for i, (nome, total, cop, pul, err) in enumerate(self.linhas):
            yc = self.topo + i * self.altura + self.altura / 2
            self.create_text(self.esq - px(10), yc, anchor="e", fill=COR["tinta"],
                             font=self.fonte, tags="barras",
                             text=self._cortar(decodificar_utf7(nome), self.esq - px(14)))
            x = self.esq
            for valor, chave in ((cop, "copiadas"), (pul, "puladas"), (err, "erros")):
                if not valor:
                    continue
                seg = max(px(2), valor / maior * larg)
                inicio = x + (2 if x > self.esq else 0)  # 2px de respiro entre segmentos
                self.create_rectangle(inicio, yc - px(5), max(inicio + 1, x + seg), yc + px(5),
                                      fill=SERIES[chave], outline="", tags="barras")
                x += seg
            if x == self.esq:  # pasta vazia: só a linha de base
                self.create_line(x, yc - px(5), x, yc + px(5), fill=COR["eixo"], tags="barras")
            self.create_text(x + px(8), yc, anchor="w", fill=COR["tinta2"],
                             font=(FONTE, 9), text=num(total), tags="barras")
        fim = self.topo + len(self.linhas) * self.altura + px(6)
        self.configure(scrollregion=(0, 0, w, max(h, fim)))

    def _hover(self, e):
        self.delete("hover")
        y = self.canvasy(e.y)
        i = int((y - self.topo) // self.altura) if self.linhas else -1
        if not 0 <= i < len(self.linhas) or y < self.topo:
            return
        nome, total, cop, pul, err = self.linhas[i]
        dica(self, e.x, y, f"{decodificar_utf7(nome)} — {num(total)} mensagens\n"
                           f"{num(cop)} copiadas · {num(pul)} já existiam · {num(err)} erros")


# -------------------------------------------------------------- componentes
class CartaoServidor(tk.Frame):
    """Servidor IMAP + usuário + senha + botão de teste."""

    def __init__(self, app, pai, lado, titulo, subtitulo):
        super().__init__(pai, bg=COR["superficie"], highlightthickness=1,
                         highlightbackground=COR["borda"])
        self.app, self.lado = app, lado
        v = app.v
        corpo = ttk.Frame(self, style="Cartao.TFrame", padding=app.px(16))
        corpo.pack(fill="both", expand=True)
        corpo.columnconfigure((0, 1), weight=1, uniform="campo")
        ttk.Label(corpo, text=titulo, style="CartaoTitulo.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(corpo, text=subtitulo, style="CartaoMudo.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w")

        app.rotulo(corpo, "Servidor IMAP", 2, 0, 2)
        app.rotulo(corpo, "Porta", 2, 2)
        ttk.Combobox(corpo, textvariable=v[f"{lado}_host"], values=SERVIDORES).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        ttk.Entry(corpo, textvariable=v[f"{lado}_port"], width=7).grid(row=3, column=2, sticky="ew")

        self.credenciais = [app.rotulo(corpo, "Usuário (e-mail)", 4, 0),
                            app.rotulo(corpo, "Senha (ou senha de app)", 4, 1, 2)]
        e = ttk.Entry(corpo, textvariable=v[f"{lado}_user"])
        e.grid(row=5, column=0, sticky="ew", padx=(0, 8))
        linha = ttk.Frame(corpo, style="Cartao.TFrame")
        linha.grid(row=5, column=1, columnspan=2, sticky="ew")
        linha.columnconfigure(0, weight=1)
        self.senha = ttk.Entry(linha, textvariable=v[f"{lado}_pass"], show="•")
        self.senha.grid(row=0, column=0, sticky="ew")
        self.olho = ttk.Button(linha, text="Mostrar", width=8, style="Discreto.TButton",
                               command=self._alternar_senha)
        self.olho.grid(row=0, column=1, padx=(6, 0))
        self.credenciais += [e, linha]
        self.nota_lote = ttk.Label(corpo, style="CartaoMudo.TLabel",
                                   text="Usuários e senhas vêm do arquivo CSV.")
        self.nota_lote.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        rodape = ttk.Frame(corpo, style="Cartao.TFrame")
        rodape.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        rodape.columnconfigure(1, weight=1)
        self.botao = ttk.Button(rodape, text="Testar conexão", style="Secundario.TButton",
                                command=lambda: app.testar_conexao(lado))
        self.botao.grid(row=0, column=0, sticky="w")
        self.status = ttk.Label(rodape, text="", style="Cartao.TLabel", wraplength=app.px(260))
        self.status.grid(row=0, column=1, sticky="w", padx=(10, 0))

    def _alternar_senha(self):
        oculta = self.senha.cget("show") == "•"
        self.senha.configure(show="" if oculta else "•")
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


class Kpi(tk.Frame):
    def __init__(self, pai, app, rotulo, marca=None):
        super().__init__(pai, bg=COR["superficie"], highlightthickness=1,
                         highlightbackground=COR["borda"])
        topo = tk.Frame(self, bg=COR["superficie"])
        topo.pack(fill="x", padx=app.px(14), pady=(app.px(12), 0))
        if marca:
            tk.Label(topo, text="●", fg=marca, bg=COR["superficie"],
                     font=(FONTE, 8)).pack(side="left", padx=(0, 4))
        tk.Label(topo, text=rotulo, fg=COR["tinta2"], bg=COR["superficie"],
                 font=(FONTE, 9)).pack(side="left")
        self.valor = tk.Label(self, text="—", fg=COR["tinta"], bg=COR["superficie"],
                              font=(FONTE, 18, "bold"), anchor="w")
        self.valor.pack(fill="x", padx=app.px(14), pady=(0, app.px(12)))

    def set(self, texto):
        self.valor.configure(text=texto)


# ---------------------------------------------------------------- aplicativo
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.escala = self.winfo_fpixels("1i") / 96
        self.title("Migrador de E-mails")
        self.geometry(f"{self.px(1200)}x{min(self.px(840), self.winfo_screenheight() - 80)}")
        self.minsize(self.px(1020), self.px(680))
        self.configure(bg=COR["fundo"])

        self.fila, self.parar = queue.Queue(), threading.Event()
        self.ui = GuiUi(self.fila, self.parar)
        me.ui = self.ui  # o motor passa a falar com a janela
        self.rodando = False
        self.pastas_origem = {}  # nome bruto -> [selecionada, mensagens]

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
        self.after(100, self._drenar)

    def px(self, n):
        return int(n * self.escala)

    # ------------------------------------------------------------- estilos
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        f = (FONTE, 10)
        s.configure(".", font=f, background=COR["fundo"], foreground=COR["tinta"])
        s.configure("TFrame", background=COR["fundo"])
        s.configure("Cartao.TFrame", background=COR["superficie"])
        for nome, cor, fonte in (("Cartao", COR["tinta"], f),
                                 ("CartaoTitulo", COR["tinta"], (FONTE, 12, "bold")),
                                 ("CartaoMudo", COR["muda"], (FONTE, 9)),
                                 ("Campo", COR["tinta2"], (FONTE, 9)),
                                 ("CartaoOk", COR["ok_texto"], (FONTE, 9)),
                                 ("CartaoErro", COR["erro_texto"], (FONTE, 9))):
            s.configure(f"{nome}.TLabel", background=COR["superficie"], foreground=cor, font=fonte)
        s.configure("Pagina.TLabel", font=(FONTE, 20, "bold"))
        s.configure("PaginaSub.TLabel", foreground=COR["tinta2"], font=(FONTE, 10))
        s.configure("Passo.TLabel", foreground=COR["tinta2"], font=(FONTE, 10, "bold"))
        s.configure("Rodape.TLabel", foreground=COR["tinta2"], font=(FONTE, 10))

        campo = dict(fieldbackground="white", bordercolor=COR["eixo"], lightcolor="white",
                     darkcolor="white", padding=(8, 5), insertcolor=COR["tinta"])
        s.configure("TEntry", **campo)
        s.configure("TCombobox", **campo, arrowcolor=COR["tinta2"])
        for w in ("TEntry", "TCombobox"):
            s.map(w, bordercolor=[("focus", COR["destaque"])],
                  lightcolor=[("focus", COR["destaque"])],
                  fieldbackground=[("disabled", COR["fundo"]), ("readonly", "white")])

        s.configure("TButton", padding=(14, 7), borderwidth=1)
        s.configure("Primario.TButton", background=COR["destaque"], foreground="white",
                    bordercolor=COR["destaque"], lightcolor=COR["destaque"],
                    darkcolor=COR["destaque"], font=(FONTE, 11, "bold"), padding=(22, 10))
        s.map("Primario.TButton",
              background=[("disabled", COR["eixo"]), ("pressed", COR["destaque_esc"]),
                          ("active", "#256abf")],
              bordercolor=[("disabled", COR["eixo"])],
              lightcolor=[("disabled", COR["eixo"]), ("active", "#256abf")],
              darkcolor=[("disabled", COR["eixo"]), ("active", "#256abf")])
        for nome, borda in (("Secundario", COR["eixo"]), ("Discreto", COR["borda"])):
            s.configure(f"{nome}.TButton", background=COR["superficie"], foreground=COR["tinta"],
                        bordercolor=borda, lightcolor=COR["superficie"], darkcolor=COR["superficie"])
            s.map(f"{nome}.TButton", background=[("active", COR["fundo"])],
                  lightcolor=[("active", COR["fundo"])], darkcolor=[("active", COR["fundo"])],
                  foreground=[("disabled", COR["muda"])])
        s.configure("Discreto.TButton", padding=(6, 4), font=(FONTE, 9))
        s.configure("Perigo.TButton", background=COR["superficie"], foreground=COR["erro_texto"],
                    bordercolor="#e9b3b3", lightcolor=COR["superficie"], darkcolor=COR["superficie"])
        s.map("Perigo.TButton", background=[("active", "#fbe5e5")],
              lightcolor=[("active", "#fbe5e5")], darkcolor=[("active", "#fbe5e5")],
              foreground=[("disabled", COR["muda"])], bordercolor=[("disabled", COR["borda"])])

        s.configure("Barra.Horizontal.TProgressbar", troughcolor=COR["grade"],
                    background=COR["destaque"], bordercolor=COR["grade"],
                    lightcolor=COR["destaque"], darkcolor=COR["destaque"],
                    thickness=self.px(10))
        s.configure("Treeview", background=COR["superficie"], fieldbackground=COR["superficie"],
                    bordercolor=COR["borda"], rowheight=self.px(26), font=f)
        s.configure("Treeview.Heading", background=COR["fundo"], foreground=COR["tinta2"],
                    bordercolor=COR["borda"], font=(FONTE, 9, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", COR["selecionado"])],
              foreground=[("selected", COR["tinta"])])
        s.configure("TCheckbutton", background=COR["superficie"])
        s.configure("Vertical.TScrollbar", background=COR["fundo"], troughcolor=COR["superficie"],
                    bordercolor=COR["superficie"], arrowcolor=COR["tinta2"])

    def rotulo(self, pai, texto, linha, coluna, colspan=1):
        lb = ttk.Label(pai, text=texto, style="Campo.TLabel")
        lb.grid(row=linha, column=coluna, columnspan=colspan, sticky="w", pady=(10, 3))
        return lb

    def cartao(self, pai, **pack):
        externo = tk.Frame(pai, bg=COR["superficie"], highlightthickness=1,
                           highlightbackground=COR["borda"])
        corpo = ttk.Frame(externo, style="Cartao.TFrame", padding=self.px(16))
        corpo.pack(fill="both", expand=True)
        if pack:
            externo.pack(**pack)
        return externo, corpo

    # -------------------------------------------------------------- layout
    def _montar(self):
        lateral = tk.Frame(self, bg=COR["lateral"], width=self.px(210))
        lateral.pack(side="left", fill="y")
        lateral.pack_propagate(False)
        tk.Label(lateral, text="✉  Migrador", bg=COR["lateral"], fg="white",
                 font=(FONTE, 15, "bold"), anchor="w").pack(fill="x", padx=self.px(20),
                                                           pady=(self.px(24), 0))
        tk.Label(lateral, text="de e-mails IMAP", bg=COR["lateral"], fg=COR["lateral_texto"],
                 font=(FONTE, 9), anchor="w").pack(fill="x", padx=self.px(48), pady=(0, self.px(24)))
        self.nav = {}
        for chave, texto in (("config", "⚙   Nova migração"), ("progresso", "📈   Andamento"),
                             ("opcoes", "🛠   Opções")):
            b = tk.Label(lateral, text=texto, bg=COR["lateral"], fg=COR["lateral_texto"],
                         font=(FONTE, 10), anchor="w", padx=self.px(20), pady=self.px(10),
                         cursor="hand2")
            b.pack(fill="x")
            b.bind("<Button-1>", lambda e, c=chave: self.mostrar(c))
            b.bind("<Enter>", lambda e, c=chave: self._nav_hover(c, True))
            b.bind("<Leave>", lambda e, c=chave: self._nav_hover(c, False))
            self.nav[chave] = b
        self.estado_lateral = tk.Label(lateral, text="● Pronto", bg=COR["lateral"],
                                       fg=COR["lateral_texto"], font=(FONTE, 9), anchor="w")
        self.estado_lateral.pack(side="bottom", fill="x", padx=self.px(20), pady=self.px(18))

        area = ttk.Frame(self)
        area.pack(side="left", fill="both", expand=True)
        self.paginas = {}
        for chave, construtor in (("config", self._pagina_config),
                                  ("progresso", self._pagina_progresso),
                                  ("opcoes", self._pagina_opcoes)):
            pg = ttk.Frame(area, padding=(self.px(28), self.px(22)))
            pg.place(relx=0, rely=0, relwidth=1, relheight=1)
            construtor(pg)
            self.paginas[chave] = pg

    def _nav_hover(self, chave, dentro):
        if chave != self.pagina_atual:
            self.nav[chave].configure(bg=COR["lateral_hover"] if dentro else COR["lateral"])

    def mostrar(self, chave):
        self.pagina_atual = chave
        self.paginas[chave].tkraise()
        for c, b in self.nav.items():
            ativo = c == chave
            b.configure(bg=COR["lateral_ativo"] if ativo else COR["lateral"],
                        fg="white" if ativo else COR["lateral_texto"],
                        font=(FONTE, 10, "bold" if ativo else "normal"))

    def _cabecalho(self, pg, titulo, subtitulo):
        topo = ttk.Frame(pg)
        topo.pack(fill="x", pady=(0, self.px(16)))
        esq = ttk.Frame(topo)
        esq.pack(side="left")
        ttk.Label(esq, text=titulo, style="Pagina.TLabel").pack(anchor="w")
        ttk.Label(esq, text=subtitulo, style="PaginaSub.TLabel").pack(anchor="w")
        return topo

    # ------------------------------------------------- página: configurar
    def _pagina_config(self, pg):
        self._cabecalho(pg, "Nova migração",
                        "Escolha o tipo, preencha as contas e clique em Iniciar. "
                        "Pode rodar de novo quantas vezes quiser: nada é duplicado.")

        # rodapé fixo com o botão principal (empacotado antes para não sumir)
        rodape = ttk.Frame(pg)
        rodape.pack(side="bottom", fill="x", pady=(self.px(14), 0))
        self.btn_iniciar = ttk.Button(rodape, text="▶  Iniciar migração",
                                      style="Primario.TButton", command=self.iniciar)
        self.btn_iniciar.pack(side="right")
        self.resumo = ttk.Label(rodape, text="", style="Rodape.TLabel",
                                wraplength=self.px(620), justify="left")
        self.resumo.pack(side="left", fill="x")

        ttk.Label(pg, text="1  ·  O que você quer fazer?", style="Passo.TLabel").pack(
            anchor="w", pady=(0, 6))
        modos = ttk.Frame(pg)
        modos.pack(fill="x")
        self.cartoes_modo = {}
        for i, (chave, (titulo, desc, _)) in enumerate(MODOS_GUI.items()):
            modos.columnconfigure(i, weight=1, uniform="modo")
            c = tk.Frame(modos, bg=COR["superficie"], highlightthickness=2,
                         highlightbackground=COR["borda"], cursor="hand2")
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else self.px(10), 0))
            t = tk.Label(c, text=titulo, bg=COR["superficie"], fg=COR["tinta"],
                         font=(FONTE, 11, "bold"), anchor="w")
            t.pack(fill="x", padx=self.px(14), pady=(self.px(12), 0))
            d = tk.Label(c, text=desc, bg=COR["superficie"], fg=COR["tinta2"],
                         font=(FONTE, 9), anchor="w", justify="left", wraplength=self.px(190))
            d.pack(fill="x", padx=self.px(14), pady=(2, self.px(12)))
            for w in (c, t, d):
                w.bind("<Button-1>", lambda e, k=chave: self.v["modo"].set(k))
            self.cartoes_modo[chave] = (c, t, d)

        ttk.Label(pg, text="2  ·  Contas", style="Passo.TLabel").pack(
            anchor="w", pady=(self.px(18), 6))
        self.grade = ttk.Frame(pg)
        self.grade.pack(fill="x")
        self.grade.columnconfigure((0, 1), weight=1, uniform="lado")
        self.card_src = CartaoServidor(self, self.grade, "src", "Origem",
                                       "De onde os e-mails saem")
        self.card_dst = CartaoServidor(self, self.grade, "dst", "Destino",
                                       "Para onde os e-mails vão")
        self.card_dir, corpo = self.cartao(self.grade)
        corpo.columnconfigure(0, weight=1)
        self.dir_titulo = ttk.Label(corpo, text="", style="CartaoTitulo.TLabel")
        self.dir_titulo.grid(row=0, column=0, columnspan=2, sticky="w")
        self.dir_sub = ttk.Label(corpo, text="", style="CartaoMudo.TLabel")
        self.dir_sub.grid(row=1, column=0, columnspan=2, sticky="w")
        self.rotulo(corpo, "Pasta no computador", 2, 0)
        ttk.Entry(corpo, textvariable=self.v["dir"]).grid(row=3, column=0, sticky="ew")
        ttk.Button(corpo, text="Procurar…", style="Secundario.TButton",
                   command=self._escolher_dir).grid(row=3, column=1, padx=(8, 0))
        self.dir_previa = ttk.Label(corpo, text="", style="CartaoMudo.TLabel",
                                    wraplength=self.px(380), justify="left")
        self.dir_previa.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.passo3 = ttk.Label(pg, text="3  ·  Pastas", style="Passo.TLabel")
        self.passo3.pack(anchor="w", pady=(self.px(18), 6))
        self.area_baixo = ttk.Frame(pg)
        self.area_baixo.pack(fill="both", expand=True)
        self._cartao_pastas()
        self._cartao_csv()

    def _cartao_pastas(self):
        self.card_pastas, corpo = self.cartao(self.area_baixo)
        topo = ttk.Frame(corpo, style="Cartao.TFrame")
        topo.pack(fill="x")
        self.btn_pastas = ttk.Button(topo, text="Carregar pastas da origem",
                                     style="Secundario.TButton", command=self.carregar_pastas)
        self.btn_pastas.pack(side="left")
        for texto, valor in (("Nenhuma", False), ("Todas", True)):
            ttk.Button(topo, text=texto, style="Discreto.TButton",
                       command=lambda x=valor: self._marcar_todas(x)).pack(side="right", padx=(6, 0))
        self.pastas_info = ttk.Label(topo, style="CartaoMudo.TLabel",
                                     text="Sem carregar, todas as pastas são migradas.")
        self.pastas_info.pack(side="left", padx=self.px(12))

        caixa = ttk.Frame(corpo, style="Cartao.TFrame")
        caixa.pack(fill="both", expand=True, pady=(self.px(10), 0))
        self.arvore = ttk.Treeview(caixa, columns=("sel", "pasta", "msgs"), show="headings",
                                   selectmode="none", height=4)
        for col, texto, larg, anc in (("sel", "", 40, "center"), ("pasta", "Pasta", 400, "w"),
                                      ("msgs", "Mensagens", 120, "e")):
            self.arvore.heading(col, text=texto, anchor=anc)
            self.arvore.column(col, width=self.px(larg), anchor=anc, stretch=col == "pasta")
        barra = ttk.Scrollbar(caixa, orient="vertical", command=self.arvore.yview)
        self.arvore.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.arvore.pack(fill="both", expand=True)
        self.arvore.bind("<Button-1>", self._clique_pasta)

    def _cartao_csv(self):
        self.card_csv, corpo = self.cartao(self.area_baixo)
        topo = ttk.Frame(corpo, style="Cartao.TFrame")
        topo.pack(fill="x")
        ttk.Label(topo, text="Arquivo CSV", style="Campo.TLabel").pack(side="left")
        ttk.Button(topo, text="Procurar…", style="Secundario.TButton",
                   command=self._escolher_csv).pack(side="right")
        ttk.Entry(topo, textvariable=self.v["csv"]).pack(side="right", fill="x", expand=True,
                                                          padx=(self.px(10), 8))
        self.csv_info = ttk.Label(corpo, style="CartaoMudo.TLabel", text=(
            "Colunas esperadas: src_user, src_pass, dst_user, dst_pass "
            "(uma linha por conta)."))
        self.csv_info.pack(anchor="w", pady=(8, 0))
        caixa = ttk.Frame(corpo, style="Cartao.TFrame")
        caixa.pack(fill="both", expand=True, pady=(self.px(8), 0))
        self.arvore_csv = ttk.Treeview(caixa, columns=("n", "src", "dst"), show="headings",
                                       selectmode="none", height=4)
        for col, texto, larg in (("n", "#", 50), ("src", "Conta de origem", 300),
                                 ("dst", "Conta de destino", 300)):
            self.arvore_csv.heading(col, text=texto, anchor="w")
            self.arvore_csv.column(col, width=self.px(larg), anchor="w", stretch=col != "n")
        barra = ttk.Scrollbar(caixa, orient="vertical", command=self.arvore_csv.yview)
        self.arvore_csv.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.arvore_csv.pack(fill="both", expand=True)

    def _aplicar_modo(self):
        modo = self.v["modo"].get()
        if modo not in MODOS_GUI:
            self.v["modo"].set("imap2imap")
            return
        for chave, (c, t, d) in self.cartoes_modo.items():
            sel = chave == modo
            fundo = COR["selecionado"] if sel else COR["superficie"]
            c.configure(bg=fundo, highlightbackground=COR["destaque"] if sel else COR["borda"])
            t.configure(bg=fundo, fg=COR["destaque_esc"] if sel else COR["tinta"])
            d.configure(bg=fundo)
        for w in (self.card_src, self.card_dst, self.card_dir):
            w.grid_remove()
        esq, dir_ = {"imap2imap": (self.card_src, self.card_dst),
                     "lote": (self.card_src, self.card_dst),
                     "export": (self.card_src, self.card_dir),
                     "import": (self.card_dir, self.card_dst)}[modo]
        esq.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(8)))
        dir_.grid(row=0, column=1, sticky="nsew", padx=(self.px(8), 0))
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
            self.passo3.pack(anchor="w", pady=(self.px(18), 6), before=self.area_baixo)
        elif modo == "lote":
            self.passo3.configure(text="3  ·  Lista de contas")
            self.card_csv.pack(fill="both", expand=True)
            self.passo3.pack(anchor="w", pady=(self.px(18), 6), before=self.area_baixo)
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
            self.arvore.delete(*self.arvore.get_children())
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
        self.arvore.delete(*self.arvore.get_children())
        for nome, delim, n in lista:
            rotulo = " › ".join(decodificar_utf7(p) for p in nome.split(delim or "/"))
            self.arvore.insert("", "end", iid=nome, values=("☑", rotulo, num(n)))
        self._atualizar_contagem_pastas()

    def _clique_pasta(self, e):
        iid = self.arvore.identify_row(e.y)
        if iid in self.pastas_origem:
            self.pastas_origem[iid][0] = not self.pastas_origem[iid][0]
            self.arvore.set(iid, "sel", "☑" if self.pastas_origem[iid][0] else "☐")
            self._atualizar_contagem_pastas()

    def _marcar_todas(self, valor):
        for iid, item in self.pastas_origem.items():
            item[0] = valor
            self.arvore.set(iid, "sel", "☑" if valor else "☐")
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
        self.arvore_csv.delete(*self.arvore_csv.get_children())
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
            self.csv_info.configure(text=f"Não foi possível ler o CSV: {e}", style="CartaoErro.TLabel")
            return
        for n, row in enumerate(contas, 1):
            self.arvore_csv.insert("", "end", values=(n, row["src_user"], row["dst_user"]))
        self.csv_info.configure(text=f"{len(contas)} contas encontradas. As senhas não são exibidas.",
                                style="CartaoMudo.TLabel")

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
        self._cabecalho(pg, "Opções", "Valem para todas as migrações. "
                                      "As senhas nunca são gravadas em disco.")
        col = ttk.Frame(pg)
        col.pack(fill="x")
        col.columnconfigure((0, 1), weight=1, uniform="op")

        c1, corpo = self.cartao(col)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(8)))
        corpo.columnconfigure(0, weight=1)
        ttk.Label(corpo, text="Conexão e registro", style="CartaoTitulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.rotulo(corpo, "Tempo limite sem resposta do servidor (segundos)", 1, 0, 2)
        ttk.Entry(corpo, textvariable=self.v["timeout"], width=8).grid(row=2, column=0, sticky="w")
        self.rotulo(corpo, "Arquivo de log", 3, 0, 2)
        ttk.Entry(corpo, textvariable=self.v["log"]).grid(row=4, column=0, sticky="ew")
        ttk.Button(corpo, text="Procurar…", style="Secundario.TButton",
                   command=self._escolher_log).grid(row=4, column=1, padx=(8, 0))
        ttk.Button(corpo, text="Abrir log", style="Discreto.TButton",
                   command=self.abrir_log).grid(row=5, column=0, sticky="w", pady=(12, 0))

        c2, corpo = self.cartao(col)
        c2.grid(row=0, column=1, sticky="nsew", padx=(self.px(8), 0))
        corpo.columnconfigure((0, 1), weight=1)
        ttk.Label(corpo, text="Avisar quando terminar", style="CartaoTitulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(corpo, text="Opcional. Webhook do Slack/Discord/Mattermost e/ou e-mail.",
                  style="CartaoMudo.TLabel").grid(row=1, column=0, columnspan=2, sticky="w")
        campos = [("Webhook (URL)", "webhook", 2), ("Enviar resultado para (e-mail)", "notify_email", 4)]
        for rotulo, chave, linha in campos:
            self.rotulo(corpo, rotulo, linha, 0, 2)
            ttk.Entry(corpo, textvariable=self.v[chave]).grid(row=linha + 1, column=0,
                                                              columnspan=2, sticky="ew")
        pares = [(("Servidor SMTP", "smtp_host"), ("Porta SMTP", "smtp_port")),
                 (("Usuário SMTP", "smtp_user"), ("Senha SMTP", "smtp_pass")),
                 (("Remetente (opcional)", "smtp_from"), None)]
        for i, par in enumerate(pares):
            linha = 6 + i * 2
            for j, item in enumerate(par):
                if not item:
                    continue
                self.rotulo(corpo, item[0], linha, j)
                ttk.Entry(corpo, textvariable=self.v[item[1]],
                          show="•" if item[1] == "smtp_pass" else "").grid(
                    row=linha + 1, column=j, sticky="ew", padx=(0, 8) if j == 0 else 0)

        rodape = ttk.Frame(pg)
        rodape.pack(fill="x", pady=(self.px(16), 0))
        ttk.Button(rodape, text="Salvar como padrão", style="Secundario.TButton",
                   command=lambda: (self._salvar_perfil(), messagebox.showinfo(
                       "Salvo", "Configurações salvas (sem as senhas).", parent=self))).pack(side="left")
        ttk.Label(rodape, text=f"Salvo em {PERFIL}", style="PaginaSub.TLabel").pack(
            side="left", padx=self.px(12))

    def _escolher_log(self):
        f = filedialog.asksaveasfilename(parent=self, title="Arquivo de log",
                                         defaultextension=".log", initialfile="migracao.log",
                                         filetypes=[("Log", "*.log"), ("Todos", "*.*")])
        if f:
            self.v["log"].set(os.path.normpath(f))

    def abrir_log(self):
        caminho = self.v["log"].get().strip() or LOG_PADRAO
        if os.path.exists(caminho):
            os.startfile(caminho)
        else:
            messagebox.showinfo("Log", "O log ainda não existe: ele é criado na primeira execução.",
                                parent=self)

    # --------------------------------------------------- página: andamento
    def _pagina_progresso(self, pg):
        topo = self._cabecalho(pg, "Andamento", "Acompanhe a migração em tempo real.")
        acoes = ttk.Frame(topo)
        acoes.pack(side="right")
        self.pilula = tk.Label(acoes, font=(FONTE, 10, "bold"), padx=self.px(12), pady=self.px(5))
        self.pilula.pack(side="left", padx=(0, self.px(12)))
        self.btn_parar = ttk.Button(acoes, text="■  Parar", style="Perigo.TButton",
                                    command=self.parar_execucao)
        self.btn_parar.pack(side="left")
        self.btn_abrir_dir = ttk.Button(acoes, text="Abrir pasta", style="Secundario.TButton",
                                        command=lambda: os.startfile(self.v["dir"].get()))
        ttk.Button(acoes, text="Abrir log", style="Secundario.TButton",
                   command=self.abrir_log).pack(side="left", padx=(8, 0))

        kpis = ttk.Frame(pg)
        kpis.pack(fill="x")
        self.kpi = {}
        for i, (chave, rotulo, marca) in enumerate((
                ("copiadas", "Copiadas", SERIES["copiadas"]),
                ("puladas", "Já existiam", SERIES["puladas"]),
                ("erros", "Erros", SERIES["erros"]),
                ("bytes", "Transferido", None), ("vel", "Velocidade média", None),
                ("tempo", "Tempo decorrido", None))):
            kpis.columnconfigure(i, weight=1, uniform="kpi")
            k = Kpi(kpis, self, rotulo, marca)
            k.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else self.px(10), 0))
            self.kpi[chave] = k

        atual, corpo = self.cartao(pg, fill="x", pady=(self.px(14), 0))
        linha = ttk.Frame(corpo, style="Cartao.TFrame")
        linha.pack(fill="x")
        self.lbl_pasta = ttk.Label(linha, text="Nenhuma migração em andamento",
                                   style="CartaoTitulo.TLabel")
        self.lbl_pasta.pack(side="left")
        self.lbl_conta = ttk.Label(linha, text="", style="CartaoMudo.TLabel")
        self.lbl_conta.pack(side="right")
        self.lbl_pasta_info = ttk.Label(corpo, text="", style="CartaoMudo.TLabel")
        self.lbl_pasta_info.pack(anchor="w", pady=(2, 8))
        self.barra = ttk.Progressbar(corpo, style="Barra.Horizontal.TProgressbar", maximum=1)
        self.barra.pack(fill="x")
        self.lbl_prog = ttk.Label(corpo, text="", style="Cartao.TLabel")
        self.lbl_prog.pack(anchor="w", pady=(6, 0))

        graficos = ttk.Frame(pg)
        graficos.pack(fill="x", pady=(self.px(14), 0))
        graficos.columnconfigure((0, 1), weight=1, uniform="graf")
        for i, (titulo, classe) in enumerate((("Velocidade de transferência", GraficoVelocidade),
                                              ("Resultado por pasta", GraficoPastas))):
            c, corpo = self.cartao(graficos)
            c.grid(row=0, column=i, sticky="nsew",
                   padx=(0, self.px(7)) if i == 0 else (self.px(7), 0))
            ttk.Label(corpo, text=titulo, style="CartaoTitulo.TLabel").pack(anchor="w", pady=(0, 6))
            g = classe(corpo, self.px)
            g.pack(fill="both", expand=True)
            if i == 0:
                self.graf_vel = g
            else:
                self.graf_pastas = g

        caixa_log, corpo = self.cartao(pg, fill="both", expand=True, pady=(self.px(14), 0))
        ttk.Label(corpo, text="Registro detalhado", style="CartaoTitulo.TLabel").pack(anchor="w")
        caixa = ttk.Frame(corpo, style="Cartao.TFrame")
        caixa.pack(fill="both", expand=True, pady=(6, 0))
        self.txt_log = tk.Text(caixa, height=6, wrap="none", bd=0, padx=8, pady=6,
                               bg=COR["superficie"], fg=COR["tinta"], font=("Consolas", 9),
                               highlightthickness=0, state="disabled")
        barra = ttk.Scrollbar(caixa, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True)
        for tag, cor, peso in (("hora", COR["muda"], "normal"), ("info", COR["tinta2"], "normal"),
                               ("ok", COR["ok_texto"], "normal"),
                               ("aviso", COR["aviso_texto"], "normal"),
                               ("erro", COR["erro_texto"], "bold"),
                               ("titulo", COR["destaque_esc"], "bold"),
                               ("secao", COR["tinta"], "bold"), ("texto", COR["tinta"], "normal")):
            self.txt_log.tag_configure(tag, foreground=cor, font=("Consolas", 9, peso))
        self._resetar_painel()
        self._estado("parado")

    def _estado(self, estado):
        texto, fundo, tinta = PILULAS[estado]
        self.pilula.configure(text=texto, bg=fundo, fg=tinta)
        self.estado_lateral.configure(text="● Migrando…" if estado == "rodando" else
                                      "● Pronto", fg="white" if estado == "rodando"
                                      else COR["lateral_texto"])
        self.btn_parar.state(["!disabled"] if estado == "rodando" else ["disabled"])

    def _resetar_painel(self):
        self.tot = {"copiadas": 0, "puladas": 0, "erros": 0, "bytes": 0}
        self.atual = {"feito": 0, "total": 0, "bytes": 0, "inicio": None}
        self.n_pastas = 0
        self.amostras = []  # (tempo, bytes acumulados) para a média móvel
        self.inicio = None
        self.graf_vel.limpar()
        self.graf_pastas.limpar()
        self.barra.stop()
        self.barra.configure(mode="determinate", value=0)
        self.lbl_prog.configure(text="")
        self.lbl_pasta_info.configure(text="")
        self.lbl_conta.configure(text="")
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")
        self._atualizar_kpis()

    def _bytes_total(self):
        return self.tot["bytes"] + self.atual["bytes"]

    def _atualizar_kpis(self):
        self.kpi["copiadas"].set(num(self.tot["copiadas"] + self.atual["feito"]))
        self.kpi["puladas"].set(num(self.tot["puladas"]))
        self.kpi["erros"].set(num(self.tot["erros"]))
        self.kpi["bytes"].set(me.fmt_bytes(self._bytes_total()))
        decorrido = time.time() - self.inicio if self.inicio else 0
        self.kpi["vel"].set(f"{me.fmt_bytes(self._bytes_total() / decorrido)}/s"
                            if decorrido > 1 else "—")
        self.kpi["tempo"].set(me.fmt_tempo(decorrido) if self.inicio else "—")

    def _escrever_log(self, nivel, texto):
        t = self.txt_log
        t.configure(state="normal")
        texto = decodificar_utf7(texto)
        if nivel == "titulo":
            t.insert("end", f"\n{texto}\n", "titulo")
        else:
            simbolo = GuiUi.SIMBOLOS.get(nivel)
            if nivel == "secao":
                t.insert("end", "\n")
            t.insert("end", time.strftime("%H:%M:%S "), "hora")
            t.insert("end", f"{'▸ ' if nivel == 'secao' else ''}"
                            f"{simbolo + ' ' if simbolo else ''}{texto}\n", nivel)
        if int(t.index("end-1c").split(".")[0]) > 5000:
            t.delete("1.0", "1000.0")
        t.see("end")
        t.configure(state="disabled")

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
            if not self.arvore_csv.get_children():
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
        self.rodando = True
        self._resetar_painel()
        self.inicio = time.time()
        self.total_pastas = (len(a.pasta) if a.pasta else len(self.pastas_origem)) \
            if self.v["modo"].get() in ("imap2imap", "export") else 0
        self._estado("rodando")
        self.btn_iniciar.state(["disabled"])
        if a.modo in ("export", "import"):
            self.btn_abrir_dir.pack(side="left", padx=(8, 0), after=self.btn_parar)
        else:
            self.btn_abrir_dir.pack_forget()
        self.lbl_pasta.configure(text="Conectando…")
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
            self.btn_parar.state(["disabled"])

    def _relogio(self):
        if not self.rodando:
            return
        agora = time.time()
        self.amostras.append((agora, self._bytes_total()))
        self.amostras = [s for s in self.amostras if agora - s[0] <= 5.5]
        t0, b0 = self.amostras[0]
        if agora - t0 >= 1:
            self.graf_vel.adicionar(agora - self.inicio, (self._bytes_total() - b0) / (agora - t0))
        self._atualizar_kpis()
        self.after(1000, self._relogio)

    # ------------------------------------------------------ fila de eventos
    def _drenar(self):
        progresso = None
        try:
            for _ in range(2000):
                ev = self.fila.get_nowait()
                if ev[0] == "progresso":
                    progresso = ev  # só o último importa
                    continue
                if progresso:
                    self._tratar(progresso)
                    progresso = None
                self._tratar(ev)
        except queue.Empty:
            pass
        if progresso:
            self._tratar(progresso)
        self.after(100, self._drenar)

    def _tratar(self, ev):
        tipo = ev[0]
        if tipo == "log":
            self._escrever_log(ev[1], ev[2])
            if ev[1] == "info" and self.rodando:
                self.lbl_pasta_info.configure(text=ev[2])
        elif tipo == "pasta":
            self.n_pastas += 1
            self.atual = {"feito": 0, "total": 0, "bytes": 0, "inicio": None}
            de = f" de {self.total_pastas}" if self.total_pastas else ""
            self.lbl_pasta.configure(text=decodificar_utf7(ev[1]))
            self.lbl_pasta_info.configure(text=f"Pasta {self.n_pastas}{de} · comparando com o destino…")
            self.lbl_prog.configure(text="")
            self.barra.configure(mode="indeterminate")
            self.barra.start(15)
        elif tipo == "progresso":
            _, feito, total, bytes_ = ev
            if self.atual["inicio"] is None:
                self.atual["inicio"] = time.time()
                self.barra.stop()
                self.barra.configure(mode="determinate")
            self.atual.update(feito=feito, total=total, bytes=bytes_)
            self.barra.configure(value=feito / total if total else 1)
            dec = time.time() - self.atual["inicio"]
            eta = dec / feito * (total - feito) if feito else 0
            self.lbl_prog.configure(text=f"{num(feito)} de {num(total)} · {feito / (total or 1):.0%}"
                                         f" · faltam ~{me.fmt_tempo(eta)}")
        elif tipo == "pasta_ok":
            linha = ev[1]
            self.tot["copiadas"] += linha[2]
            self.tot["puladas"] += linha[3]
            self.tot["erros"] += linha[4]
            self.tot["bytes"] += self.atual["bytes"]
            self.atual = {"feito": 0, "total": 0, "bytes": 0, "inicio": 0}
            self.barra.stop()
            self.barra.configure(mode="determinate", value=1)
            self.graf_pastas.adicionar(linha)
            self._atualizar_kpis()
        elif tipo == "conta":
            _, n, total, src, dst = ev
            self.lbl_conta.configure(text=f"Conta {n} de {total}: {src} → {dst}")
            self.n_pastas = 0
            self.graf_pastas.limpar()
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

    def _fim(self, estado, resumo):
        self.rodando = False
        self._atualizar_kpis()  # se parou no meio, a pasta em andamento continua contada
        feito, total = self.atual["feito"], self.atual["total"]
        self.barra.stop()
        self.barra.configure(mode="determinate",
                             value=1 if estado == "ok" else feito / total if total else 0)
        self._estado(estado)
        self.btn_iniciar.state(["!disabled"])
        self.lbl_pasta.configure(text={"ok": "Migração concluída",
                                       "cancelado": "Migração interrompida"}.get(
                                           estado, "Migração terminou com problemas"))
        self.lbl_pasta_info.configure(text=resumo.splitlines()[0])
        self.lbl_prog.configure(text="")
        self.bell()
        mostrar = messagebox.showinfo if estado == "ok" else messagebox.showwarning
        mostrar("Migração", f"{PILULAS[estado][0].lstrip('✔✖⚠ ')}\n\n{resumo}\n\n"
                            f"Tempo: {me.fmt_tempo(time.time() - self.inicio)}", parent=self)

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

    def _fechar(self):
        if self.rodando and not messagebox.askyesno(
                "Sair", "Uma migração está em andamento. Parar e sair?\n"
                        "Ao abrir de novo, ela continua de onde parou.", parent=self):
            return
        self.parar.set()
        self._salvar_perfil()
        self.destroy()


def main():
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
