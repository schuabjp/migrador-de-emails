#!/usr/bin/env python3
"""
Migrador de E-mails — aplicativo desktop (PySide6/Qt).

Interface gráfica para o migrar_email.py: usa o mesmo motor (mesmos modos,
deduplicação, reconexão e notificações); esta camada coleta os dados, testa
as conexões, mostra o andamento e guarda um relatório de cada execução.

    pip install -r requirements.txt
    pythonw migrador_gui.py
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
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

import atualizador  # noqa: E402
import migrar_email as me  # noqa: E402
import tema  # noqa: E402
from componentes import (  # noqa: E402
    Abas, BarraLateral, BarraSimples, Botao, Campo, Cartao, CartaoModo, Combo, GraficoVelocidade,
    Kpi, Ladrilho, PainelProgresso, Segmentado, Selo, Superficie, Tabela, Toast, Var, avisar,
    caixa, mudar_papel, perguntar, rotulo)
from tema import TEMA, TEMAS  # noqa: E402
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
                 "notify_email", "smtp_host", "smtp_port", "smtp_user", "smtp_from", "tema"]
MODOS_GUI = {  # modo da tela -> (título, descrição, modo do motor, ícone)
    "imap2imap": ("Servidor → Servidor", "Copia direto de um IMAP para outro",
                  "imap2imap", "arrows-left-right"),
    "export": ("Exportar backup", "Salva as mensagens em arquivos .eml", "export",
               "download-simple"),
    "import": ("Importar backup", "Envia arquivos .eml para um servidor", "import",
               "upload-simple"),
    "lote": ("Várias contas", "Migra uma lista de contas de um CSV", "imap2imap",
             "users-three"),
}
SELO = {  # estado -> (texto, tipo do selo, ícone)
    "parado": ("Aguardando", "neutro", None), "rodando": ("Em andamento", "info", None),
    "parando": ("Parando…", "aviso", None), "ok": ("Concluído", "ok", "check-circle-fill"),
    "falha": ("Com problemas", "erro", "x-circle-fill"),
    "cancelado": ("Interrompido", "aviso", "pause-circle-fill"),
}
BANNER = {  # estado -> (ícone, fundo, borda, tinta, cores do anel)
    "ok": ("check-circle-fill", "ok_claro", "ok", "ok_texto"),
    "falha": ("x-circle-fill", "erro_claro", "erro", "erro_texto"),
    "cancelado": ("pause-circle-fill", "aviso_claro", "aviso", "aviso_texto"),
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
# colunas: (id, título, largura, alinhamento, elástica, tipo)
COLS_PASTAS = [("sit", "Situação", 150, "w", False, "status"), ("conta", "Conta", 180, "w", False),
               ("pasta", "Pasta", 240, "w", True), ("prog", "Progresso", 150, "w", False, "progresso"),
               ("msgs", "Mensagens", 96, "e", False, "num"), ("cop", "Copiadas", 92, "e", False, "num"),
               ("pul", "Já existiam", 100, "e", False, "num"), ("err", "Erros", 64, "e", False, "num"),
               ("tam", "Tamanho", 90, "e", False, "num"), ("tempo", "Tempo", 76, "e", False, "num")]
VISIVEIS_ANDAMENTO = ["sit", "conta", "pasta", "prog", "msgs", "cop", "pul", "err"]
COLS_PROBLEMAS = [("tipo", "Tipo", 110, "w", False, "status"), ("hora", "Hora", 100, "w", False, "mono"),
                  ("conta", "Conta", 180, "w", False), ("pasta", "Pasta", 180, "w", False),
                  ("msg", "Mensagem", 420, "w", True)]
COLS_CONTAS = [("sit", "Situação", 136, "w", False, "status"), ("n", "#", 44, "w", False, "num"),
               ("src", "Origem", 150, "w", True), ("dst", "Destino", 150, "w", True),
               ("cop", "Copiadas", 88, "e", False, "num"), ("pul", "Já existiam", 98, "e", False, "num"),
               ("err", "Erros", 60, "e", False, "num"), ("det", "Detalhe", 160, "w", True)]
COLS_HIST = [("res", "Resultado", 176, "w", False, "status"), ("data", "Data", 130, "w", False),
             ("tipo", "Tipo", 150, "w", False), ("rota", "De → Para", 340, "w", True),
             ("cop", "Copiadas", 90, "e", False, "num"), ("pul", "Já existiam", 100, "e", False, "num"),
             ("err", "Erros", 64, "e", False, "num"), ("dur", "Duração", 80, "e", False, "num")]
COLS_CFG = [("pasta", "Pasta", 420, "w", True, "check"), ("msgs", "Mensagens", 120, "e", False, "num")]
COLS_CSV = [("n", "#", 50, "w", False, "num"), ("src", "Conta de origem", 300, "w", True),
            ("dst", "Conta de destino", 300, "w", True)]
SIMBOLOS_TELA = {"info": "•", "ok": "✔", "aviso": "▲", "erro": "✖"}  # sem virar emoji
NIVEIS_LOG = {"hora": "muda", "info": "tinta2", "ok": "ok_texto", "aviso": "aviso_texto",
              "erro": "erro_texto", "titulo": "destaque_tinta", "secao": "tinta", "texto": "tinta"}


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
    feitas = p["copiadas"] + p["puladas"] + p["erros"]
    if p["status"] == "pendente":
        prog = None
    elif p["status"] in ("ok", "erros"):
        prog = 1.0
    else:
        prog = min(1.0, feitas / p["mensagens"]) if p["mensagens"] else 0.0
    return (((icone, texto), p["conta"], decodificar_utf7(p["pasta"]), prog, num(p["mensagens"]),
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
        for rotulo_, valor in (("Início", data_hora(rel["inicio"])),
                               ("Duração", me.fmt_tempo(rel.get("duracao", 0))),
                               ("Tipo", rel["tipo"]), ("Origem", rel["origem"]),
                               ("Destino", rel["destino"]),
                               ("Resultado", RESULTADO.get(rel["estado"], (rel["estado"],))[0]),
                               ("Resumo", rel.get("resumo", "")), ("Copiadas", t["copiadas"]),
                               ("Já existiam", t["puladas"]), ("Erros", t["erros"]),
                               ("Transferido (bytes)", t["bytes"])):
            w.writerow([rotulo_, valor])
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


# ------------------------------------------------------------ peças da tela
def cabecalho(lay, titulo, subtitulo, mono=False, espaco=10):
    """Título da página + subtítulo; devolve (linha, rótulo do subtítulo)."""
    linha = QtWidgets.QHBoxLayout()
    linha.setSpacing(12)
    col = caixa("v", rotulo(titulo, "titulo"), espaco=2)
    sub = rotulo(subtitulo, "mono" if mono else "sub", quebra=not mono)
    col.addWidget(sub)
    linha.addLayout(col, 1)
    lay.addLayout(linha)
    if espaco:
        lay.addSpacing(espaco)
    return linha, sub


def topo_cartao(nome_icone, titulo, subtitulo, tinta="destaque", fundo="destaque_claro"):
    lad = Ladrilho(nome_icone, tinta, fundo, 40)
    tit, sub = rotulo(titulo, "cartao_titulo"), rotulo(subtitulo, "mudo")
    return caixa("h", lad, caixa("v", tit, sub, espaco=1), None, espaco=12), tit, sub, lad


class CartaoServidor(Cartao):
    """Servidor IMAP + usuário + senha + botão de teste."""

    def __init__(self, app, lado, titulo, subtitulo, nome_icone):
        super().__init__(padding=20, espaco=0)
        self.app, self.lado = app, lado
        v = app.v
        topo, *_ = topo_cartao(nome_icone, titulo, subtitulo)
        self.lay.addLayout(topo)
        self.lay.addSpacing(14)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(5)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        g.addWidget(rotulo("Servidor IMAP", "rotulo"), 0, 0, 1, 2)
        g.addWidget(rotulo("Porta", "rotulo"), 0, 2)
        g.addWidget(Combo(v[f"{lado}_host"], SERVIDORES), 1, 0, 1, 2)
        porta = Campo(v[f"{lado}_port"])
        porta.setFixedWidth(92)
        g.addWidget(porta, 1, 2)
        g.setRowMinimumHeight(2, 10)
        self.credenciais = [rotulo("Usuário (e-mail)", "rotulo"), rotulo("Senha (ou senha de app)", "rotulo"),
                            Campo(v[f"{lado}_user"], dica="voce@empresa.com"),
                            Campo(v[f"{lado}_pass"], senha=True)]
        g.addWidget(self.credenciais[0], 3, 0)
        g.addWidget(self.credenciais[1], 3, 1, 1, 2)
        g.addWidget(self.credenciais[2], 4, 0)
        g.addWidget(self.credenciais[3], 4, 1, 1, 2)
        self.nota_lote = rotulo("Usuários e senhas vêm do arquivo CSV.", "mudo")
        g.addWidget(self.nota_lote, 3, 0, 2, 3)
        self.lay.addLayout(g)
        self.lay.addSpacing(16)
        self.botao = Botao("Testar conexão", "wifi-high", comando=lambda: app.testar_conexao(lado))
        self.status = rotulo("", "mudo", quebra=True)
        self.lay.addLayout(caixa("h", self.botao, self.status, espaco=12))
        self.lay.addStretch(1)
        self.nota_lote.hide()

    def modo_lote(self, lote):
        for w in self.credenciais:
            w.setVisible(not lote)
        self.nota_lote.setVisible(lote)
        self.botao.setVisible(not lote)
        self.mostrar_status("")

    def mostrar_status(self, txt, tipo="info"):
        self.status.setText(txt)
        mudar_papel(self.status, {"ok": "ok", "erro": "erro"}.get(tipo, "mudo"))


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


class DialogoAtualizacao(QtWidgets.QDialog):
    """Mostra a versão nova, baixa com progresso e oferece reiniciar."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Atualização do Migrador")
        self.setModal(False)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(6)
        self.lad = Ladrilho("arrow-circle-up-fill", "destaque", "destaque_claro", 48, redondo=True)
        self.titulo = rotulo("", "cartao_titulo")
        self.sub = rotulo("", "sub", quebra=True)
        lay.addLayout(caixa("h", self.lad, caixa("v", self.titulo, self.sub, espaco=2), espaco=16))
        lay.addSpacing(12)
        lay.addWidget(rotulo("Novidades", "passo"))
        quadro = Cartao(padding=14, fundo="superficie2", sombra=False)
        self.notas = QtWidgets.QPlainTextEdit()
        self.notas.setReadOnly(True)
        self.notas.setFixedHeight(150)
        quadro.lay.addWidget(self.notas)
        lay.addWidget(quadro)
        lay.addSpacing(8)
        self.barra = BarraSimples()
        lay.addWidget(self.barra)
        self.estado = rotulo("", "mudo", quebra=True)
        lay.addWidget(self.estado)
        lay.addSpacing(12)
        self.btn_site = Botao("Abrir página da versão", "arrow-square-out", "discreto",
                              comando=self._abrir_site)
        self.btn_depois = Botao("Depois", comando=self.close)
        self.btn_principal = Botao("Atualizar agora", "download-simple", "primario")
        lay.addLayout(caixa("h", self.btn_site, None, self.btn_depois, self.btn_principal, espaco=8))
        self.setMinimumWidth(560)
        self.atualizar()

    def showEvent(self, e):
        tema.barra_titulo(self)
        super().showEvent(e)
        g = self.app.frameGeometry()  # centralizado sobre a janela principal
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + (g.height() - self.height()) // 3)
        self.btn_principal.setFocus(Qt.OtherFocusReason)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):  # Enter = botão principal
            if self.btn_principal.isEnabled():
                self.btn_principal.click()
            return
        super().keyPressEvent(e)

    def _abrir_site(self):
        webbrowser.open((self.app.atualizacao or {}).get("pagina") or atualizador.PAGINA)

    def atualizar(self, erro=None):
        app, info, pronta = self.app, self.app.atualizacao, self.app.atualizacao_pronta
        versao_nova = (pronta or info or {}).get("versao", "?")
        self.notas.setPlainText(notas_legiveis(info["notas"]) if info else
                                "A versão nova já foi baixada e conferida.")
        self.btn_site.setVisible(False)
        self.btn_principal.carregando(False)
        mudar_papel(self.estado, "mudo")
        if erro:
            self.barra.hide()
            self.lad.definir("x-circle-fill", "erro", "erro_claro")
            self.titulo.setText("Não foi possível baixar")
            self.estado.setText(erro)
            mudar_papel(self.estado, "erro")
            self.btn_site.setVisible(True)
            self.btn_principal.definir("Tentar de novo", "arrows-clockwise", app.baixar_atualizacao)
            self.btn_principal.setEnabled(True)
            return
        if pronta:
            self.barra.hide()
            self.lad.definir("check-circle-fill", "ok", "ok_claro")
            self.titulo.setText("Atualização pronta para instalar")
            self.sub.setText(f"A versão {versao_nova} foi baixada. Ao reiniciar, o programa já "
                             "abre atualizado. Suas configurações e o histórico continuam.")
            self.btn_principal.definir("Reiniciar agora", "arrows-clockwise", app.reiniciar_para_atualizar)
            if app.rodando:
                self.btn_principal.setEnabled(False)
                self.estado.setText("Há uma migração em andamento: a atualização será instalada "
                                    "sozinha quando você fechar o programa.")
            else:
                self.btn_principal.setEnabled(True)
                self.estado.setText("Se preferir, clique em Depois: ela é instalada sozinha "
                                    "quando você fechar o programa.")
        elif app.baixando:
            self.lad.definir("cloud-arrow-down", "destaque", "destaque_claro")
            self.titulo.setText(f"Baixando a versão {versao_nova}…")
            self.barra.definir("b", "Download", "")
            self.barra.show()
            self.btn_principal.definir("Baixando…")
            self.btn_principal.carregando(True)
        else:
            self.barra.hide()
            self.lad.definir("arrow-circle-up-fill", "destaque", "destaque_claro")
            self.titulo.setText("Nova versão disponível")
            alfa = " (Alpha)" if (info or {}).get("pre") else ""
            self.sub.setText(f"Versão {versao_nova}{alfa} · você está na {VERSAO}")
            self.estado.setText("O download leva poucos segundos e não interrompe uma migração "
                                "em andamento.")
            self.btn_principal.definir("Atualizar agora", "download-simple", app.baixar_atualizacao)
            self.btn_principal.setEnabled(True)

    def progresso(self, feito, total):
        self.barra.alvo("b", feito / total if total else 0)
        self.barra.definir("b", direita=f"{me.fmt_bytes(feito)} de {me.fmt_bytes(total)}"
                           if total else me.fmt_bytes(feito))


