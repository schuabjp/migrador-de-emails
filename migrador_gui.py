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
    "fundo": "#f4f4f1", "superficie": "#fcfcfb", "borda": "#e1e0d9", "zebra": "#f5f5f2",
    "tinta": "#0b0b0b", "tinta2": "#52514e", "muda": "#898781",
    "grade": "#e1e0d9", "eixo": "#c3c2b7",
    "destaque": "#2a78d6", "destaque_esc": "#1c5cab", "destaque_claro": "#e3eefc",
    "area": "#cde2fb", "selecionado": "#eef4fc", "hover": "#f7f9fd",
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
BANNER = {  # estado -> (ícone, fundo, borda, tinta)
    "ok": ("✔", "#eaf6ea", "#bfe3bf", COR["ok_texto"]),
    "falha": ("✖", "#fbe9e9", "#efc2c2", COR["erro_texto"]),
    "cancelado": ("⚠", "#fdf3dc", "#f1d9a0", COR["aviso_texto"]),
}
SITUACAO_PASTA = {  # status -> (texto, tag da linha)
    "pendente": ("○ Pendente", "muda"), "andamento": ("● Copiando", "ativa"),
    "ok": ("✔ Concluída", ""), "erros": ("⚠ Com erros", "erro"),
    "parada": ("■ Interrompida", "aviso"),
}
SITUACAO_CONTA = {
    "pendente": ("○ Pendente", "muda"), "andamento": ("● Migrando", "ativa"),
    "OK": ("✔ OK", ""), "COM ERROS": ("⚠ Com erros", "erro"), "FALHA": ("✖ Falhou", "erro"),
    "parada": ("■ Interrompida", "aviso"),
}
RESULTADO = {"ok": "✔ Concluída", "falha": "✖ Com problemas", "cancelado": "⚠ Interrompida",
             "rodando": "⚠ Interrompida"}
COLS_PASTAS = [("situacao", "Situação", 120, "w", False), ("conta", "Conta", 180, "w", False),
               ("pasta", "Pasta", 240, "w", True), ("msgs", "Mensagens", 90, "e", False),
               ("cop", "Copiadas", 90, "e", False), ("pul", "Já existiam", 95, "e", False),
               ("err", "Erros", 60, "e", False), ("tam", "Tamanho", 85, "e", False),
               ("tempo", "Tempo", 75, "e", False)]
COLS_PROBLEMAS = [("hora", "Hora", 75, "w", False), ("tipo", "Tipo", 80, "w", False),
                  ("conta", "Conta", 180, "w", False), ("pasta", "Pasta", 180, "w", False),
                  ("msg", "Mensagem", 420, "w", True)]
COLS_CONTAS = [("n", "#", 40, "w", False), ("src", "Origem", 200, "w", True),
               ("dst", "Destino", 200, "w", True), ("situacao", "Situação", 120, "w", False),
               ("cop", "Copiadas", 85, "e", False), ("pul", "Já existiam", 95, "e", False),
               ("err", "Erros", 60, "e", False), ("det", "Detalhe", 260, "w", True)]
COLS_HIST = [("data", "Data", 130, "w", False), ("tipo", "Tipo", 150, "w", False),
             ("rota", "De → Para", 380, "w", True), ("res", "Resultado", 130, "w", False),
             ("cop", "Copiadas", 85, "e", False), ("pul", "Já existiam", 95, "e", False),
             ("err", "Erros", 60, "e", False), ("dur", "Duração", 80, "e", False)]
FONTE = "Segoe UI"


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
    texto, tag = SITUACAO_PASTA[p["status"]]
    return ((texto, p["conta"], decodificar_utf7(p["pasta"]), num(p["mensagens"]),
             num(p["copiadas"]), num(p["puladas"]), num(p["erros"]),
             me.fmt_bytes(p["bytes"]) if p["bytes"] else "—",
             me.fmt_tempo(p["segundos"]) if p["segundos"] >= 1 else
             "<1s" if p["status"] != "pendente" else "—"), (tag,) if tag else ())


def linha_conta(c):
    texto, tag = SITUACAO_CONTA[c["status"]]
    return ((c["n"], c["src"], c["dst"], texto, num(c["copiadas"]), num(c["puladas"]),
             num(c["erros"]), c["detalhe"]), (tag,) if tag else ())


def linha_problema(pr):
    return ((pr["hora"], "✖ Erro" if pr["nivel"] == "erro" else "⚠ Aviso", pr["conta"],
             pr["pasta"], pr["texto"]), (pr["nivel"],))