def pagina_rolavel():
    """Página com rolagem: (página, conteúdo, layout do conteúdo)."""
    pagina = Superficie()
    externo = QtWidgets.QVBoxLayout(pagina)
    externo.setContentsMargins(0, 0, 0, 0)
    externo.setSpacing(0)
    rolagem = QtWidgets.QScrollArea()
    rolagem.setObjectName("rolagem")
    rolagem.setWidgetResizable(True)
    rolagem.setFrameShape(QtWidgets.QFrame.NoFrame)
    rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    conteudo = Superficie()
    rolagem.setWidget(conteudo)
    externo.addWidget(rolagem, 1)
    lay = QtWidgets.QVBoxLayout(conteudo)
    lay.setContentsMargins(34, 28, 34, 18)
    lay.setSpacing(10)
    return pagina, externo, lay


# ---------------------------------------------------------------- aplicativo
class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.titulo_base = TITULO
        self.setWindowTitle(self.titulo_base)
        tela = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1320, tela.width() - 60), min(920, tela.height() - 60))
        self.setMinimumSize(1100, 720)

        self.fila, self.parar = queue.Queue(), threading.Event()
        self.ui = GuiUi(self.fila, self.parar)
        me.ui = self.ui  # o motor passa a falar com a janela
        self.rodando = False
        self.rel = None  # relatório da execução atual (vai para o histórico)
        self.pastas_origem = {}  # nome bruto -> [selecionada, mensagens, rótulo]
        self.contas_csv = []  # [(src_user, dst_user)]
        self.pagina_atual = None
        self.atualizacao = None  # versão nova publicada (dict do atualizador)
        self.atualizacao_pronta = None  # já baixada e conferida, esperando reiniciar
        self.baixando = False
        self.dialogo = None
        self._reiniciar = False
        self._saindo = False
        self._animacoes = []
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for nome in ("icone.ico", "icone.png"):  # o ícone do .exe em si vem do build
            if os.path.exists(os.path.join(base, nome)):
                self.setWindowIcon(QtGui.QIcon(os.path.join(base, nome)))
                break

        self.v = {k: Var() for k in CAMPOS_PERFIL + ["src_pass", "dst_pass", "smtp_pass"]}
        padroes = {"modo": "imap2imap", "src_port": "993", "dst_port": "993", "timeout": "60",
                   "log": LOG_PADRAO, "smtp_port": "587", "tema": "claro"}
        for k, val in padroes.items():
            self.v[k].set(val)
        self._carregar_perfil()
        TEMA.aplicar(self.v["tema"].get())

        self._montar()
        for k in ("src_host", "src_user"):
            self.v[k].observar(self._invalidar_pastas)
        for k in CAMPOS_PERFIL:
            self.v[k].observar(self._atualizar_resumo)
        self.v["modo"].observar(self._aplicar_modo)
        self.v["csv"].observar(self._ler_csv)
        self.v["dir"].observar(self._previa_dir)
        self.v["tema"].observar(lambda: TEMA.aplicar(self.v["tema"].get()))
        TEMA.mudou.connect(self._tema_mudou)
        self._aplicar_modo()
        self._ler_csv()
        self._previa_dir()
        self.mostrar("config", animar=False)

        self.t_drenar = QtCore.QTimer(self)
        self.t_drenar.timeout.connect(self._drenar)
        self.t_drenar.start(50)
        self.t_relogio = QtCore.QTimer(self)
        self.t_relogio.timeout.connect(self._relogio)
        if atualizador.empacotado():
            self.atualizacao_pronta = atualizador.pendente()
            if self.atualizacao_pronta:  # baixada antes, mas ainda não instalada
                self.lateral.definir_aviso("Atualização pronta", "clique para instalar",
                                           "check-circle-fill")
            atualizador.limpar_sobras()
            self.t_busca = QtCore.QTimer(self)
            self.t_busca.timeout.connect(self.procurar_atualizacao)
            self.t_busca.start(6 * 3600 * 1000)
            QtCore.QTimer.singleShot(3000, self.procurar_atualizacao)

    # -------------------------------------------------------------- layout
    def _montar(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        lay = QtWidgets.QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        versao = f"versão {VERSAO}" if atualizador.empacotado() else "versão de desenvolvimento"
        self.lateral = BarraLateral(versao)  # navegação e andamento, sempre visíveis
        self.lateral.escolhido.connect(self.mostrar)
        self.lateral.aviso_clicado.connect(self.abrir_atualizacao)
        lay.addWidget(self.lateral)
        self.pilha = QtWidgets.QStackedWidget()
        lay.addWidget(self.pilha, 1)
        self.paginas = {}
        for chave, construtor in (("config", self._pagina_config),
                                  ("progresso", self._pagina_progresso),
                                  ("historico", self._pagina_historico),
                                  ("opcoes", self._pagina_opcoes)):
            pg = construtor()
            pg.setObjectName("pagina")
            self.paginas[chave] = pg
            self.pilha.addWidget(pg)
        self.toast = Toast(central)

    def mostrar(self, chave, animar=True):
        if chave == "historico":
            self._atualizar_historico()
        pg = self.paginas[chave]
        if self.pilha.currentWidget() is not pg:
            self.pilha.setCurrentWidget(pg)
            if animar:  # a página nova aparece num fade curto
                efeito = QtWidgets.QGraphicsOpacityEffect(pg)
                pg.setGraphicsEffect(efeito)
                anim = QtCore.QPropertyAnimation(efeito, b"opacity", pg)
                anim.setDuration(200)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
                anim.finished.connect(lambda: pg.setGraphicsEffect(None))
                anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
        self.pagina_atual = chave
        self.lateral.ativar(chave)

    def _tema_mudou(self):
        for w in self.findChildren(QtWidgets.QWidget):
            w.update()
        self._cor_banner()
        self._repintar_log()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast._posicionar()

    # ------------------------------------------------- página: configurar
    def _pagina_config(self):
        pg, externo, lay = pagina_rolavel()
        cabecalho(lay, "Nova migração", "Escolha o tipo, preencha as contas e clique em Iniciar. "
                                        "Pode rodar de novo quantas vezes quiser: nada é duplicado.")
        lay.addWidget(rotulo("1  ·  O que você quer fazer?", "passo"))
        self.cartoes_modo = {}
        linha = QtWidgets.QHBoxLayout()
        linha.setSpacing(14)
        for chave, (titulo, desc, _, nome_icone) in MODOS_GUI.items():
            c = CartaoModo(titulo, desc, nome_icone)
            c.clicado.connect(lambda k=chave: self.v["modo"].set(k))
            self.cartoes_modo[chave] = c
            linha.addWidget(c)
        lay.addLayout(linha)

        lay.addSpacing(14)
        lay.addWidget(rotulo("2  ·  Contas", "passo"))
        self.grade = QtWidgets.QGridLayout()
        self.grade.setHorizontalSpacing(16)
        self.grade.setColumnStretch(0, 1)
        self.grade.setColumnStretch(1, 1)
        self.card_src = CartaoServidor(self, "src", "Origem", "De onde os e-mails saem", "export")
        self.card_dst = CartaoServidor(self, "dst", "Destino", "Para onde os e-mails vão",
                                       "download-simple")
        self.card_dir = Cartao(padding=20, espaco=0)
        topo, self.dir_titulo, self.dir_sub, _ = topo_cartao("folder-open", "", "")
        self.card_dir.lay.addLayout(topo)
        self.card_dir.lay.addSpacing(14)
        self.card_dir.lay.addWidget(rotulo("Pasta no computador", "rotulo"))
        self.card_dir.lay.addSpacing(5)
        self.card_dir.lay.addLayout(caixa("h", Campo(self.v["dir"]),
                                          Botao("Procurar…", "folder-open", comando=self._escolher_dir)))
        self.card_dir.lay.addSpacing(12)
        self.dir_previa = rotulo("", "mudo", quebra=True)
        self.card_dir.lay.addWidget(self.dir_previa)
        self.card_dir.lay.addStretch(1)
        for w in (self.card_src, self.card_dst, self.card_dir):
            self.grade.addWidget(w, 0, 0)
            w.hide()
        lay.addLayout(self.grade)

        lay.addSpacing(14)
        self.passo3 = rotulo("3  ·  Pastas", "passo")
        lay.addWidget(self.passo3)
        self.area_baixo = QtWidgets.QStackedWidget()
        self.area_baixo.setMinimumHeight(260)
        self.area_baixo.addWidget(self._cartao_pastas())
        self.area_baixo.addWidget(self._cartao_csv())
        lay.addWidget(self.area_baixo, 1)

        # rodapé fixo com o resumo e o botão principal
        rodape = Superficie()
        rl = QtWidgets.QVBoxLayout(rodape)
        rl.setContentsMargins(34, 6, 34, 22)
        barra = Cartao(padding=14)
        self.resumo = rotulo("", "rodape", quebra=True)
        self.btn_iniciar = Botao("Iniciar migração", "play-fill", "primario", grande=True,
                                 comando=self.iniciar)
        barra.lay.addLayout(caixa("h", Ladrilho("flow-arrow", "destaque", "destaque_claro", 40),
                                  self.resumo, self.btn_iniciar, espaco=14))
        barra.lay.itemAt(0).layout().setStretch(1, 1)
        rl.addWidget(barra)
        externo.addWidget(rodape)
        return pg

    def _cartao_pastas(self):
        w = QtWidgets.QWidget()
        lay = caixa("v", espaco=10)
        w.setLayout(lay)
        self.btn_pastas = Botao("Carregar pastas da origem", "arrows-clockwise",
                                comando=self.carregar_pastas)
        self.pastas_info = rotulo("Sem carregar, todas as pastas são migradas.", "mudo")
        lay.addLayout(caixa("h", self.btn_pastas, self.pastas_info, None,
                            Botao("Todas", "checks", "discreto", comando=lambda: self._marcar_todas(True)),
                            Botao("Nenhuma", "x-bold", "discreto", comando=lambda: self._marcar_todas(False)),
                            espaco=8))
        self.tab_cfg = Tabela(COLS_CFG, selecionavel=False,
                              vazio=("folders", "Clique em “Carregar pastas da origem” para escolher "
                                                "quais migrar (sem carregar, vão todas)"))
        self.tab_cfg.vista.clicavel = True
        self.tab_cfg.vista.linha_clicada.connect(self._clique_pasta)
        lay.addWidget(self.tab_cfg, 1)
        return w

    def _cartao_csv(self):
        w = QtWidgets.QWidget()
        lay = caixa("v", espaco=10)
        w.setLayout(lay)
        lay.addLayout(caixa("h", rotulo("Arquivo CSV", "rodape"), Campo(self.v["csv"]),
                            Botao("Procurar…", "folder-open", comando=self._escolher_csv)))
        self.csv_info = rotulo("Colunas esperadas: src_user, src_pass, dst_user, dst_pass "
                               "(uma linha por conta).", "mudo")
        lay.addWidget(self.csv_info)
        self.tab_csv = Tabela(COLS_CSV, selecionavel=False,
                              vazio=("users-three", "Escolha o arquivo CSV com as contas"))
        lay.addWidget(self.tab_csv, 1)
        return w

    def _aplicar_modo(self):
        modo = self.v["modo"].get()
        if modo not in MODOS_GUI:
            self.v["modo"].set("imap2imap")
            return
        for chave, c in self.cartoes_modo.items():
            c.selecionar(chave == modo)
        esq, dir_ = {"imap2imap": (self.card_src, self.card_dst),
                     "lote": (self.card_src, self.card_dst),
                     "export": (self.card_src, self.card_dir),
                     "import": (self.card_dir, self.card_dst)}[modo]
        for w in (self.card_src, self.card_dst, self.card_dir):
            if w not in (esq, dir_):
                w.hide()
        self.grade.addWidget(esq, 0, 0)
        self.grade.addWidget(dir_, 0, 1)
        esq.show()
        dir_.show()
        self.card_src.modo_lote(modo == "lote")
        self.card_dst.modo_lote(modo == "lote")
        if modo == "export":
            self.dir_titulo.setText("Salvar em")
            self.dir_sub.setText("Cada pasta do e-mail vira uma pasta com arquivos .eml")
        else:
            self.dir_titulo.setText("Ler de")
            self.dir_sub.setText("Pasta com o backup (.eml) a importar")
        if modo in ("imap2imap", "export"):
            self.passo3.setText("3  ·  Pastas (opcional)")
            self.area_baixo.setCurrentIndex(0)
        elif modo == "lote":
            self.passo3.setText("3  ·  Lista de contas")
            self.area_baixo.setCurrentIndex(1)
        self.passo3.setVisible(modo != "import")
        self.area_baixo.setVisible(modo != "import")
        self._previa_dir()
        verbo = {"imap2imap": "migração", "export": "exportação", "import": "importação",
                 "lote": "migração em lote"}[modo]
        self.btn_iniciar.definir(f"Iniciar {verbo}")
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
            sel = sum(1 for s, _, _ in self.pastas_origem.values() if s)
            texto += f" · {sel} de {len(self.pastas_origem)} pastas"
        self.resumo.setText(texto)

    # ----------------------------------------------------- pastas da origem
    def _invalidar_pastas(self):
        if self.pastas_origem:
            self.pastas_origem = {}
            self.tab_cfg.limpar()
            self.pastas_info.setText("A origem mudou: carregue as pastas de novo "
                                     "(ou deixe assim para migrar todas).")
            self._atualizar_resumo()

    def carregar_pastas(self):
        try:
            conta = self._conta("src", "origem")
        except ValueError as e:
            avisar(self, "Faltam dados", str(e))
            return
        self.btn_pastas.carregando(True)
        self.pastas_info.setText("Conectando e contando mensagens…")
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
        self.btn_pastas.carregando(False)
        if erro:
            self.pastas_info.setText(f"Falhou: {erro}")
            return
        self.pastas_origem = {}
        self.tab_cfg.limpar()
        for nome, delim, n in lista:
            rot = " › ".join(decodificar_utf7(p) for p in nome.split(delim or "/"))
            self.pastas_origem[nome] = [True, n, rot]
            self.tab_cfg.inserir(((True, rot), num(n)), iid=nome)
        self._atualizar_contagem_pastas()

    def _marcar_pasta(self, iid, valor):
        self.pastas_origem[iid][0] = valor
        self.tab_cfg.mudar(iid, "pasta", (valor, self.pastas_origem[iid][2]))

    def _clique_pasta(self, iid):
        if iid in self.pastas_origem:
            self._marcar_pasta(iid, not self.pastas_origem[iid][0])
            self._atualizar_contagem_pastas()

    def _marcar_todas(self, valor):
        for iid in self.pastas_origem:
            self._marcar_pasta(iid, valor)
        self._atualizar_contagem_pastas()

    def _atualizar_contagem_pastas(self):
        sel = [n for s, n, _ in self.pastas_origem.values() if s]
        self.pastas_info.setText(f"{len(sel)} de {len(self.pastas_origem)} pastas · "
                                 f"{num(sum(sel))} mensagens  (clique na linha para marcar/desmarcar)")
        self._atualizar_resumo()

    def _pastas_escolhidas(self):
        if not self.pastas_origem:
            return None
        sel = [nome for nome, (s, _, _) in self.pastas_origem.items() if s]
        if not sel:
            raise ValueError("Marque ao menos uma pasta (ou clique em Todas).")
        return None if len(sel) == len(self.pastas_origem) else sel

    # ------------------------------------------------------- csv / diretório
    def _escolher_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Escolha a pasta", self.v["dir"].get() or os.path.expanduser("~"))
        if d:
            self.v["dir"].set(os.path.normpath(d))

    def _escolher_csv(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Arquivo de contas", "",
                                                     "CSV (*.csv);;Todos (*.*)")
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
            self.csv_info.setText(f"Não foi possível ler o CSV: {e}")
            mudar_papel(self.csv_info, "erro")
            return
        self.contas_csv = [(row["src_user"].strip(), row["dst_user"].strip()) for row in contas]
        for n, (src, dst) in enumerate(self.contas_csv, 1):
            self.tab_csv.inserir((n, src, dst))
        self.csv_info.setText(f"{len(contas)} contas encontradas. As senhas não são exibidas.")
        mudar_papel(self.csv_info, "mudo")

    def _previa_dir(self):
        d = self.v["dir"].get().strip()
        if self.v["modo"].get() != "import" or not d:
            self.dir_previa.setText("")
            return
        if not os.path.isdir(d):
            self.dir_previa.setText("Pasta não encontrada.")
            return
        self.dir_previa.setText("Contando arquivos…")

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
    def _pagina_opcoes(self):
        pg, externo, lay = pagina_rolavel()
        cabecalho(lay, "Opções", "Valem para todas as migrações. As senhas nunca são gravadas em disco.")
        grade = QtWidgets.QGridLayout()
        grade.setSpacing(16)
        grade.setColumnStretch(0, 1)
        grade.setColumnStretch(1, 1)

        c1 = Cartao(padding=20, espaco=5)
        c1.lay.addLayout(topo_cartao("hard-drives",
                                     "Conexão e registro", "Tempo limite e arquivo de log")[0])
        c1.lay.addSpacing(10)
        c1.lay.addWidget(rotulo("Tempo limite sem resposta do servidor (segundos)", "rotulo"))
        t = Campo(self.v["timeout"])
        t.setFixedWidth(120)
        c1.lay.addWidget(t)
        c1.lay.addSpacing(8)
        c1.lay.addWidget(rotulo("Arquivo de log", "rotulo"))
        c1.lay.addLayout(caixa("h", Campo(self.v["log"]),
                               Botao("Procurar…", "folder-open", comando=self._escolher_log)))
        c1.lay.addSpacing(6)
        c1.lay.addLayout(caixa("h", Botao("Abrir log", "file-text", "discreto", comando=self.abrir_log),
                               None))
        c1.lay.addStretch(1)
        grade.addWidget(c1, 0, 0)

        c2 = Cartao(padding=20, espaco=5)
        c2.lay.addLayout(topo_cartao("bell-ringing", "Avisar quando terminar",
                                     "Opcional: webhook do Slack/Discord/Mattermost e/ou e-mail",
                                     "roxo", "roxo_claro")[0])
        c2.lay.addSpacing(10)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(5)
        campos = [(0, 0, 2, "Webhook (URL)", "webhook"), (2, 0, 2, "Enviar resultado para (e-mail)", "notify_email"),
                  (4, 0, 1, "Servidor SMTP", "smtp_host"), (4, 1, 1, "Porta SMTP", "smtp_port"),
                  (6, 0, 1, "Usuário SMTP", "smtp_user"), (6, 1, 1, "Senha SMTP", "smtp_pass"),
                  (8, 0, 1, "Remetente (opcional)", "smtp_from")]
        for linha, col, span, txt, chave in campos:
            g.addWidget(rotulo(txt, "rotulo"), linha, col, 1, span)
            g.addWidget(Campo(self.v[chave], senha=chave == "smtp_pass"), linha + 1, col, 1, span)
        c2.lay.addLayout(g)
        grade.addWidget(c2, 0, 1, 2, 1)

        c3 = Cartao(padding=20, espaco=5)
        c3.lay.addLayout(topo_cartao("palette", "Aparência", "Tema claro, escuro ou igual ao Windows",
                                     "aviso_texto", "aviso_claro")[0])
        c3.lay.addSpacing(12)
        self.seg_tema = Segmentado()
        for chave, txt in TEMAS.items():
            self.seg_tema.adicionar(chave, txt)
        self.seg_tema.selecionar(self.v["tema"].get())
        self.seg_tema.escolhido.connect(lambda k: (self.seg_tema.selecionar(k), self.v["tema"].set(k)))
        c3.lay.addLayout(caixa("h", self.seg_tema, None))
        c3.lay.addStretch(1)
        grade.addWidget(c3, 1, 0)

        c4 = Cartao(padding=20, espaco=5)
        c4.lay.addLayout(topo_cartao(
            "arrow-circle-up", "Versão e atualizações",
            f"Versão {VERSAO} · procura atualizações ao abrir e a cada 6 horas"
            if atualizador.empacotado() else
            "Versão de desenvolvimento: a atualização automática funciona no .exe",
            "ok_texto", "ok_claro")[0])
        c4.lay.addSpacing(12)
        c4.lay.addLayout(caixa("h", Botao("Procurar atualizações", "arrows-clockwise",
                                          comando=lambda: self.procurar_atualizacao(manual=True)),
                               Botao("Ver todas as versões", "arrow-square-out", "discreto",
                                     comando=lambda: webbrowser.open(atualizador.PAGINA)), None,
                               espaco=8))
        grade.addWidget(c4, 2, 0, 1, 2)
        lay.addLayout(grade)
        lay.addSpacing(10)
        lay.addLayout(caixa("h", Botao("Salvar como padrão", "floppy-disk", "primario",
                                       comando=self._salvar_padrao),
                            rotulo(f"Salvo em {PERFIL}", "mudo"), None, espaco=14))
        lay.addStretch(1)
        return pg

    def _salvar_padrao(self):
        self._salvar_perfil()
        self.toast.mostrar("Configurações salvas (sem as senhas)")

    def _escolher_log(self):
        f, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Arquivo de log",
                                                     self.v["log"].get() or "migracao.log",
                                                     "Log (*.log);;Todos (*.*)")
        if f:
            self.v["log"].set(os.path.normpath(f))

    def abrir_log(self):
        caminho = self.v["log"].get().strip() or LOG_PADRAO
        if os.path.exists(caminho):
            os.startfile(caminho)
        else:
            self.toast.mostrar("O log ainda não existe: ele é criado na primeira execução.", "info")

    # --------------------------------------------------- página: andamento
    def _pagina_progresso(self):
        pg = Superficie()
        lay = QtWidgets.QVBoxLayout(pg)
        lay.setContentsMargins(34, 28, 34, 26)
        lay.setSpacing(14)
        topo, self.lbl_rota = cabecalho(lay, "Andamento", "Nenhuma migração em andamento", mono=True,
                                        espaco=0)
        self.selo = Selo()
        self.btn_abrir_dir = Botao("Abrir pasta", "folder-open",
                                   comando=lambda: os.startfile(self.v["dir"].get()))
        self.btn_parar = Botao("Parar", "stop-circle", "perigo", comando=self.parar_execucao)
        topo.addWidget(self.selo, 0, Qt.AlignTop)
        topo.addWidget(self.btn_abrir_dir, 0, Qt.AlignTop)
        topo.addWidget(self.btn_parar, 0, Qt.AlignTop)
        self.btn_abrir_dir.hide()

        self.kpis = {"copiadas": Kpi("Copiadas", "envelope-simple", "destaque"),
                     "puladas": Kpi("Já existiam", "copy", "roxo"),
                     "erros": Kpi("Erros", "warning-circle", "erro"),
                     "bytes": Kpi("Transferido", "database", "ok")}
        linha = QtWidgets.QHBoxLayout()
        linha.setSpacing(16)
        for k in self.kpis.values():
            linha.addWidget(k)
        lay.addLayout(linha)

        meio = QtWidgets.QHBoxLayout()
        meio.setSpacing(16)
        self.zona = QtWidgets.QStackedWidget()
        self.progresso = PainelProgresso([("pasta", "destaque", "destaque2"), ("total", "ok", "ok2")])
        self.zona.addWidget(self.progresso)
        self.zona.addWidget(self._montar_banner())
        meio.addWidget(self.zona, 3)
        self.vel = GraficoVelocidade(me.fmt_bytes, me.fmt_tempo)
        meio.addWidget(self.vel, 2)
        self.zona.setFixedHeight(250)
        self.vel.setFixedHeight(250)
        lay.addLayout(meio)

        self.abas = Abas()
        self.tab_pastas = self.abas.adicionar("pastas", "Pastas", Tabela(
            COLS_PASTAS, vazio=("folders", "As pastas aparecem aqui assim que a migração começar")))
        self.tab_contas = self.abas.adicionar("contas", "Contas", Tabela(
            COLS_CONTAS, vazio=("users-three", "Nenhuma conta")))
        self.tab_problemas = self.abas.adicionar("problemas", "Problemas", Tabela(
            COLS_PROBLEMAS, vazio=("check-circle", "Nenhum problema até agora")))
        self.abas.adicionar("log", "Registro completo", self._montar_log())
        lay.addWidget(self.abas, 1)
        self.abas.visivel("contas", False)
        self._resetar_painel()
        self._estado("parado")
        return pg

    def _montar_log(self):
        w = QtWidgets.QWidget()
        lay = caixa("v", espaco=8)
        w.setLayout(lay)
        caixa_log = Cartao(padding=14)
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setObjectName("log")
        self.txt_log.setReadOnly(True)
        self.txt_log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txt_log.setMaximumBlockCount(LIMITE_LOG)
        caixa_log.lay.addWidget(self.txt_log)
        lay.addWidget(caixa_log, 1)
        self.lbl_log_cortado = rotulo("", "mudo")
        lay.addLayout(caixa("h", self.lbl_log_cortado, None,
                            Botao("Abrir arquivo de log", "arrow-square-out", "discreto",
                                  comando=self.abrir_log)))
        self.linhas_log = 0
        self._log_itens = []  # (nível, texto) do que está na tela, para repintar no outro tema
        return w

    def _montar_banner(self):
        self.banner = Cartao(padding=24, espaco=0)
        self.ban_icone = Ladrilho("check-circle-fill", "ok", "superficie", 56, redondo=True)
        self.ban_titulo = rotulo("", "secao")
        self.ban_texto = rotulo("", "rodape", quebra=True)
        self.ban_problemas = Botao("Ver problemas", "warning", comando=lambda: self.abas.mostrar("problemas"))
        self.ban_exportar = Botao("Exportar relatório", "export",
                                  comando=lambda: self.exportar_relatorio(self.rel))
        self.ban_nova = Botao("Nova migração", "plus-circle", comando=lambda: self.mostrar("config"))
        col = caixa("v", self.ban_titulo, 4, self.ban_texto, 16,
                    caixa("h", self.ban_problemas, self.ban_exportar, self.ban_nova, None, espaco=8),
                    espaco=0)
        self.banner.lay.addStretch(1)
        self.banner.lay.addLayout(caixa("h", self.ban_icone, col, espaco=20))
        self.banner.lay.itemAt(1).layout().setAlignment(self.ban_icone, Qt.AlignTop)
        self.banner.lay.addStretch(1)
        return self.banner

    def _mostrar_banner(self, estado, titulo, texto):
        icone_, fundo, borda, tinta = BANNER[estado]
        self.banner.recolorir(fundo, borda)
        self.ban_icone.definir(icone_, tinta, "superficie")
        self.ban_titulo.setText(titulo)
        self.ban_texto.setText(texto)
        problemas = len(self.rel["problemas"])
        self.ban_problemas.setVisible(bool(problemas))
        if problemas:
            self.ban_problemas.definir(f"Ver problemas ({num(problemas)})")
        self._banner_tinta = tinta
        self._cor_banner()
        self.zona.setCurrentIndex(1)

    def _cor_banner(self):
        if getattr(self, "_banner_tinta", None):
            self.ban_titulo.setStyleSheet(f"color: {tema.COR[self._banner_tinta]};")

    def _estado(self, estado):
        texto, tipo, icone_ = SELO[estado]
        self.selo.definir(texto, tipo, icone_)
        ativo = estado in ("rodando", "parando")
        situacao = self.lateral.situacao
        self.lateral.estado("Parando…" if estado == "parando" else "Migrando…" if ativo else "Pronto",
                            situacao[1] if ativo else "", ativo, situacao[3] if ativo else None)
        if not ativo:
            self.setWindowTitle(self.titulo_base)
        self.btn_parar.setVisible(ativo)
        self.btn_parar.setEnabled(estado == "rodando")
        self.progresso.em_andamento(estado == "rodando")
        self.vel.em_andamento(estado == "rodando")

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
        self.progresso.anel(0, "—", "aguardando", ("destaque", "destaque2"))
        for t in (self.tab_pastas, self.tab_contas, self.tab_problemas):
            t.limpar()
        self.abas.contador("problemas", "")
        self.txt_log.clear()
        self._log_itens = []
        self.linhas_log = 0
        self.lbl_log_cortado.setText("")
        self.zona.setCurrentIndex(0)
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
            rotulo_feitas, feitas = "Contas", f"{self.contas_feitas} de {self.contas_total}"
        else:
            rotulo_feitas = "Pastas"
            feitas = (f"{self.pastas_feitas} de {self.total_pastas}" if self.total_pastas
                      else num(self.pastas_feitas))
        if not self.inicio:
            for k in self.kpis.values():
                k.set(None)
            self.vel.definir_info([("timer", "Decorrido", "—"), ("hourglass", "Restante", "—"),
                                   ("folder", rotulo_feitas, "—")])
            return
        t = self._totais()
        self.kpis["copiadas"].set(t["copiadas"], num, "destaque_tinta")
        self.kpis["puladas"].set(t["puladas"], num)
        self.kpis["erros"].set(t["erros"], num, "erro_texto" if t["erros"] else None)
        self.kpis["bytes"].set(t["bytes"], me.fmt_bytes)
        eta = self._eta() if self.rodando else 0
        self.vel.definir_info([
            ("timer", "Decorrido", me.fmt_tempo(time.time() - self.inicio)),
            ("hourglass", "Restante", me.fmt_tempo(eta) if eta else "—" if self.rodando else "0s"),
            ("folder", rotulo_feitas, feitas)])

    def _atualizar_progresso(self):
        a = self.atual
        if a["inicio"] is not None:
            self.progresso.definir("pasta", direita=f"{num(a['feito'])} / {num(a['total'])}")
            self.progresso.alvo("pasta", a["feito"] / a["total"] if a["total"] else 1)
        pct = None
        if self.total_geral:
            feitas = min(self._processadas(), self.total_geral)
            pct = feitas / self.total_geral
            self.progresso.definir("total", "Total", f"{num(feitas)} de {num(self.total_geral)} mensagens")
        elif self.contas_total:
            pct = self.contas_feitas / self.contas_total
            self.progresso.definir("total", "Contas", f"{self.contas_feitas} de {self.contas_total}")
        else:
            self.progresso.definir("total", "Total", plural(self.pastas_feitas, "pasta concluída",
                                                            "pastas concluídas"))
        if pct is not None:
            self.progresso.barra_visivel("total", True)
            self.progresso.alvo("total", pct)
            self.progresso.anel(pct, f"{pct:.0%}", "concluído")
            if self.rodando:
                self.setWindowTitle(f"{pct:.0%} · {self.titulo_base}")
                self.lateral.estado(self.lateral.situacao[0], f"{pct:.0%} concluído", True, pct)
        else:
            self.progresso.barra_visivel("total", False)  # total desconhecido: sem barra que engane
            self.progresso.anel(None, num(self.pastas_feitas),
                                "pasta concluída" if self.pastas_feitas == 1 else "pastas concluídas",
                                indeterminado=self.rodando)
            if self.rodando:
                self.lateral.estado(self.lateral.situacao[0],
                                    plural(self.pastas_feitas, "pasta concluída", "pastas concluídas"),
                                    True, None)

    def _formato_log(self, nivel, negrito=False):
        f = QtGui.QTextCharFormat()
        f.setForeground(tema.cor(NIVEIS_LOG.get(nivel, "tinta")))
        if negrito or nivel in ("erro", "titulo", "secao"):
            f.setFontWeight(QtGui.QFont.DemiBold)
        return f

    def _escrever_logs(self, itens, guardar=True):
        """Escreve várias linhas de uma vez, num só bloco de edição."""
        t = self.txt_log
        barra = t.verticalScrollBar()
        acompanhar = barra.value() >= barra.maximum() - 4  # só rola se você já estava no fim
        cursor = QtGui.QTextCursor(t.document())
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.beginEditBlock()
        hora_fmt = self._formato_log("hora")
        for nivel, texto, hora in itens:
            texto = decodificar_utf7(texto)
            vazio = t.document().isEmpty() and cursor.position() == 0
            if nivel == "titulo":
                if not vazio:
                    cursor.insertBlock()
                cursor.insertText(texto, self._formato_log("titulo"))
                cursor.insertBlock()
                continue
            if nivel == "secao" and not vazio:
                cursor.insertBlock()
            if not (t.document().isEmpty() and cursor.position() == 0):
                cursor.insertBlock()
            simbolo = SIMBOLOS_TELA.get(nivel)
            cursor.insertText(hora + " ", hora_fmt)
            cursor.insertText(f"{'▸ ' if nivel == 'secao' else ''}{simbolo + ' ' if simbolo else ''}"
                              f"{texto}", self._formato_log(nivel))
        cursor.endEditBlock()
        if guardar:
            self._log_itens.extend(itens)
            if len(self._log_itens) > LIMITE_LOG:
                del self._log_itens[:len(self._log_itens) - LIMITE_LOG]
            self.linhas_log += len(itens)
            if self.linhas_log > LIMITE_LOG:
                self.lbl_log_cortado.setText(
                    f"Mostrando as últimas {num(LIMITE_LOG)} linhas. O log completo está em "
                    f"{self.rel['log'] if self.rel else LOG_PADRAO}")
        if acompanhar:
            barra.setValue(barra.maximum())

    def _repintar_log(self):
        if hasattr(self, "txt_log") and self._log_itens:
            self.txt_log.clear()
            self._escrever_logs(list(self._log_itens), guardar=False)

    # ------------------------------------------------------ página: histórico
    def _pagina_historico(self):
        pg = Superficie()
        lay = QtWidgets.QVBoxLayout(pg)
        lay.setContentsMargins(34, 28, 34, 26)
        lay.setSpacing(12)
        topo, _ = cabecalho(lay, "Histórico", "Toda execução fica registrada com o resultado de cada "
                                              "pasta e cada problema, mesmo depois de fechar o app.")
        topo.addWidget(Botao("Abrir pasta dos relatórios", "folder-open",
                             comando=self._abrir_pasta_historico), 0, Qt.AlignTop)
        self.tab_hist = Tabela(COLS_HIST, vazio=("clock-counter-clockwise",
                                                 "Nenhuma migração registrada ainda"))
        self.tab_hist.setFixedHeight(290)
        self.tab_hist.vista.selecionada.connect(self._mostrar_execucao)
        lay.addWidget(self.tab_hist)
        lay.addSpacing(8)
        self.hist_titulo = rotulo("Nenhuma migração registrada ainda.", "secao")
        self.hist_exportar = Botao("Exportar relatório (CSV)", "export",
                                   comando=lambda: self.exportar_relatorio(self.hist_sel))
        self.hist_exportar.setEnabled(False)
        self.hist_selo = Selo()
        self.hist_selo.hide()
        lay.addLayout(caixa("h", self.hist_titulo, self.hist_selo, None, self.hist_exportar, espaco=12))
        self.hist_resumo = rotulo("", "mudo", quebra=True)
        lay.addWidget(self.hist_resumo)
        self.hist_abas = Abas()
        self.hist_pastas = self.hist_abas.adicionar("pastas", "Pastas", Tabela(
            COLS_PASTAS, vazio=("folders", "Nenhuma pasta nesta execução")))
        self.hist_contas = self.hist_abas.adicionar("contas", "Contas", Tabela(
            COLS_CONTAS, vazio=("users-three", "Nenhuma conta")))
        self.hist_problemas = self.hist_abas.adicionar("problemas", "Problemas", Tabela(
            COLS_PROBLEMAS, vazio=("check-circle", "Nenhum problema nesta execução")))
        lay.addWidget(self.hist_abas, 1)
        self.historico, self.hist_sel = [], None
        return pg

    def _atualizar_historico(self):
        anterior = self.hist_sel["id"] if self.hist_sel else None
        self.historico = carregar_historico()
        self.tab_hist.limpar()
        for rel in self.historico:
            em_curso = self.rodando and self.rel and rel["id"] == self.rel["id"]
            t = rel["totais"]
            texto, icone_ = ("Em andamento", "andamento") if em_curso else \
                RESULTADO.get(rel["estado"], (rel["estado"], "pendente"))
            self.tab_hist.inserir(
                ((icone_, texto), data_hora(rel["inicio"]), rel["tipo"],
                 f"{rel['origem']} → {rel['destino']}",
                 num(t["copiadas"]), num(t["puladas"]), num(t["erros"]),
                 me.fmt_tempo(rel.get("duracao", 0))),
                ("ativa",) if em_curso else (), iid=rel["id"])
        if self.historico:
            ids = [r["id"] for r in self.historico]
            self.tab_hist.selecionar(anterior if anterior in ids else ids[0])
            self._mostrar_execucao(anterior if anterior in ids else ids[0])
        else:
            self._mostrar_execucao(None)

    def _mostrar_execucao(self, iid):
        rel = next((r for r in self.historico if r["id"] == iid), None)
        self.hist_sel = rel
        for t in (self.hist_pastas, self.hist_contas, self.hist_problemas):
            t.limpar()
        if not rel:
            self.hist_titulo.setText("Nenhuma migração registrada ainda.")
            self.hist_resumo.setText("")
            self.hist_selo.hide()
            self.hist_exportar.setEnabled(False)
            return
        lote = bool(rel["contas"])
        self.hist_titulo.setText(f"{rel['tipo']} · {data_hora(rel['inicio'])}")
        estado = rel["estado"] if rel["estado"] in SELO else "cancelado"
        if self.rodando and self.rel and rel["id"] == self.rel["id"]:
            estado = "rodando"
        self.hist_selo.definir(*SELO[estado])
        self.hist_selo.show()
        t = rel["totais"]
        self.hist_resumo.setText(
            f"{rel['origem']} → {rel['destino']}\n"
            f"{plural(t['copiadas'], 'copiada', 'copiadas')} · {num(t['puladas'])} já existiam · "
            f"{plural(t['erros'], 'erro', 'erros')} · {me.fmt_bytes(t['bytes'])} em "
            f"{me.fmt_tempo(rel.get('duracao', 0))}"
            + (f"\n{rel['resumo'].splitlines()[0]}" if rel.get("resumo") else ""))
        self.hist_pastas.colunas_visiveis([c[0] for c in COLS_PASTAS if lote or c[0] != "conta"])
        self.hist_problemas.colunas_visiveis([c[0] for c in COLS_PROBLEMAS if lote or c[0] != "conta"])
        for p in rel["pastas"]:
            self.hist_pastas.inserir(*linha_pasta(p))
        for c in rel["contas"]:
            self.hist_contas.inserir(*linha_conta(c))
        for pr in rel["problemas"]:
            self.hist_problemas.inserir(*linha_problema(pr))
        erros = sum(1 for pr in rel["problemas"] if pr["nivel"] == "erro")
        self.hist_abas.contador("problemas", num(len(rel["problemas"])) if rel["problemas"] else "",
                                "erro" if erros else "aviso")
        self.hist_abas.visivel("contas", lote)
        self.hist_exportar.setEnabled(True)

    def _abrir_pasta_historico(self):
        os.makedirs(HIST_DIR, exist_ok=True)
        os.startfile(HIST_DIR)

    def exportar_relatorio(self, rel):
        if not rel:
            return
        if rel is self.rel:
            self._sincronizar_relatorio()
        caminho, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salvar relatório",
            os.path.join(os.path.expanduser("~"), "Documents", f"relatorio_migracao_{rel['id']}.csv"),
            "CSV (abre no Excel) (*.csv)")
        if not caminho:
            return
        try:
            exportar_csv(rel, caminho)
        except OSError as e:
            avisar(self, "Relatório", f"Não foi possível salvar: {e}", "erro")
            return
        self.toast.mostrar(f"Relatório salvo: {os.path.basename(caminho)}",
                           acao=("Abrir", lambda: os.startfile(caminho)), segundos=7)

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
        card.botao.carregando(True)
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
            avisar(self, "Faltam dados", str(e))
            return
        self._salvar_perfil()
        self.parar.clear()
        self._resetar_painel()
        modo = self.v["modo"].get()
        lote = modo == "lote"
        self.rel = self._novo_relatorio(a, modo)
        self.lbl_rota.setText(self._rota(a, modo, len(self.contas_csv)))
        self.tab_pastas.colunas_visiveis([c for c in VISIVEIS_ANDAMENTO if lote or c != "conta"])
        self.tab_problemas.colunas_visiveis([c[0] for c in COLS_PROBLEMAS if lote or c[0] != "conta"])
        self.abas.visivel("contas", lote)
        self.abas.mostrar("contas" if lote else "pastas")
        if modo in ("imap2imap", "export") and self.pastas_origem:
            for nome, (sel, n, _) in self.pastas_origem.items():
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
        self.btn_iniciar.setEnabled(False)
        self.btn_abrir_dir.setVisible(a.modo in ("export", "import"))
        self.progresso.definir("pasta", "Conectando…", "")
        self.progresso.indeterminado("pasta")
        self._atualizar_progresso()
        self._kpis()
        self.mostrar("progresso")
        threading.Thread(target=self._trabalhar, args=(a,), daemon=True).start()
        self.t_relogio.start(1000)

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
        if self.rodando and perguntar(
                self, "Parar migração", "Parar agora? O que já foi copiado fica no destino e, ao "
                "rodar de novo, a migração continua de onde parou.", "Parar", "Continuar",
                perigo=True):
            self.parar.set()
            self._estado("parando")

    def _relogio(self):
        if not self.rodando:
            self.t_relogio.stop()
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
                    logs.append((ev[1], ev[2], time.strftime("%H:%M:%S")))
                    self._registrar_problema(ev[1], ev[2])
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

    def _contador_problemas(self):
        probs = self.rel["problemas"]
        erros = any(pr["nivel"] == "erro" for pr in probs)
        self.abas.contador("problemas", num(len(probs)) if probs else "", "erro" if erros else "aviso")

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
        self._contador_problemas()
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
            self.tab_pastas.ver(iid)
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
            self.lbl_rota.setText(f"conta {n} de {total} · {src} → {dst}")
            if n <= len(self.rel["contas"]):
                self.rel["contas"][n - 1]["status"] = "andamento"
                self._atualizar_conta(n)
                self.tab_contas.ver(f"c{n}")
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
            card.botao.carregando(False)
            card.mostrar_status(msg, "ok" if ok else "erro")
        elif tipo == "pastas":
            self._mostrar_pastas(ev[1], ev[2])
        elif tipo == "previa_dir":
            if ev[1] == self.v["dir"].get().strip():
                self.dir_previa.setText(ev[2])
        elif tipo == "atualizacao":
            self._resultado_busca(*ev[1:])
        elif tipo == "download":
            if self.dialogo is not None and self.dialogo.isVisible():
                self.dialogo.progresso(ev[1], ev[2])
        elif tipo == "download_fim":
            self._fim_download(ev[1], ev[2])

    # --------------------------------------------------------- atualização
    def procurar_atualizacao(self, manual=False):
        if not atualizador.empacotado():
            if manual:
                avisar(self, "Atualizações", "Rodando pelo Python não há atualização automática: "
                       "ela funciona no MigradorEmails.exe.", "info")
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
            self.lateral.definir_aviso(f"Nova versão {info['versao']}", "clique para atualizar")
            if manual or (nova and not self.rodando):  # durante migração, só o aviso discreto
                self.abrir_atualizacao()
        elif manual and erro:
            avisar(self, "Atualizações", f"Não foi possível verificar agora:\n{erro}")
        elif manual:
            self.toast.mostrar(f"Você já está na versão mais recente ({VERSAO}).")

    def abrir_atualizacao(self):
        if not (self.atualizacao or self.atualizacao_pronta):
            return
        if self.dialogo is None:
            self.dialogo = DialogoAtualizacao(self)
        self.dialogo.atualizar()
        self.dialogo.show()
        self.dialogo.raise_()
        self.dialogo.activateWindow()

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
            self.lateral.definir_aviso("Atualização pronta", "clique para instalar", "check-circle-fill")
        if self.dialogo is not None and self.dialogo.isVisible():
            self.dialogo.atualizar(erro)
        elif erro:
            avisar(self, "Atualização", erro)

    def reiniciar_para_atualizar(self):
        self._reiniciar = True
        self.close()

    def _fim(self, estado, resumo):
        self._fechar_pasta_parcial()
        self.rodando = False
        self.t_relogio.stop()
        for c in self.rel["contas"]:
            if c["status"] == "andamento":
                c["status"] = "parada"
                self._atualizar_conta(c["n"])
        self.rel.update(estado=estado, resumo=resumo, fim=time.time())
        self._salvar_relatorio()
        self._kpis()
        self._atualizar_progresso()
        self._estado(estado)
        self.btn_iniciar.setEnabled(True)

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
        QtWidgets.QApplication.beep()
        QtWidgets.QApplication.alert(self)  # pisca na barra de tarefas se estiver em segundo plano

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

    def closeEvent(self, e):
        reiniciar, self._reiniciar = self._reiniciar, False
        if self.rodando:
            if not perguntar(self, "Sair", "Uma migração está em andamento. Parar e sair?\n"
                             "Ao abrir de novo, ela continua de onde parou.", "Parar e sair",
                             "Continuar", perigo=True):
                e.ignore()
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
            except OSError as erro:
                atualizador.registrar(f"falha ao iniciar a instalação: {erro!r}")
        e.accept()
        QtWidgets.QApplication.quit()


def iniciar_qt():
    """Cria o QApplication com o estilo e as fontes do app."""
    if os.name == "nt":
        try:  # agrupa as janelas sob o ícone do app na barra de tarefas
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MigradorEmails")
        except (AttributeError, OSError):
            pass
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName(TITULO)
    app.setStyle("Fusion")
    tradutor = QtCore.QTranslator(app)  # menus prontos do Qt (Copiar, Colar...) em português
    for pasta in (QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.TranslationsPath),
                  os.path.join(os.path.dirname(QtCore.__file__), "translations")):
        if tradutor.load("qtbase_pt_BR", pasta):
            app.installTranslator(tradutor)
            break
    tema.FONTES.carregar()
    app.setFont(tema.fonte(10))
    return app


def main():
    if atualizador.concluir(sys.argv):  # executável novo terminando a própria instalação
        return
    app = iniciar_qt()
    janela = App()
    if "--verificar" in sys.argv:  # usado no build: o .exe monta a interface e fecha (código 0)
        janela.show()
        app.processEvents()
        janela.hide()
        return
    janela.show()
    tema.barra_titulo(janela)
    app.exec()


if __name__ == "__main__":
    main()