def exportar_csv(rel, caminho):
    """Relatório completo em CSV (separador ';' abre direto no Excel em português)."""
    t = rel["totais"]
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        for rotulo, valor in (("Início", data_hora(rel["inicio"])),
                              ("Duração", me.fmt_tempo(rel.get("duracao", 0))),
                              ("Tipo", rel["tipo"]), ("Origem", rel["origem"]),
                              ("Destino", rel["destino"]),
                              ("Resultado", RESULTADO.get(rel["estado"], rel["estado"])),
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


class Grafico(CanvasFluido):
    """Base dos gráficos: título à esquerda e um texto de contexto à direita."""

    def __init__(self, pai, px, titulo):
        super().__init__(pai, px, 182)
        self.titulo, self.extra = titulo, ""
        self.cab = px(30)

    def _cabecalho(self, y0=0):
        w = self.winfo_width()
        self.create_rectangle(0, y0, w, y0 + self.cab, fill=COR["superficie"], outline="",
                              tags="cab")
        self.create_text(0, y0 + self.cab / 2 - 2, anchor="w", text=self.titulo,
                         fill=COR["tinta"], font=(FONTE, 12, "bold"), tags="cab")
        self.create_text(w, y0 + self.cab / 2 - 2, anchor="e", text=self.extra,
                         fill=COR["muda"], font=(FONTE, 9), tags=("cab", "extra"))

    def definir_extra(self, texto):
        if texto != self.extra:
            self.extra = texto
            self.itemconfigure("extra", text=texto)


class GraficoVelocidade(Grafico):
    """Velocidade de transferência (média móvel de 5 s) desde o início."""
    LIMITE = 600

    def __init__(self, pai, px):
        super().__init__(pai, px, "Velocidade de transferência")
        self.pontos = []  # (segundos desde o início, bytes/s)
        self.xs = []
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
        self._cabecalho()
        w, h = self.winfo_width(), self.winfo_height()
        self.esq, dir_, self.topo, self.base = px(70), px(14), self.cab + px(10), h - px(22)
        if len(p) < 2:
            self.create_line(self.esq, self.base, w - dir_, self.base, fill=COR["eixo"])
            self.create_text(w / 2, (h + self.cab) / 2,
                             text="O gráfico aparece quando a transferência começar",
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


class GraficoPastas(Grafico):
    """Barras horizontais empilhadas: copiadas / já existiam / erros por pasta."""

    def __init__(self, pai, px):
        super().__init__(pai, px, "Resultado por pasta")
        self.linhas = []  # [nome, total, copiadas, puladas, erros]
        self.fonte = tkfont.Font(family=FONTE, size=9)
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
        self._fixar_cabecalho()

    def _rolar(self, e):
        if self.bbox("barras") and self.bbox("barras")[3] > self.winfo_height():
            self.yview_scroll(-1 if e.delta > 0 else 1, "units")
            self._fixar_cabecalho()

    def _fixar_cabecalho(self):
        """Título e legenda ficam parados no topo enquanto as barras rolam."""
        topo = self.canvasy(0)
        self.move("fixo", 0, topo - self._fixo_y)
        self._fixo_y = topo
        self.tag_raise("fixo")

    def _cortar(self, texto, largura):
        if self.fonte.measure(texto) <= largura:
            return texto
        while texto and self.fonte.measure(texto + "…") > largura:
            texto = texto[:-1]
        return texto + "…"

    def desenhar(self):
        px = self.px
        self.delete("all")
        self.yview_moveto(0)
        self._fixo_y = 0
        w, h = self.winfo_width(), self.winfo_height()
        self.altura = px(24)
        legenda = self.cab + px(2)
        self.topo = legenda + px(26)
        self.create_rectangle(0, 0, w, self.topo - px(4), fill=COR["superficie"], outline="",
                              tags="fixo")
        self._cabecalho()
        self.addtag_withtag("fixo", "cab")
        x = 0
        for chave, rotulo in (("copiadas", "Copiadas"), ("puladas", "Já existiam"),
                              ("erros", "Erros")):
            self.create_rectangle(x, legenda + px(5), x + px(10), legenda + px(15),
                                  fill=SERIES[chave], outline="", tags="fixo")
            t = self.create_text(x + px(15), legenda + px(10), anchor="w", text=rotulo,
                                 fill=COR["tinta2"], font=(FONTE, 9), tags="fixo")
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
        self.tag_raise("fixo")

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
class Tabela(ttk.Frame):
    """Treeview com rolagem, números à direita e linhas zebradas."""

    def __init__(self, pai, px, colunas, altura=5, selecao=True):
        super().__init__(pai, style="Cartao.TFrame")
        self.tv = ttk.Treeview(self, columns=[c[0] for c in colunas], show="headings",
                               height=altura, selectmode="browse" if selecao else "none")
        for cid, titulo, larg, anc, estica in colunas:
            self.tv.heading(cid, text=titulo, anchor=anc)
            self.tv.column(cid, width=px(larg), minwidth=px(40), anchor=anc, stretch=estica)
        barra = ttk.Scrollbar(self, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("par", background=COR["zebra"])
        for tag, cor in (("muda", COR["muda"]), ("ativa", COR["destaque_esc"]),
                         ("erro", COR["erro_texto"]), ("aviso", COR["aviso_texto"])):
            self.tv.tag_configure(tag, foreground=cor)
        self.n = 0

    def limpar(self):
        self.tv.delete(*self.tv.get_children())
        self.n = 0

    def no_fim(self):
        return self.tv.yview()[1] >= 0.999

    def inserir(self, valores, tags=(), iid=None, rolar=False):
        """rolar=True acompanha o fim só se o usuário já estava no fim (não tira do lugar)."""
        acompanhar = rolar and self.no_fim()
        iid = self.tv.insert("", "end", iid=iid, values=valores,
                             tags=(("par",) if self.n % 2 else ()) + tuple(tags))
        self.n += 1
        if acompanhar:
            self.tv.see(iid)
        return iid

    def atualizar(self, iid, valores, tags=()):
        zebra = ("par",) if "par" in self.tv.item(iid, "tags") else ()
        self.tv.item(iid, values=valores, tags=zebra + tuple(tags))

    def colunas_visiveis(self, ids):
        self.tv.configure(displaycolumns=ids)


class Abas(tk.Frame):
    """Abas com o cabeçalho desenhado num canvas; só a aba visível ocupa a tela."""

    def __init__(self, pai, px):
        super().__init__(pai, bg=COR["superficie"])
        self.fonte = tkfont.Font(family=FONTE, size=10)
        self.cab = tk.Canvas(self, height=px(36), bg=COR["superficie"], highlightthickness=0)
        self.cab.pack(fill="x")
        self.conteudo = tk.Frame(self, bg=COR["superficie"])
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
        c = self.cab
        c.delete("all")
        h = c.winfo_height()
        c.create_line(0, h - 1, c.winfo_width(), h - 1, fill=COR["borda"])
        x, self.zonas = 0, []
        for chave, texto in self.textos.items():
            if chave in self.ocultas:
                continue
            w = self.fonte.measure(texto) + 28
            sel = chave == self.atual
            cor = (COR["destaque_esc"] if sel else
                   COR["tinta"] if chave == self.hover else COR["tinta2"])
            c.create_text(x + w / 2, h / 2 - 1, text=texto, fill=cor, font=self.fonte)
            if sel:
                c.create_rectangle(x + 6, h - 3, x + w - 6, h, fill=COR["destaque"], outline="")
            self.zonas.append((x, x + w, chave))
            x += w + 4

    def adicionar(self, chave, titulo):
        self.textos[chave] = titulo
        self.quadros[chave] = ttk.Frame(self.conteudo, style="Cartao.TFrame")
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


class CartaoServidor(tk.Frame):
    """Servidor IMAP + usuário + senha + botão de teste."""

    def __init__(self, app, pai, lado, titulo, subtitulo):
        super().__init__(pai, bg=COR["superficie"], highlightthickness=1,
                         highlightbackground=COR["borda"], padx=app.px(16), pady=app.px(16))
        self.app, self.lado = app, lado
        v = app.v
        corpo = self
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


class PainelKpi(CanvasFluido):
    """Fileira de indicadores (número grande + rótulo + contexto) num só canvas."""

    def __init__(self, pai, px, itens):  # itens: [(chave, rótulo, cor da marca ou None)]
        super().__init__(pai, px, 92, fundo=COR["fundo"])
        self.itens = itens
        self.valores = {chave: ("—", "") for chave, _, _ in itens}

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        vao = px(10)
        larg = (w - vao * (len(self.itens) - 1)) / len(self.itens)
        for i, (chave, rotulo, marca) in enumerate(self.itens):
            x0 = i * (larg + vao)
            self.create_rectangle(x0, 0, x0 + larg - 1, h - 1, fill=COR["superficie"],
                                  outline=COR["borda"])
            x = x0 + px(14)
            if marca:
                self.create_rectangle(x, px(11), x + px(9), px(20), fill=marca, outline="")
            self.create_text(x + (px(15) if marca else 0), px(16), anchor="w", text=rotulo,
                             fill=COR["tinta2"], font=(FONTE, 9))
            valor, sub = self.valores[chave]
            self.create_text(x, px(44), anchor="w", text=valor, fill=COR["tinta"],
                             font=(FONTE, 20, "bold"), tags=f"v_{chave}")
            self.create_text(x, px(73), anchor="w", text=sub, fill=COR["muda"],
                             font=(FONTE, 9), tags=f"s_{chave}")

    def set(self, chave, valor, sub=""):
        if self.valores[chave] == (valor, sub):  # sem redesenho à toa
            return
        self.valores[chave] = (valor, sub)
        self.itemconfigure(f"v_{chave}", text=valor)
        self.itemconfigure(f"s_{chave}", text=sub)


class CartoesModo(CanvasFluido):
    """Os quatro tipos de migração como cartões clicáveis, num só canvas."""

    def __init__(self, pai, px, ao_escolher):
        super().__init__(pai, px, 78, fundo=COR["fundo"])
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
        vao = px(10)
        larg = (w - vao * (len(MODOS_GUI) - 1)) / len(MODOS_GUI)
        self.zonas = []
        for i, (chave, (titulo, desc, _)) in enumerate(MODOS_GUI.items()):
            x0 = i * (larg + vao)
            sel, hov = chave == self.sel, chave == self.hover
            fundo = COR["selecionado"] if sel else COR["hover"] if hov else COR["superficie"]
            borda = COR["destaque"] if sel else COR["eixo"] if hov else COR["borda"]
            self.create_rectangle(x0 + 1, 1, x0 + larg - 1, h - 1, fill=fundo, outline=borda,
                                  width=2)
            self.create_text(x0 + px(15), px(13), anchor="nw", text=titulo,
                             fill=COR["destaque_esc"] if sel else COR["tinta"],
                             font=(FONTE, 11, "bold"))
            self.create_text(x0 + px(15), px(37), anchor="nw", text=desc, fill=COR["tinta2"],
                             font=(FONTE, 9), width=larg - px(30))
            self.zonas.append((x0, x0 + larg, chave))


class BarraLateral(tk.Canvas):
    """Navegação + andamento resumido, sempre visíveis, num só canvas."""
    ITENS = (("config", "⚙", "Nova migração"), ("progresso", "📈", "Andamento"),
             ("historico", "🕘", "Histórico"), ("opcoes", "🛠", "Opções"))

    def __init__(self, pai, px, ao_escolher):
        super().__init__(pai, bg=COR["lateral"], highlightthickness=0, width=px(210))
        self.px, self.ao_escolher = px, ao_escolher
        self.ativo = self.hover = None
        self.situacao = ("● Pronto", "", False)
        self.zonas = []
        self.bind("<Configure>", lambda e: self.desenhar())
        self.bind("<Motion>", self._mover)
        self.bind("<Leave>", lambda e: self._mover(None))
        self.bind("<Button-1>", self._clique)

    def _em(self, y):
        return next((k for a, b, k in self.zonas if a <= y <= b), None)

    def _clique(self, e):
        chave = self._em(e.y)
        if chave:
            self.ao_escolher(chave)

    def _mover(self, e):
        chave = self._em(e.y) if e else None
        self.configure(cursor="hand2" if chave else "")
        if chave != self.hover:
            self.hover = chave
            self.desenhar()

    def ativar(self, chave):
        self.ativo = chave
        self.desenhar()

    def desenhar(self):
        px = self.px
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        self.create_text(px(20), px(38), anchor="w", text="✉  Migrador", fill="white",
                         font=(FONTE, 15, "bold"))
        self.create_text(px(49), px(61), anchor="w", text="de e-mails IMAP",
                         fill=COR["lateral_texto"], font=(FONTE, 9))
        y, alt, self.zonas = px(96), px(42), []
        for chave, icone, texto in self.ITENS:
            sel, hov = chave == self.ativo, chave == self.hover
            if sel or hov:
                self.create_rectangle(0, y, w, y + alt, outline="",
                                      fill=COR["lateral_ativo"] if sel else COR["lateral_hover"])
            if sel:
                self.create_rectangle(0, y, px(3), y + alt, fill=COR["destaque"], outline="")
            cor = "white" if sel else COR["lateral_texto"]
            self.create_text(px(22), y + alt / 2, anchor="w", text=icone, fill=cor,
                             font=(FONTE, 10))
            self.create_text(px(52), y + alt / 2, anchor="w", text=texto, fill=cor,
                             font=(FONTE, 10, "bold" if sel else "normal"))
            self.zonas.append((y, y + alt, chave))
            y += alt
        estado, detalhe, ativo = self.situacao
        self.create_text(px(20), h - px(42), anchor="w", text=estado, tags="estado",
                         fill="white" if ativo else COR["lateral_texto"], font=(FONTE, 9, "bold"))
        self.create_text(px(20), h - px(22), anchor="w", text=detalhe, tags="detalhe",
                         fill=COR["lateral_texto"], font=(FONTE, 9))

    def estado(self, estado, detalhe="", ativo=False):
        if (estado, detalhe, ativo) == self.situacao:
            return
        self.situacao = (estado, detalhe, ativo)
        self.itemconfigure("estado", text=estado, fill="white" if ativo else COR["lateral_texto"])
        self.itemconfigure("detalhe", text=detalhe)


# ---------------------------------------------------------------- aplicativo
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.escala = self.winfo_fpixels("1i") / 96
        self.titulo_base = TITULO
        self.title(self.titulo_base)
        self.geometry(f"{self.px(1240)}x{min(self.px(880), self.winfo_screenheight() - 80)}")
        self.minsize(self.px(1040), self.px(700))
        self.configure(bg=COR["fundo"])

        self.fila, self.parar = queue.Queue(), threading.Event()
        self.ui = GuiUi(self.fila, self.parar)
        me.ui = self.ui  # o motor passa a falar com a janela
        self.rodando = False
        self.rel = None  # relatório da execução atual (vai para o histórico)
        self.pastas_origem = {}  # nome bruto -> [selecionada, mensagens]
        self.contas_csv = []  # [(src_user, dst_user)]
        self.alvos = {}  # barra de progresso -> valor alvo (animação suave)
        self.pagina_atual = None

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
            s.map(f"{nome}.TButton", background=[("active", COR["selecionado"])],
                  lightcolor=[("active", COR["selecionado"])],
                  darkcolor=[("active", COR["selecionado"])],
                  bordercolor=[("active", COR["destaque"])],
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
                    bordercolor=COR["borda"], rowheight=self.px(28), font=f)
        s.configure("Treeview.Heading", background=COR["fundo"], foreground=COR["tinta2"],
                    bordercolor=COR["borda"], font=(FONTE, 9, "bold"), relief="flat",
                    padding=(6, 5))
        s.map("Treeview", background=[("selected", COR["selecionado"])],
              foreground=[("selected", COR["tinta"])])
        s.map("Treeview.Heading", background=[("active", COR["borda"])])
        s.configure("Vertical.TScrollbar", background=COR["fundo"], troughcolor=COR["superficie"],
                    bordercolor=COR["superficie"], arrowcolor=COR["tinta2"])

    def rotulo(self, pai, texto, linha, coluna, colspan=1):
        lb = ttk.Label(pai, text=texto, style="Campo.TLabel")
        lb.grid(row=linha, column=coluna, columnspan=colspan, sticky="w", pady=(10, 3))
        return lb

    def cartao(self, pai, padding=16, **pack):
        """Moldura única com espaçamento interno (uma janela do sistema a menos)."""
        f = tk.Frame(pai, bg=COR["superficie"], highlightthickness=1,
                     highlightbackground=COR["borda"], padx=self.px(padding),
                     pady=self.px(padding))
        if pack:
            f.pack(**pack)
        return f, f

    # -------------------------------------------------------------- layout
    def _montar(self):
        # navegação e andamento resumido, sempre visíveis, em qualquer página
        self.lateral = BarraLateral(self, self.px, self.mostrar)
        self.lateral.pack(side="left", fill="y")

        area = ttk.Frame(self)
        area.pack(side="left", fill="both", expand=True)
        self.paginas = {}
        for chave, construtor in (("config", self._pagina_config),
                                  ("progresso", self._pagina_progresso),
                                  ("historico", self._pagina_historico),
                                  ("opcoes", self._pagina_opcoes)):
            pg = ttk.Frame(area, padding=(self.px(28), self.px(22)))
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
        self.cartoes_modo = CartoesModo(pg, self.px, self.v["modo"].set)
        self.cartoes_modo.pack(fill="x")

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
        self.tab_cfg = Tabela(corpo, self.px, [("sel", "", 44, "center", False),
                                               ("pasta", "Pasta", 400, "w", True),
                                               ("msgs", "Mensagens", 120, "e", False)],
                              altura=4, selecao=False)
        self.tab_cfg.pack(fill="both", expand=True, pady=(self.px(10), 0))
        self.tab_cfg.tv.configure(cursor="hand2")
        self.tab_cfg.tv.bind("<Button-1>", self._clique_pasta)

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
        self.tab_csv = Tabela(corpo, self.px, [("n", "#", 50, "w", False),
                                               ("src", "Conta de origem", 300, "w", True),
                                               ("dst", "Conta de destino", 300, "w", True)],
                              altura=4, selecao=False)
        self.tab_csv.pack(fill="both", expand=True, pady=(self.px(8), 0))

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
            self.tab_cfg.inserir(("☑", rotulo, num(n)), iid=nome)
        self._atualizar_contagem_pastas()

    def _clique_pasta(self, e):
        iid = self.tab_cfg.tv.identify_row(e.y)
        if iid in self.pastas_origem:
            self.pastas_origem[iid][0] = not self.pastas_origem[iid][0]
            self.tab_cfg.tv.set(iid, "sel", "☑" if self.pastas_origem[iid][0] else "☐")
            self._atualizar_contagem_pastas()

    def _marcar_todas(self, valor):
        for iid, item in self.pastas_origem.items():
            item[0] = valor
            self.tab_cfg.tv.set(iid, "sel", "☑" if valor else "☐")
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
            self.csv_info.configure(text=f"Não foi possível ler o CSV: {e}", style="CartaoErro.TLabel")
            return
        self.contas_csv = [(row["src_user"].strip(), row["dst_user"].strip()) for row in contas]
        for n, (src, dst) in enumerate(self.contas_csv, 1):
            self.tab_csv.inserir((n, src, dst))
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
        topo = self._cabecalho(pg, "Andamento", "Acompanhe em tempo real. Cada execução "
                                                "fica guardada no Histórico.")
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

        self.kpis = PainelKpi(pg, self.px, (
            ("copiadas", "Copiadas", SERIES["copiadas"]),
            ("puladas", "Já existiam", SERIES["puladas"]),
            ("erros", "Erros", SERIES["erros"]),
            ("bytes", "Transferido", None), ("tempo", "Tempo", None)))
        self.kpis.pack(fill="x")

        self.zona = ttk.Frame(pg)
        self.zona.pack(fill="x", pady=(self.px(14), 0))
        self._montar_card_progresso()
        self._montar_banner()

        graficos = ttk.Frame(pg)
        graficos.pack(fill="x", pady=(self.px(14), 0))
        graficos.columnconfigure((0, 1), weight=1, uniform="graf")
        for i, classe in enumerate((GraficoVelocidade, GraficoPastas)):
            c, _ = self.cartao(graficos, padding=14)
            c.grid(row=0, column=i, sticky="nsew",
                   padx=(0, self.px(7)) if i == 0 else (self.px(7), 0))
            g = classe(c, self.px)  # o próprio gráfico desenha título e contexto
            g.pack(fill="both", expand=True)
            if i == 0:
                self.graf_vel = g
            else:
                self.graf_pastas = g

        _, corpo = self.cartao(pg, padding=12, fill="both", expand=True, pady=(self.px(14), 0))
        self.abas = Abas(corpo, self.px)
        self.abas.pack(fill="both", expand=True)
        f = self.abas.adicionar("pastas", "Pastas")
        self.tab_pastas = Tabela(f, self.px, COLS_PASTAS, altura=4)
        self.tab_pastas.pack(fill="both", expand=True)
        f = self.abas.adicionar("contas", "Contas")
        self.tab_contas = Tabela(f, self.px, COLS_CONTAS, altura=4)
        self.tab_contas.pack(fill="both", expand=True)
        f = self.abas.adicionar("problemas", "Problemas (0)")
        self.tab_problemas = Tabela(f, self.px, COLS_PROBLEMAS, altura=4)
        self.tab_problemas.pack(fill="both", expand=True)
        f = self.abas.adicionar("log", "Registro completo")
        self.lbl_log_cortado = ttk.Label(f, style="CartaoMudo.TLabel", text="")
        caixa = ttk.Frame(f, style="Cartao.TFrame")
        caixa.pack(fill="both", expand=True)
        self.txt_log = tk.Text(caixa, height=5, wrap="none", bd=0, padx=8, pady=6,
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
        self.abas.visivel("contas", False)
        self._resetar_painel()
        self._estado("parado")

    def _montar_card_progresso(self):
        self.card_prog, corpo = self.cartao(self.zona)
        self.card_prog.pack(fill="x")
        linha = ttk.Frame(corpo, style="Cartao.TFrame")
        linha.pack(fill="x")
        self.lbl_pasta = ttk.Label(linha, text="Nenhuma migração em andamento",
                                   style="CartaoTitulo.TLabel")
        self.lbl_pasta.pack(side="left")
        self.lbl_conta = ttk.Label(linha, text="", style="CartaoMudo.TLabel")
        self.lbl_conta.pack(side="right")
        self.lbl_pasta_info = ttk.Label(corpo, text="", style="CartaoMudo.TLabel")
        self.lbl_pasta_info.pack(anchor="w", pady=(2, 10))
        grade = ttk.Frame(corpo, style="Cartao.TFrame")
        grade.pack(fill="x")
        grade.columnconfigure(1, weight=1)
        self.barras = {}
        for i, (chave, rotulo) in enumerate((("pasta", "Pasta atual"), ("total", "Total"))):
            lb = ttk.Label(grade, text=rotulo, style="Campo.TLabel", width=11)
            lb.grid(row=i, column=0, sticky="w", pady=4)
            bar = ttk.Progressbar(grade, style="Barra.Horizontal.TProgressbar", maximum=1)
            bar.grid(row=i, column=1, sticky="ew", padx=(8, 14))
            # largura fixa: o texto muda sem empurrar a barra
            txt = ttk.Label(grade, text="", style="Cartao.TLabel", width=48, anchor="e")
            txt.grid(row=i, column=2, sticky="e")
            self.barras[chave] = (lb, bar, txt)

    def _montar_banner(self):
        px = self.px
        self.banner = tk.Frame(self.zona, highlightthickness=1)
        dentro = tk.Frame(self.banner)
        dentro.pack(fill="x", padx=px(18), pady=px(14))
        dentro.columnconfigure(1, weight=1)
        self.ban_icone = tk.Label(dentro, font=(FONTE, 22, "bold"))
        self.ban_icone.grid(row=0, column=0, rowspan=2, padx=(0, px(14)))
        self.ban_titulo = tk.Label(dentro, font=(FONTE, 13, "bold"), anchor="w")
        self.ban_titulo.grid(row=0, column=1, sticky="w")
        self.ban_texto = tk.Label(dentro, font=(FONTE, 10), anchor="w", justify="left",
                                  wraplength=px(560))
        self.ban_texto.grid(row=1, column=1, sticky="w")
        botoes = tk.Frame(dentro)
        botoes.grid(row=0, column=2, rowspan=2, sticky="e")
        # o texto se ajusta à largura que sobra ao lado dos botões, em vez de ser cortado
        dentro.bind("<Configure>", lambda e: self.ban_texto.configure(
            wraplength=max(px(200), e.width - botoes.winfo_reqwidth() - px(90))))
        self.ban_problemas = ttk.Button(botoes, text="Ver problemas", style="Secundario.TButton",
                                        command=lambda: self.abas.mostrar("problemas"))
        self.ban_problemas.pack(side="left")
        self.ban_exportar = ttk.Button(botoes, text="Exportar relatório", style="Secundario.TButton",
                                       command=lambda: self.exportar_relatorio(self.rel))
        self.ban_exportar.pack(side="left", padx=(8, 0))
        ttk.Button(botoes, text="Nova migração", style="Secundario.TButton",
                   command=lambda: self.mostrar("config")).pack(side="left", padx=(8, 0))
        self.ban_fundos = [self.banner, dentro, self.ban_icone, self.ban_titulo,
                           self.ban_texto, botoes]

    def _mostrar_banner(self, estado, titulo, texto):
        icone, fundo, borda, tinta = BANNER[estado]
        for w in self.ban_fundos:
            w.configure(bg=fundo)
        self.banner.configure(highlightbackground=borda)
        self.ban_icone.configure(text=icone, fg=tinta)
        self.ban_titulo.configure(text=titulo, fg=tinta)
        self.ban_texto.configure(text=texto, fg=COR["tinta"])
        problemas = len(self.rel["problemas"])
        if problemas:
            self.ban_problemas.configure(text=f"Ver problemas ({num(problemas)})")
            self.ban_problemas.pack(side="left", before=self.ban_exportar)
        else:
            self.ban_problemas.pack_forget()
        self.card_prog.pack_forget()
        self.banner.pack(fill="x")

    def _estado(self, estado):
        texto, fundo, tinta = PILULAS[estado]
        self.pilula.configure(text=texto, bg=fundo, fg=tinta)
        ativo = estado in ("rodando", "parando")
        detalhe = self.lateral.situacao[1] if ativo else ""
        self.lateral.estado("● Parando…" if estado == "parando" else
                            "● Migrando…" if ativo else "● Pronto", detalhe, ativo)
        if not ativo:
            self.title(self.titulo_base)
        self.btn_parar.state(["!disabled"] if estado == "rodando" else ["disabled"])

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
        self.graf_vel.limpar()
        self.graf_pastas.limpar()
        self.graf_vel.definir_extra("")
        self.graf_pastas.definir_extra("")
        for _, bar, txt in self.barras.values():
            bar.stop()
            bar.configure(mode="determinate", value=0)
            txt.configure(text="")
            self.alvos[bar] = 0
        self.lbl_pasta.configure(text="Nenhuma migração em andamento")
        self.lbl_pasta_info.configure(text="")
        self.lbl_conta.configure(text="")
        for t in (self.tab_pastas, self.tab_contas, self.tab_problemas):
            t.limpar()
        self.abas.titulo("problemas", "Problemas (0)")
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")
        self.lbl_log_cortado.pack_forget()
        self.banner.pack_forget()
        self.card_prog.pack(fill="x")
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
        if not self.inicio:
            for chave, _, _ in self.kpis.itens:
                self.kpis.set(chave, "—")
            return
        t = self._totais()
        dec = time.time() - self.inicio
        self.kpis.set("copiadas", num(t["copiadas"]), "novas no destino")
        self.kpis.set("puladas", num(t["puladas"]), "puladas, sem duplicar")
        self.kpis.set("erros", num(t["erros"]), "nenhum até agora" if not t["erros"]
                      else "detalhes na aba Problemas")
        self.kpis.set("bytes", me.fmt_bytes(t["bytes"]),
                      f"média de {me.fmt_bytes(t['bytes'] / dec)}/s" if dec > 1 else "")
        eta = self._eta() if self.rodando else 0
        self.kpis.set("tempo", me.fmt_tempo(dec), f"faltam ~{me.fmt_tempo(eta)}" if eta else "")

    def _atualizar_progresso(self):
        a = self.atual
        _, bar, txt = self.barras["pasta"]
        if a["inicio"] is not None:
            pct = a["feito"] / a["total"] if a["total"] else 1
            dec = time.time() - a["inicio"]
            eta = dec / a["feito"] * (a["total"] - a["feito"]) if a["feito"] else 0
            txt.configure(text=f"{num(a['feito'])} de {num(a['total'])} · {pct:.0%}"
                               + (f" · faltam ~{me.fmt_tempo(eta)}" if a["feito"] < a["total"] else ""))
            self.alvos[bar] = pct
        _, bar, txt = self.barras["total"]
        pct = None
        if self.total_geral:
            feitas = min(self._processadas(), self.total_geral)
            pct = feitas / self.total_geral
            txt.configure(text=f"{num(feitas)} de {num(self.total_geral)} mensagens · {pct:.0%}")
        elif self.contas_total:
            pct = self.contas_feitas / self.contas_total
            txt.configure(text=f"{self.contas_feitas} de {self.contas_total} contas concluídas")
        else:
            txt.configure(text=f"{self.pastas_feitas} pastas concluídas")
        if pct is not None:
            self.alvos[bar] = pct
            bar.grid()
            self.title(f"{pct:.0%} · {self.titulo_base}")
            self.lateral.estado(self.lateral.situacao[0], f"{pct:.0%} concluído", True)
        else:
            bar.grid_remove()  # total desconhecido: sem barra que engane
            self.lateral.estado(self.lateral.situacao[0], f"{self.pastas_feitas} pastas concluídas", True)

    def _animar_barras(self):
        """Barras deslizam até o valor novo em vez de pular."""
        for bar, alvo in self.alvos.items():
            if str(bar.cget("mode")) != "determinate":
                continue
            v = float(bar.cget("value"))
            if alvo < v or abs(alvo - v) < 0.003:
                if v != alvo:
                    bar.configure(value=alvo)
            else:
                bar.configure(value=v + (alvo - v) * 0.3)

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
            self.lbl_log_cortado.pack(anchor="w", pady=(0, 4), before=t.master)
        t.configure(state="disabled")
        if acompanhar:
            t.see("end")

    # ------------------------------------------------------ página: histórico
    def _pagina_historico(self, pg):
        topo = self._cabecalho(pg, "Histórico", "Toda execução fica registrada com o resultado de "
                                                "cada pasta e cada problema, mesmo depois de fechar o app.")
        acoes = ttk.Frame(topo)
        acoes.pack(side="right")
        ttk.Button(acoes, text="Abrir pasta dos relatórios", style="Secundario.TButton",
                   command=self._abrir_pasta_historico).pack(side="left")

        _, corpo = self.cartao(pg, padding=12, fill="x")
        self.tab_hist = Tabela(corpo, self.px, COLS_HIST, altura=6)
        self.tab_hist.pack(fill="both", expand=True)
        self.tab_hist.tv.bind("<<TreeviewSelect>>", lambda e: self._mostrar_execucao())

        _, corpo = self.cartao(pg, fill="both", expand=True, pady=(self.px(14), 0))
        linha = ttk.Frame(corpo, style="Cartao.TFrame")
        linha.pack(fill="x")
        self.hist_titulo = ttk.Label(linha, text="Nenhuma migração registrada ainda.",
                                     style="CartaoTitulo.TLabel")
        self.hist_titulo.pack(side="left")
        self.hist_exportar = ttk.Button(linha, text="Exportar relatório (CSV)",
                                        style="Secundario.TButton",
                                        command=lambda: self.exportar_relatorio(self.hist_sel))
        self.hist_exportar.pack(side="right")
        self.hist_exportar.state(["disabled"])
        self.hist_resumo = ttk.Label(corpo, text="", style="CartaoMudo.TLabel",
                                     wraplength=self.px(900), justify="left")
        self.hist_resumo.pack(anchor="w", pady=(2, 8))
        self.hist_abas = Abas(corpo, self.px)
        self.hist_abas.pack(fill="both", expand=True)
        f = self.hist_abas.adicionar("pastas", "Pastas")
        self.hist_pastas = Tabela(f, self.px, COLS_PASTAS, altura=4)
        self.hist_pastas.pack(fill="both", expand=True)
        f = self.hist_abas.adicionar("contas", "Contas")
        self.hist_contas = Tabela(f, self.px, COLS_CONTAS, altura=4)
        self.hist_contas.pack(fill="both", expand=True)
        f = self.hist_abas.adicionar("problemas", "Problemas")
        self.hist_problemas = Tabela(f, self.px, COLS_PROBLEMAS, altura=4)
        self.hist_problemas.pack(fill="both", expand=True)
        self.historico, self.hist_sel = [], None

    def _atualizar_historico(self):
        anterior = self.hist_sel["id"] if self.hist_sel else None
        self.historico = carregar_historico()
        self.tab_hist.limpar()
        for rel in self.historico:
            em_curso = self.rodando and self.rel and rel["id"] == self.rel["id"]
            t = rel["totais"]
            self.tab_hist.inserir(
                (data_hora(rel["inicio"]), rel["tipo"], f"{rel['origem']} → {rel['destino']}",
                 "● Em andamento" if em_curso else RESULTADO.get(rel["estado"], rel["estado"]),
                 num(t["copiadas"]), num(t["puladas"]), num(t["erros"]),
                 me.fmt_tempo(rel.get("duracao", 0))),
                ("ativa",) if em_curso else ("erro",) if t["erros"] or rel["estado"] == "falha" else (),
                iid=rel["id"])
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
                 f"{RESULTADO.get(rel['estado'], rel['estado'])} em "
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
        self.tab_pastas.colunas_visiveis([c[0] for c in COLS_PASTAS if lote or c[0] != "conta"])
        self.tab_problemas.colunas_visiveis([c[0] for c in COLS_PROBLEMAS if lote or c[0] != "conta"])
        self.abas.visivel("contas", lote)
        self.abas.mostrar("contas" if lote else "pastas")
        self.graf_pastas.definir_extra("conta atual" if lote else "")
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
            self.btn_abrir_dir.pack(side="left", padx=(8, 0), after=self.btn_parar)
        else:
            self.btn_abrir_dir.pack_forget()
        self.lbl_pasta.configure(text="Conectando…")
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
            self.btn_parar.state(["disabled"])

    def _relogio(self):
        if not self.rodando:
            return
        agora = time.time()
        total = self._totais()["bytes"]
        self.amostras.append((agora, total))
        self.amostras = [s for s in self.amostras if agora - s[0] <= 5.5]
        t0, b0 = self.amostras[0]
        if agora - t0 >= 1:
            vel = (total - b0) / (agora - t0)
            self.graf_vel.adicionar(agora - self.inicio, vel)
            self.graf_vel.definir_extra(f"agora {me.fmt_bytes(vel)}/s")
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
        self._animar_barras()
        self.after(50, self._drenar)

    def _registrar_problema(self, nivel, texto):
        if nivel == "info" and self.rodando:
            self.lbl_pasta_info.configure(text=decodificar_utf7(texto))
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
            self.n_pasta_txt = decodificar_utf7(ev[1])
            self.pasta_ctx = re.sub(r" \(\d+ \w+\)$", "", self.n_pasta_txt).split(" → ")[0]
            self.atual = self._novo_atual()
            self.atual["inicio_pasta"] = time.time()
            self.erros_atual = 0
            de = f" · pasta {self.pastas_feitas + 1} de {self.total_pastas}" if self.total_pastas else ""
            self.lbl_pasta.configure(text=self.n_pasta_txt)
            self.lbl_pasta_info.configure(text=f"Comparando com o destino…{de}")
            _, bar, txt = self.barras["pasta"]
            txt.configure(text="preparando…")
            bar.configure(mode="indeterminate")
            bar.start(20)
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
                _, bar, _ = self.barras["pasta"]
                bar.stop()
                bar.configure(mode="determinate", value=0)
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
            _, bar, txt = self.barras["pasta"]
            bar.stop()
            bar.configure(mode="determinate")
            self.alvos[bar] = 1
            txt.configure(text="concluída")
            self.graf_pastas.adicionar(ev[1])
            self._prog_mudou = True
            self._salvar_relatorio()
        elif tipo == "conta":
            _, n, total, src, dst = ev
            self._fechar_pasta_parcial()
            self.conta_n, self.conta_atual, self.pasta_ctx = n, src, ""
            self.lbl_conta.configure(text=f"Conta {n} de {total}: {src} → {dst}")
            self.graf_pastas.limpar()
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
        for _, bar, _ in self.barras.values():
            bar.stop()
            bar.configure(mode="determinate")
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

    def _fechar(self):
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
