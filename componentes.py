"""
Componentes visuais do Migrador (PySide6), quase todos desenhados com QPainter:
cantos arredondados com antisserrilhado, sombras suaves e animações.

Todas as animações passam por um único relógio (RELOGIO) que roda a ~60 quadros/s
só enquanto alguma coisa está se movendo, e para sozinho depois.
"""
import math
import time

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPointF, QRectF, Qt

from tema import COR, TEMA, cor, fonte, icone, misturar, pintar_icone

ESQ, DIR, CENTRO = Qt.AlignLeft | Qt.AlignVCenter, Qt.AlignRight | Qt.AlignVCenter, Qt.AlignCenter


# --------------------------------------------------------------- animação
class _Relogio(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._passo)
        self.itens = set()
        self._ultimo = 0.0

    def acordar(self, obj):
        self.itens.add(obj)
        if not self.timer.isActive():
            self._ultimo = time.perf_counter()
            self.timer.start()

    def _passo(self):
        agora = time.perf_counter()
        dt, self._ultimo = min(0.1, agora - self._ultimo), agora
        for obj in list(self.itens):
            try:
                continua = obj.passo(dt)
            except RuntimeError:  # o widget já foi destruído
                continua = False
            if not continua:
                self.itens.discard(obj)
        if not self.itens:
            self.timer.stop()


_RELOGIO = None


def relogio():
    global _RELOGIO
    if _RELOGIO is None:
        _RELOGIO = _Relogio()
    return _RELOGIO


class Suave:
    """Valor que desliza até o alvo; a aproximação exponencial dá o mesmo movimento
    em qualquer taxa de quadros (e desacelera naturalmente ao chegar)."""

    def __init__(self, valor=0.0, velocidade=8.0, precisao=0.0005):
        self.v = self.alvo = float(valor)
        self.vel, self.precisao = velocidade, precisao

    def ir(self, alvo, instantaneo=False):
        self.alvo = float(alvo)
        if instantaneo:
            self.v = self.alvo

    def passo(self, dt):
        dif = self.alvo - self.v
        if abs(dif) <= self.precisao * max(1.0, abs(self.alvo)):
            self.v = self.alvo
            return False
        self.v += dif * (1 - math.exp(-dt * self.vel))
        return True


QUADRO_AMBIENTE = 1 / 30  # pulsos, brilhos e arcos girando não precisam de 60 quadros/s


def ambiente(obj, dt, quadro=QUADRO_AMBIENTE):
    """True quando é hora de mais um quadro de animação "ambiente" (a ~30/s)."""
    obj._acum = getattr(obj, "_acum", 0.0) + dt
    if obj._acum >= quadro:
        obj._acum = 0.0
        return True
    return False


class Animado:
    """Mistura para widgets: passo() avança os Suave e pede repaint."""
    continuo = False  # True = segue animando (brilho, pulso) mesmo parado
    quadro = 0.0  # intervalo mínimo entre repaints (0 = todo quadro, ~60/s)

    def suaves(self):
        return ()

    def animar(self):
        relogio().acordar(self)

    def regiao_continua(self):
        """Área que a animação contínua muda (None = o widget todo)."""
        return None

    def passo(self, dt):
        movendo = any([s.passo(dt) for s in self.suaves()])  # todos avançam (sem curto-circuito)
        if movendo:
            if not self.quadro or ambiente(self, dt, self.quadro):
                self.update()
        elif ambiente(self, dt):
            regiao = self.regiao_continua()
            if regiao is None:
                self.update()
            elif not regiao.isEmpty():
                self.update(regiao)
        return movendo or self.continuo

    def showEvent(self, e):
        super().showEvent(e)
        self.animar()  # retoma o que parou enquanto estava escondido


def caminho_arredondado(r, raio):
    p = QtGui.QPainterPath()
    p.addRoundedRect(r, raio, raio)
    return p


def pintor(widget):
    p = QtGui.QPainter(widget)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    return p


def texto(p, r, s, f, c, alinhar=ESQ, elidir=True):
    p.setFont(f)
    p.setPen(c if isinstance(c, QtGui.QColor) else cor(c))
    if elidir:
        s = QtGui.QFontMetricsF(f).elidedText(s, Qt.ElideRight, r.width())
    p.drawText(r, alinhar, s)


def largura_texto(s, f):
    return QtGui.QFontMetricsF(f).horizontalAdvance(s)


def desenhar_spinner(p, centro, raio, c, espessura=2.0):
    """Arco girando (o ângulo vem do relógio, então gira enquanto houver repaint)."""
    t = time.perf_counter()
    caneta = QtGui.QPen(c if isinstance(c, QtGui.QColor) else cor(c), espessura)
    caneta.setCapStyle(Qt.RoundCap)
    p.setPen(caneta)
    p.setBrush(Qt.NoBrush)
    inicio = -(t * 360 * 1.1) % 360
    tamanho = 70 + 50 * (1 + math.sin(t * 3.2)) / 2
    r = QRectF(centro.x() - raio, centro.y() - raio, 2 * raio, 2 * raio)
    p.drawArc(r, int(inicio * 16), int(tamanho * 16))


_sombras = {}
MARGEM_SOMBRA = 20


def _camadas_sombra(p, r, raio, elevacao):
    forca = float(COR["sombra_forca"])
    camadas = 7 + int(5 * elevacao)
    for i in range(camadas, 0, -1):
        alfa = 0.016 * forca * (1 - i / (camadas + 1)) * (1 + 0.8 * elevacao)
        c = cor("sombra", min(1.0, alfa))
        rr = r.adjusted(-i * 0.8, -i * 0.45 + 1.5 + 2 * elevacao,
                        i * 0.8, i * 1.05 + 1.5 + 3 * elevacao)
        p.fillPath(caminho_arredondado(rr, raio + i * 0.7), c)


def desenhar_sombra(p, r, raio, elevacao=0.0):
    """Sombra difusa: camadas cada vez maiores e mais transparentes. Fica guardada como
    imagem por tamanho (desenhar as camadas a cada quadro sairia caro)."""
    elevacao = round(elevacao * 20) / 20
    dpr = p.device().devicePixelRatioF()
    k = (round(r.width()), round(r.height()), raio, elevacao, dpr, COR["sombra"], COR["sombra_forca"])
    pm = _sombras.get(k)
    if pm is None:
        if len(_sombras) > 200:
            _sombras.clear()
        m = MARGEM_SOMBRA
        pm = QtGui.QPixmap(int((k[0] + 2 * m) * dpr), int((k[1] + 2 * m) * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        q = QtGui.QPainter(pm)
        q.setRenderHint(QtGui.QPainter.Antialiasing)
        _camadas_sombra(q, QRectF(m, m, k[0], k[1]), raio, elevacao)
        q.end()
        _sombras[k] = pm
    p.drawPixmap(QPointF(r.x() - MARGEM_SOMBRA, r.y() - MARGEM_SOMBRA), pm)


# --------------------------------------------------------------- estado
class Var(QtCore.QObject):
    """Valor de texto observável (o equivalente do StringVar do Tk)."""
    mudou = QtCore.Signal(str)

    def __init__(self, valor=""):
        super().__init__()
        self._v = str(valor)

    def get(self):
        return self._v

    def set(self, v):
        v = str(v)
        if v != self._v:
            self._v = v
            self.mudou.emit(v)

    def observar(self, funcao):
        self.mudou.connect(lambda _: funcao())


# --------------------------------------------------------------- superfícies
class Superficie(QtWidgets.QWidget):
    """Fundo de página. Desenha as sombras dos cartões que estão sobre ele: assim a
    sombra pode passar da borda do cartão sem roubar espaço do layout."""

    def __init__(self, pai=None):
        super().__init__(pai)
        self._sombras = []

    def paintEvent(self, e):
        p = pintor(self)
        p.fillRect(e.rect(), cor("fundo"))
        area = QRectF(e.rect()).adjusted(-24, -24, 24, 24)
        vivos = []
        for c in self._sombras:
            try:
                if not c.isVisibleTo(self) or not c.isVisible():
                    vivos.append(c)
                    continue
                r = QRectF(QPointF(c.mapTo(self, QtCore.QPoint(0, 0))), QtCore.QSizeF(c.size()))
            except RuntimeError:
                continue
            vivos.append(c)
            if c.sombra and area.intersects(r):
                desenhar_sombra(p, r, c.raio, c.elevacao())
        self._sombras = vivos


class Cartao(QtWidgets.QWidget):
    """Cartão branco de cantos arredondados (com sombra desenhada pela Superficie)."""

    def __init__(self, pai=None, padding=18, raio=16, fundo="superficie", borda="borda",
                 sombra=True, espaco=10):
        super().__init__(pai)
        self.raio, self.fundo, self.cor_borda, self.sombra = raio, fundo, borda, sombra
        self._sup = None
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(espaco)

    def elevacao(self):
        return 0.0

    def recolorir(self, fundo, borda):
        self.fundo, self.cor_borda = fundo, borda
        self.update()

    def _registrar(self):
        w = self.parentWidget()
        while w is not None and not isinstance(w, Superficie):
            w = w.parentWidget()
        if w is not self._sup:
            self._sup = w
            if w is not None and self not in w._sombras:
                w._sombras.append(self)

    def _avisar_superficie(self):
        if self._sup is not None:
            try:
                r = QtCore.QRect(self.mapTo(self._sup, QtCore.QPoint(0, 0)), self.size())
                self._sup.update(r.adjusted(-24, -24, 24, 24))
            except RuntimeError:
                self._sup = None

    def showEvent(self, e):
        self._registrar()
        self._avisar_superficie()
        super().showEvent(e)

    def hideEvent(self, e):
        self._avisar_superficie()
        super().hideEvent(e)

    def moveEvent(self, e):
        if self._sup is not None:
            self._sup.update()
        super().moveEvent(e)

    def resizeEvent(self, e):
        self._avisar_superficie()
        super().resizeEvent(e)

    def pintar_fundo(self, p):
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        caminho = caminho_arredondado(r, self.raio)
        p.fillPath(caminho, cor(self.fundo) if isinstance(self.fundo, str) else self.fundo)
        if self.cor_borda:
            p.setPen(QtGui.QPen(cor(self.cor_borda) if isinstance(self.cor_borda, str)
                                else self.cor_borda, 1))
            p.drawPath(caminho)

    def paintEvent(self, e):
        p = pintor(self)
        self.pintar_fundo(p)


# --------------------------------------------------------------- botões
ESTILOS_BOTAO = {  # fundo, hover, pressionado, texto, borda
    "primario": ("destaque", "destaque_hover", "destaque_press", "#ffffff", None),
    "secundario": ("superficie", "superficie2", "trilho", "tinta", "borda_forte"),
    "perigo": ("superficie", "erro_claro", "erro_claro", "erro_texto", "erro"),
    "discreto": (None, "trilho", "borda_forte", "tinta2", None),
    "fantasma": (None, "destaque_claro", "destaque_claro", "destaque_tinta", None),
}


class Botao(Animado, QtWidgets.QAbstractButton):
    """Botão arredondado com transição suave de cor, leve "afundar" ao clicar,
    contorno de foco para teclado e estado carregando (arco girando)."""

    def __init__(self, texto_="", icone_=None, estilo="secundario", grande=False,
                 comando=None, pai=None):
        super().__init__(pai)
        self.setText(texto_)
        self.nome_icone, self.estilo, self.grande = icone_, estilo, grande
        self.h = Suave(0, 14)
        self.pr = Suave(0, 22)
        self._foco_teclado = False
        self._carregando = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        if comando:
            self.clicked.connect(lambda: comando())
        TEMA.mudou.connect(self.update)

    def suaves(self):
        return (self.h, self.pr)

    @property
    def continuo(self):
        return self._carregando

    def _fonte(self):
        return fonte(11 if self.grande else 9.5 if self.estilo == "discreto" else 10,
                     600 if self.estilo == "primario" or self.grande else 500)

    def sizeHint(self):
        alt = 46 if self.grande else 32 if self.estilo == "discreto" else 38
        larg = largura_texto(self.text(), self._fonte()) + (44 if self.grande else 30)
        if self.nome_icone or self._carregando:
            larg += 24 if self.text() else 10
        if not self.text():
            larg = alt
        return QtCore.QSize(int(larg), alt)

    def definir(self, texto_=None, icone_=None, comando=None):
        if texto_ is not None:
            self.setText(texto_)
        if icone_ is not None:
            self.nome_icone = icone_ or None
        if comando is not None:
            try:
                self.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.clicked.connect(lambda: comando())
        self.updateGeometry()
        self.update()

    def carregando(self, sim):
        self._carregando = sim
        self.setEnabled(not sim)
        self.updateGeometry()
        self.animar()

    def enterEvent(self, e):
        self.h.ir(1)
        self.animar()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.h.ir(0)
        self.animar()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self.pr.ir(1)
        self.animar()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.pr.ir(0)
        self.animar()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.animateClick()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        self._foco_teclado = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._foco_teclado = False
        self.update()
        super().focusOutEvent(e)

    def changeEvent(self, e):
        if e.type() == QtCore.QEvent.EnabledChange:
            self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)
            if not self.isEnabled():
                self.h.ir(0, True)
        super().changeEvent(e)

    def paintEvent(self, e):
        p = pintor(self)
        fundo, hover, press, tinta, borda = ESTILOS_BOTAO[self.estilo]
        transp = QtGui.QColor(0, 0, 0, 0)
        base = cor(fundo) if fundo else QtGui.QColor(cor(hover)).lighter(100)
        if not fundo:
            base.setAlphaF(0)
        c = misturar(base, hover, self.h.v)
        c = misturar(c, press, self.pr.v * 0.8)
        habilitado = self.isEnabled() or self._carregando
        if not habilitado:
            c = cor("trilho") if self.estilo == "primario" else (base if fundo else transp)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        escala = 1 - 0.03 * self.pr.v
        p.translate(r.center())
        p.scale(escala, escala)
        p.translate(-r.center())
        raio = 12 if self.grande else 10
        caminho = caminho_arredondado(r, raio)
        if self.estilo == "primario" and habilitado:  # leve brilho de cima para baixo
            g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0, c.lighter(108))
            g.setColorAt(1, c)
            p.fillPath(caminho, g)
        else:
            p.fillPath(caminho, c)
        if borda:
            p.setPen(QtGui.QPen(misturar(borda, "destaque", .35 * self.h.v) if habilitado
                                else cor("borda"), 1))
            p.drawPath(caminho)
        if self._foco_teclado:
            p.setPen(QtGui.QPen(cor("destaque", .55), 2))
            p.drawPath(caminho_arredondado(r.adjusted(-0.5, -0.5, 0.5, 0.5), raio + 1))
        cor_tinta = cor(tinta) if habilitado else cor("muda")
        f = self._fonte()
        lt = largura_texto(self.text(), f)
        tem_icone = bool(self.nome_icone) or self._carregando
        total = lt + (18 + (8 if self.text() else 0) if tem_icone else 0)
        x = r.center().x() - total / 2
        if tem_icone:
            ri = QRectF(x, r.center().y() - 9, 18, 18)
            if self._carregando:
                desenhar_spinner(p, ri.center(), 7, cor_tinta, 2)
            else:
                pintar_icone(p, self.nome_icone, cor_tinta, ri)
            x += 18 + (8 if self.text() else 0)
        texto(p, QRectF(x, r.y(), lt + 2, r.height()), self.text(), f, cor_tinta, elidir=False)


# --------------------------------------------------------------- campos
def ligar(campo, var):
    """Liga um QLineEdit a uma Var, nos dois sentidos."""
    campo.setText(var.get())
    campo.textChanged.connect(var.set)
    var.mudou.connect(lambda v: campo.setText(v) if campo.text() != v else None)


class Campo(QtWidgets.QLineEdit):
    def __init__(self, var=None, senha=False, dica="", pai=None):
        super().__init__(pai)
        self.setPlaceholderText(dica)
        self._senha = senha
        if var is not None:
            ligar(self, var)
        if senha:
            self.setEchoMode(QtWidgets.QLineEdit.Password)
            self.olho = self.addAction(icone("eye", "muda"), QtWidgets.QLineEdit.TrailingPosition)
            self.olho.setToolTip("Mostrar a senha")
            self.olho.triggered.connect(self._alternar)
            TEMA.mudou.connect(self._icone_olho)

    def _alternar(self):
        oculta = self.echoMode() == QtWidgets.QLineEdit.Password
        self.setEchoMode(QtWidgets.QLineEdit.Normal if oculta else QtWidgets.QLineEdit.Password)
        self.olho.setToolTip("Ocultar a senha" if oculta else "Mostrar a senha")
        self._icone_olho()

    def _icone_olho(self):
        visivel = self.echoMode() == QtWidgets.QLineEdit.Normal
        self.olho.setIcon(icone("eye-slash" if visivel else "eye", "muda"))


class Combo(QtWidgets.QComboBox):
    """Combobox editável com seta desenhada e lista de cantos arredondados."""

    def __init__(self, var, valores, pai=None):
        super().__init__(pai)
        self.setEditable(True)
        self.addItems(valores)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.setMaxVisibleItems(10)
        ligar(self.lineEdit(), var)
        lista = self.view().window()
        lista.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        lista.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = pintor(self)
        r = QRectF(self.width() - 28, (self.height() - 14) / 2, 14, 14)
        pintar_icone(p, "caret-down", "muda", r)


# --------------------------------------------------------------- selos
TIPOS_SELO = {  # tipo -> (fundo, tinta, ponto)
    "neutro": ("trilho", "tinta2", "muda"), "info": ("destaque_claro", "destaque_tinta", "destaque"),
    "ok": ("ok_claro", "ok_texto", "ok"), "aviso": ("aviso_claro", "aviso_texto", "aviso"),
    "erro": ("erro_claro", "erro_texto", "erro"),
}


class Selo(Animado, QtWidgets.QWidget):
    """Pílula de status; no tipo "info" (em andamento) o ponto pulsa."""
    quadro = QUADRO_AMBIENTE  # contar/deslizar a ~30 quadros/s já fica suave aqui

    def __init__(self, pai=None):
        super().__init__(pai)
        self.txt, self.tipo, self.nome_icone = "", "neutro", None
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    @property
    def continuo(self):
        return self.tipo == "info" and self.isVisible()

    def definir(self, texto_, tipo="neutro", icone_=None):
        self.txt, self.tipo, self.nome_icone = texto_, tipo, icone_
        self.updateGeometry()
        self.update()
        if self.tipo == "info":
            self.animar()

    def sizeHint(self):
        return QtCore.QSize(int(largura_texto(self.txt, fonte(9.5, 600)) + 46), 32)

    def paintEvent(self, e):
        p = pintor(self)
        fundo, tinta, ponto = TIPOS_SELO[self.tipo]
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(caminho_arredondado(r, r.height() / 2), cor(fundo))
        centro = QPointF(r.x() + 17, r.center().y())
        if self.nome_icone:
            pintar_icone(p, self.nome_icone, ponto, QRectF(centro.x() - 7, centro.y() - 7, 14, 14))
        else:
            if self.tipo == "info":  # halo pulsando
                fase = (time.perf_counter() * 0.9) % 1
                p.setPen(Qt.NoPen)
                p.setBrush(cor(ponto, 0.35 * (1 - fase)))
                p.drawEllipse(centro, 4 + 6 * fase, 4 + 6 * fase)
            p.setPen(Qt.NoPen)
            p.setBrush(cor(ponto))
            p.drawEllipse(centro, 4, 4)
        texto(p, QRectF(r.x() + 29, r.y(), r.width() - 36, r.height()), self.txt,
              fonte(9.5, 600), tinta)


# --------------------------------------------------------------- abas
class Segmentado(Animado, QtWidgets.QWidget):
    """Controle segmentado: a pílula branca desliza até a aba escolhida."""
    escolhido = QtCore.Signal(str)

    def __init__(self, pai=None):
        super().__init__(pai)
        self.itens = {}  # chave -> [texto, contador, tipo_contador, visível]
        self.atual = self.hover = None
        self.x, self.w = Suave(0, 16, 0.002), Suave(0, 16, 0.002)
        self.zonas = []
        self.setMouseTracking(True)
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)

    def suaves(self):
        return (self.x, self.w)

    def _fonte(self, sel=False):
        return fonte(9.5, 600 if sel else 500)

    def _medir(self):
        zonas, x = [], 4.0
        for chave, (txt, cont, _, vis) in self.itens.items():
            if not vis:
                continue
            w = largura_texto(txt, self._fonte(True)) + 30
            if cont:
                w += largura_texto(cont, fonte(8, 700)) + 18
            zonas.append((x, w, chave))
            x += w + 2
        self.zonas = zonas
        return x + 2

    def sizeHint(self):
        return QtCore.QSize(int(self._medir()), 38)

    def _reposicionar(self, instantaneo=False):
        self._medir()
        for x, w, chave in self.zonas:
            if chave == self.atual:
                self.x.ir(x, instantaneo or self.w.v == 0)
                self.w.ir(w, instantaneo or self.w.v == 0)
        self.updateGeometry()
        self.animar()

    def adicionar(self, chave, txt):
        self.itens[chave] = [txt, "", "neutro", True]
        if self.atual is None:
            self.atual = chave
        self._reposicionar(True)

    def selecionar(self, chave):
        self.atual = chave
        self._reposicionar()

    def definir(self, chave, txt=None, contador=None, tipo=None, visivel=None):
        it = self.itens[chave]
        for i, v in ((0, txt), (1, contador), (2, tipo), (3, visivel)):
            if v is not None:
                it[i] = v
        self._reposicionar()

    def _em(self, x):
        return next((k for a, w, k in self.zonas if a <= x <= a + w), None)

    def mouseMoveEvent(self, e):
        h = self._em(e.position().x())
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        chave = self._em(e.position().x())
        if chave and chave != self.atual:
            self.escolhido.emit(chave)

    def resizeEvent(self, e):
        self._reposicionar(True)

    def paintEvent(self, e):
        p = pintor(self)
        larg = self._medir()
        trilho = QRectF(0.5, 0.5, larg, self.height() - 1)
        p.fillPath(caminho_arredondado(trilho, trilho.height() / 2), cor("trilho"))
        sel = QRectF(self.x.v, 4, self.w.v, self.height() - 8)
        if self.w.v > 0:
            desenhar_sombra(p, sel, sel.height() / 2, -0.4)
            p.fillPath(caminho_arredondado(sel, sel.height() / 2), cor("superficie"))
        for x, w, chave in self.zonas:
            txt, cont, tipo, _ = self.itens[chave]
            ativo = chave == self.atual
            tinta = "tinta" if ativo or chave == self.hover else "tinta2"
            f = self._fonte(ativo)
            lt = largura_texto(txt, f)
            lc = largura_texto(cont, fonte(8, 700)) + 12 if cont else 0
            x0 = x + (w - lt - (lc + 6 if cont else 0)) / 2
            texto(p, QRectF(x0, 0, lt + 1, self.height()), txt, f, tinta, elidir=False)
            if cont:
                fundo, tinta_c, ponto = TIPOS_SELO[tipo]
                rc = QRectF(x0 + lt + 6, self.height() / 2 - 9, lc, 18)
                p.fillPath(caminho_arredondado(rc, 9), cor(ponto if tipo != "neutro" else "borda_forte"))
                texto(p, rc, cont, fonte(8, 700), "#ffffff" if tipo != "neutro" else "tinta2",
                      CENTRO, elidir=False)


class Abas(QtWidgets.QWidget):
    """Segmentado + páginas; só a aba visível fica montada."""

    def __init__(self, pai=None):
        super().__init__(pai)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.cabecalho = QtWidgets.QHBoxLayout()
        self.cabecalho.setSpacing(8)
        self.seg = Segmentado()
        self.cabecalho.addWidget(self.seg)
        self.cabecalho.addStretch(1)
        lay.addLayout(self.cabecalho)
        self.pilha = QtWidgets.QStackedWidget()
        lay.addWidget(self.pilha, 1)
        self.paginas = {}
        self.seg.escolhido.connect(self.mostrar)

    def adicionar(self, chave, txt, widget):
        self.seg.adicionar(chave, txt)
        self.paginas[chave] = widget
        self.pilha.addWidget(widget)
        return widget

    def mostrar(self, chave):
        self.seg.selecionar(chave)
        self.pilha.setCurrentWidget(self.paginas[chave])

    @property
    def atual(self):
        return self.seg.atual

    def contador(self, chave, n, tipo="neutro"):
        self.seg.definir(chave, contador=n, tipo=tipo)

    def visivel(self, chave, sim):
        self.seg.definir(chave, visivel=sim)
        if not sim and self.atual == chave:
            self.mostrar(next(k for k, it in self.seg.itens.items() if it[3]))


# --------------------------------------------------------------- tabela
# visual de cada situação: ícone Phosphor, cor do ícone/texto, fundo da pílula
VISUAL_SITUACAO = {
    "ok": ("check-circle-fill", "ok_texto", "ok_claro"),
    "aviso": ("warning-fill", "aviso_texto", "aviso_claro"),
    "erro": ("x-circle-fill", "erro_texto", "erro_claro"),
    "falha": ("x-circle-fill", "erro_texto", "erro_claro"),
    "andamento": (None, "destaque_tinta", "destaque_claro"),
    "pendente": ("circle", "muda", "trilho"),
    "parada": ("pause-circle-fill", "aviso_texto", "aviso_claro"),
}


class Coluna:
    def __init__(self, cid, titulo, largura, alinhar="w", elastica=False, tipo="texto"):
        self.id, self.titulo, self.largura = cid, titulo, largura
        self.alinhar, self.elastica, self.tipo = alinhar, elastica, tipo


class Modelo(QtCore.QAbstractTableModel):
    def __init__(self, colunas):
        super().__init__()
        self.colunas = colunas
        self.linhas = []  # [iid, valores, tags]
        self.indice = {}
        self.ativas = set()  # linhas "em andamento" (o arco da situação gira)

    def rowCount(self, _=QtCore.QModelIndex()):
        return len(self.linhas)

    def columnCount(self, _=QtCore.QModelIndex()):
        return len(self.colunas)

    def data(self, idx, papel=Qt.DisplayRole):
        if not idx.isValid():
            return None
        v = self.linhas[idx.row()][1][idx.column()]
        if papel == Qt.UserRole:
            return v
        if papel in (Qt.DisplayRole, Qt.ToolTipRole):
            if isinstance(v, tuple):
                v = v[-1]
            if papel == Qt.ToolTipRole and self.colunas[idx.column()].tipo not in ("texto", "check"):
                return None
            return None if v is None else str(v)
        return None

    def headerData(self, secao, orientacao, papel=Qt.DisplayRole):
        if orientacao != Qt.Horizontal:
            return None
        c = self.colunas[secao]
        if papel == Qt.DisplayRole:
            return c.titulo
        if papel == Qt.TextAlignmentRole:
            return int(DIR if c.alinhar == "e" else ESQ)
        return None

    def inserir(self, iid, valores, tags):
        n = len(self.linhas)
        self.beginInsertRows(QtCore.QModelIndex(), n, n)
        self.linhas.append([iid, list(valores), set(tags)])
        self.indice[iid] = n
        self._marcar(iid, tags)
        self.endInsertRows()

    def _marcar(self, iid, tags):
        if "ativa" in tags:
            self.ativas.add(iid)
        else:
            self.ativas.discard(iid)

    def atualizar(self, iid, valores=None, tags=None, coluna=None, valor=None):
        i = self.indice[iid]
        if valores is not None:
            self.linhas[i][1] = list(valores)
        if coluna is not None:
            self.linhas[i][1][coluna] = valor
        if tags is not None:
            self.linhas[i][2] = set(tags)
            self._marcar(iid, tags)
        self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.colunas) - 1))

    def limpar(self):
        self.beginResetModel()
        self.linhas, self.indice, self.ativas = [], {}, set()
        self.endResetModel()


class Delegado(QtWidgets.QStyledItemDelegate):
    def __init__(self, vista):
        super().__init__(vista)
        self.vista = vista

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        modelo, vista = self.vista.model(), self.vista
        linha = modelo.linhas[idx.row()]
        tags = linha[2]
        col = modelo.colunas[idx.column()]
        valor = linha[1][idx.column()]
        r = QRectF(opt.rect)
        visiveis = vista.colunas_exibidas()
        primeira = visiveis and idx.column() == visiveis[0]
        ultima = visiveis and idx.column() == visiveis[-1]

        # fundo da linha: hover, selecionada ou em andamento (pílula sobre a linha toda)
        sel = vista.selecionavel and vista.selectionModel().isRowSelected(idx.row(), QtCore.QModelIndex())
        fundo = None
        if "ativa" in tags or sel:
            fundo = cor("destaque_claro")
        elif idx.row() == vista.linha_hover:
            fundo = cor("hover_linha")
        if fundo is not None:
            esq = 4 if primeira else 0
            dir_ = -4 if ultima else 0
            rr = r.adjusted(esq, 3, dir_, -3)
            caminho = QtGui.QPainterPath()
            raio = 9
            if primeira and ultima:
                caminho.addRoundedRect(rr, raio, raio)
            elif primeira:
                caminho.addRoundedRect(rr.adjusted(0, 0, raio, 0), raio, raio)
            elif ultima:
                caminho.addRoundedRect(rr.adjusted(-raio, 0, 0, 0), raio, raio)
            else:
                caminho.addRect(rr)
            p.save()
            p.setClipRect(r, Qt.IntersectClip)
            p.fillPath(caminho, fundo)
            p.restore()
            if primeira and "ativa" in tags:
                p.fillPath(caminho_arredondado(QRectF(r.x() + 4, r.y() + 11, 3, r.height() - 22), 1.5),
                           cor("destaque"))
        elif idx.row() < modelo.rowCount() - 1:  # separador fino entre linhas
            p.setPen(QtGui.QPen(cor("grade"), 1))
            y = r.bottom() + 0.5
            p.drawLine(QPointF(r.x() + (12 if primeira else 0), y),
                       QPointF(r.right() - (12 if ultima else 0) + 1, y))

        muda = "muda" in tags
        c = r.adjusted(14, 0, -14, 0)
        alinhar = DIR if col.alinhar == "e" else ESQ
        if col.tipo == "status" and valor:
            self._situacao(p, c, *valor)
        elif col.tipo == "check" and valor:
            marcado, txt = valor
            caixa = QRectF(c.x(), c.center().y() - 9, 18, 18)
            if marcado:
                p.fillPath(caminho_arredondado(caixa, 6), cor("destaque"))
                pintar_icone(p, "check-bold", "#ffffff", caixa.adjusted(3, 3, -3, -3))
            else:
                p.setPen(QtGui.QPen(cor("borda_forte"), 1.5))
                p.setBrush(cor("superficie"))
                p.drawRoundedRect(caixa.adjusted(0.75, 0.75, -0.75, -0.75), 5.5, 5.5)
            texto(p, c.adjusted(28, 0, 0, 0), txt, fonte(10), "tinta" if marcado else "muda")
        elif col.tipo == "progresso":
            if valor is not None:
                self._progresso(p, c, valor, "ativa" in tags)
        else:
            s = "" if valor is None else str(valor)
            f = fonte(9.5 if col.tipo == "mono" else 10, 400, mono=col.tipo == "mono",
                      tabular=col.tipo == "num")
            tinta = "muda" if muda or (col.tipo == "num" and s in ("0", "—")) else "tinta"
            if col.tipo == "num" and col.id == "err" and s not in ("0", "—", ""):
                tinta = "erro_texto"
                f = fonte(10, 600, tabular=True)
            texto(p, c, s, f, tinta, alinhar)
        p.restore()

    def _situacao(self, p, r, chave, txt):
        nome_icone, tinta, fundo = VISUAL_SITUACAO.get(chave, VISUAL_SITUACAO["pendente"])
        f = fonte(9, 600)
        w = min(r.width(), largura_texto(txt, f) + 40)
        pill = QRectF(r.x(), r.center().y() - 13, w, 26)
        p.fillPath(caminho_arredondado(pill, 13), cor(fundo))
        ri = QRectF(pill.x() + 9, pill.center().y() - 7, 14, 14)
        if nome_icone:
            pintar_icone(p, nome_icone, tinta, ri)
        else:
            desenhar_spinner(p, ri.center(), 5.5, cor(tinta), 2)
        texto(p, pill.adjusted(29, 0, -8, 0), txt, f, tinta)

    def _progresso(self, p, r, valor, ativa):
        f = fonte(9, 500, tabular=True)
        rotulo = f"{valor:.0%}"
        lr = largura_texto("100%", f) + 8
        barra = QRectF(r.x(), r.center().y() - 3, max(10, r.width() - lr), 6)
        p.fillPath(caminho_arredondado(barra, 3), cor("trilho"))
        if valor > 0:
            cheio = QRectF(barra.x(), barra.y(), max(6, barra.width() * min(1, valor)), 6)
            chave = "ok" if valor >= 1 else "destaque"
            g = QtGui.QLinearGradient(cheio.topLeft(), cheio.topRight())
            g.setColorAt(0, cor(chave))
            g.setColorAt(1, cor(chave + "2"))
            p.fillPath(caminho_arredondado(cheio, 3), g)
        texto(p, QRectF(barra.right() + 4, r.y(), lr, r.height()), rotulo, f,
              "tinta" if ativa else "tinta2", DIR, elidir=False)


class Vista(Animado, QtWidgets.QTableView):
    linha_clicada = QtCore.Signal(str)
    selecionada = QtCore.Signal(str)

    def __init__(self, colunas, selecionavel=True, vazio=("tray", "Nada por aqui ainda")):
        super().__init__()
        self.selecionavel, self.vazio = selecionavel, vazio
        self.clicavel = selecionavel  # mostra a mãozinha sobre as linhas
        self.modelo = Modelo(colunas)
        self.setModel(self.modelo)
        self.setItemDelegate(Delegado(self))
        self.linha_hover = -1
        self._exibidas = [c.id for c in colunas]
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(46)
        self.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        cab = self.horizontalHeader()
        cab.setFixedHeight(38)
        cab.setHighlightSections(False)
        cab.setSectionsClickable(False)
        cab.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        cab.setMinimumSectionSize(30)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(14)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection if selecionavel
                              else QtWidgets.QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.StrongFocus if selecionavel else Qt.NoFocus)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.viewport().setAutoFillBackground(False)
        self.clicked.connect(lambda i: self.linha_clicada.emit(self.modelo.linhas[i.row()][0]))
        self.selectionModel().selectionChanged.connect(self._mudou_selecao)
        self.modelo.rowsInserted.connect(self._talvez_animar)
        self.modelo.dataChanged.connect(self._talvez_animar)

    # animação do arco "em andamento" na coluna de situação
    @property
    def continuo(self):
        return self.isVisible() and bool(self.modelo.ativas)

    def _talvez_animar(self, *_):
        if self.continuo:
            self.animar()

    def passo(self, dt):
        if not ambiente(self, dt):
            return self.continuo
        col = next((i for i, c in enumerate(self.modelo.colunas) if c.tipo == "status"), None)
        for iid in self.modelo.ativas:
            if col is None:
                self.viewport().update()
                break
            self.viewport().update(self.visualRect(self.modelo.index(self.modelo.indice[iid], col)))
        return self.continuo

    def _mudou_selecao(self, *_):
        linhas = self.selectionModel().selectedRows()
        if linhas:
            self.selecionada.emit(self.modelo.linhas[linhas[0].row()][0])
        self.viewport().update()

    def colunas_exibidas(self):
        return [i for i, c in enumerate(self.modelo.colunas) if c.id in self._exibidas]

    def exibir(self, ids):
        self._exibidas = list(ids)
        for i, c in enumerate(self.modelo.colunas):
            self.setColumnHidden(i, c.id not in self._exibidas)
        self.ajustar()

    def ajustar(self):
        """Todas as colunas visíveis cabem: as de número mantêm a largura, as de texto
        ficam com o que sobra; se nem assim couber, tudo encolhe por igual."""
        w = self.viewport().width()
        if w < 50:
            return
        cols = [(i, c) for i, c in enumerate(self.modelo.colunas) if c.id in self._exibidas]
        fixas = [(i, c) for i, c in cols if not c.elastica]
        elasticas = [(i, c) for i, c in cols if c.elastica]
        sobra = w - sum(c.largura for _, c in fixas)
        if elasticas and sobra >= 80 * len(elasticas):
            base = sum(c.largura for _, c in elasticas)
            for i, c in fixas:
                self.setColumnWidth(i, c.largura)
            resto = sobra
            for n, (i, c) in enumerate(elasticas):
                larg = resto if n == len(elasticas) - 1 else int(sobra * c.largura / base)
                self.setColumnWidth(i, larg)
                resto -= larg
        else:
            fator = w / (sum(c.largura for _, c in cols) or 1)
            for i, c in cols:
                self.setColumnWidth(i, max(30, int(c.largura * fator)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ajustar()

    def mouseMoveEvent(self, e):
        linha = self.rowAt(int(e.position().y()))
        if linha != self.linha_hover:
            self.linha_hover = linha
            self.viewport().update()
            self.viewport().setCursor(Qt.PointingHandCursor if linha >= 0 and self.clicavel
                                      else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.linha_hover = -1
        self.viewport().update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.modelo.rowCount() == 0 and self.vazio:
            p = pintor(self.viewport())
            r = QRectF(self.viewport().rect())
            meio = r.center()
            nome, txt = self.vazio
            pintar_icone(p, nome, "borda_forte", QRectF(meio.x() - 16, meio.y() - 30, 32, 32))
            texto(p, QRectF(r.x() + 20, meio.y() + 8, r.width() - 40, 22), txt, fonte(9.5),
                  "muda", CENTRO)


class Tabela(Cartao):
    """Tabela num cartão: cabeçalho discreto, linhas altas, situação em pílula colorida."""

    def __init__(self, colunas, selecionavel=True, vazio=("tray", "Nada por aqui ainda"),
                 pai=None, sombra=True):
        super().__init__(pai, padding=6, sombra=sombra)
        self.colunas = [c if isinstance(c, Coluna) else Coluna(*c) for c in colunas]
        self.ids = [c.id for c in self.colunas]
        self.vista = Vista(self.colunas, selecionavel, vazio)
        self.lay.addWidget(self.vista)
        self.modelo = self.vista.modelo
        self._seq = 0
        TEMA.mudou.connect(self.vista.viewport().update)

    def limpar(self):
        self.modelo.limpar()

    def no_fim(self):
        b = self.vista.verticalScrollBar()
        return b.value() >= b.maximum() - 4

    def inserir(self, valores, tags=(), iid=None, rolar=False):
        """rolar=True acompanha o fim só se você já estava no fim (não tira do lugar)."""
        acompanhar = rolar and self.no_fim()
        if iid is None:
            self._seq += 1
            iid = f"_{self._seq}"
        self.modelo.inserir(iid, valores, tags)
        if acompanhar:
            self.vista.scrollToBottom()
        return iid

    def atualizar(self, iid, valores, tags=()):
        self.modelo.atualizar(iid, valores, tags)

    def valor(self, iid, cid):
        return self.modelo.linhas[self.modelo.indice[iid]][1][self.ids.index(cid)]

    def mudar(self, iid, cid, valor):
        self.modelo.atualizar(iid, coluna=self.ids.index(cid), valor=valor)

    def ver(self, iid):
        if iid in self.modelo.indice:
            self.vista.scrollTo(self.modelo.index(self.modelo.indice[iid], 0))

    def selecionar(self, iid):
        if iid in self.modelo.indice:
            self.vista.selectRow(self.modelo.indice[iid])

    def colunas_visiveis(self, ids):
        self.vista.exibir(ids)


# --------------------------------------------------------------- indicadores
class Kpi(Animado, Cartao):
    """Cartão de indicador: ícone num quadrado colorido, rótulo e número que conta
    suavemente até o valor novo."""
    quadro = QUADRO_AMBIENTE  # contar/deslizar a ~30 quadros/s já fica suave aqui

    def __init__(self, rotulo, nome_icone, cor_icone, pai=None):
        super().__init__(pai, padding=0)
        self.rotulo, self.nome_icone, self.cor_icone = rotulo, nome_icone, cor_icone
        self.n = Suave(0, 7, 0.0001)
        self.formato = str
        self.cor_valor = "tinta"
        self.vazio = True
        self.setMinimumHeight(86)
        TEMA.mudou.connect(self.update)

    def suaves(self):
        return (self.n,)

    def set(self, valor, formato=None, cor_valor=None):
        if formato:
            self.formato = formato
        self.cor_valor = cor_valor or "tinta"
        if valor is None:
            self.vazio = True
            self.n.ir(0, True)
            self.update()
            return
        primeira = self.vazio
        self.vazio = False
        self.n.ir(valor, primeira)
        self.animar()

    def paintEvent(self, e):
        p = pintor(self)
        self.pintar_fundo(p)
        r = QRectF(self.rect()).adjusted(18, 0, -14, 0)
        tile = QRectF(r.x(), r.center().y() - 22, 44, 44)
        p.fillPath(caminho_arredondado(tile, 13), cor(self.cor_icone + "_claro"))
        pintar_icone(p, self.nome_icone, self.cor_icone if self.cor_icone != "destaque"
                     else "destaque", tile.adjusted(11, 11, -11, -11))
        x = tile.right() + 14
        texto(p, QRectF(x, r.center().y() - 24, r.right() - x, 20), self.rotulo, fonte(9, 500),
              "muda")
        valor = "—" if self.vazio else self.formato(int(round(self.n.v)))
        texto(p, QRectF(x, r.center().y() - 4, r.right() - x, 30), valor,
              fonte(17, 600, titulo=True, tabular=True), self.cor_valor)


def desenhar_barra(p, r, valor, c1, c2, indet=None, brilho=None):
    """Barra arredondada; indet = fase do vai-e-vem; brilho = fase do reflexo (0..1)."""
    raio = r.height() / 2
    p.fillPath(caminho_arredondado(r, raio), cor("trilho"))
    if indet is not None:
        seg = 0.32
        pos = (math.sin(indet * 2.3 - math.pi / 2) + 1) / 2 * (1 - seg)
        cheio = QRectF(r.x() + r.width() * pos, r.y(), r.width() * seg, r.height())
    else:
        if valor <= 0.001:
            return
        cheio = QRectF(r.x(), r.y(), max(r.height(), r.width() * min(1.0, valor)), r.height())
    g = QtGui.QLinearGradient(cheio.topLeft(), cheio.topRight())
    g.setColorAt(0, cor(c1))
    g.setColorAt(1, cor(c2))
    caminho = caminho_arredondado(cheio, raio)
    p.fillPath(caminho, g)
    if brilho is not None and indet is None and cheio.width() > 30:
        # reflexo de luz atravessando a parte preenchida
        larg = min(140.0, cheio.width() * 0.5)
        x = cheio.x() - larg + (cheio.width() + larg) * brilho
        gb = QtGui.QLinearGradient(QPointF(x, 0), QPointF(x + larg, 0))
        gb.setColorAt(0, QtGui.QColor(255, 255, 255, 0))
        gb.setColorAt(0.5, QtGui.QColor(255, 255, 255, 80))
        gb.setColorAt(1, QtGui.QColor(255, 255, 255, 0))
        p.save()
        p.setClipPath(caminho)
        p.fillRect(QRectF(x, cheio.y(), larg, cheio.height()), gb)
        p.restore()


class _Linha:
    def __init__(self, c1, c2):
        self.c1, self.c2 = c1, c2
        self.rotulo = self.direita = ""
        self.val = Suave(0, 6.0)
        self.indet = None
        self.barra = True


class PainelProgresso(Animado, Cartao):
    """Anel com o total à esquerda e barras finas à direita. Tudo desliza até o
    valor novo; enquanto roda, um reflexo de luz atravessa as barras."""

    def __init__(self, linhas, anel=True, pai=None, cartao=True):
        super().__init__(pai, padding=0, sombra=cartao)
        if not cartao:
            self.fundo, self.cor_borda = None, None
        self.ordem = [k for k, _, _ in linhas]
        self.linhas = {k: _Linha(c1, c2) for k, c1, c2 in linhas}
        self.com_anel = anel
        self.anel_val = Suave(0, 5.0)
        self.anel_indet = False
        self.anel_txt, self.anel_leg = "—", ""
        self.anel_cores = ("destaque", "destaque2")
        self.ativo = False
        self._fase = 0.0
        alt = (44 if cartao else 0) + 58 * len(linhas)
        self.setMinimumHeight(max(alt, 196 if anel else 0))
        TEMA.mudou.connect(self.update)

    def suaves(self):
        return [L.val for L in self.linhas.values()] + [self.anel_val]

    @property
    def continuo(self):
        return self.isVisible() and (self.ativo or self.anel_indet
                                     or any(L.indet is not None for L in self.linhas.values()))

    def passo(self, dt):
        self._fase += dt
        for L in self.linhas.values():
            if L.indet is not None:
                L.indet += dt
        return super().passo(dt)

    def regiao_continua(self):
        if self.anel_indet or not getattr(self, "_barras", None):
            return None
        return QtGui.QRegion(self._barras.toAlignedRect().adjusted(-2, -2, 2, 2))

    def pintar_fundo(self, p):
        if self.fundo:
            super().pintar_fundo(p)

    def definir(self, chave, rotulo=None, direita=None):
        L = self.linhas[chave]
        if rotulo is not None:
            L.rotulo = rotulo
        if direita is not None:
            L.direita = direita
        self.update()

    def barra_visivel(self, chave, sim):
        self.linhas[chave].barra = sim
        self.update()

    def alvo(self, chave, valor, instantaneo=False):
        L = self.linhas[chave]
        valor = max(0.0, min(1.0, valor))
        L.indet = None
        if valor < L.val.v - 0.02:  # recomeço (pasta nova): sem deslizar para trás
            instantaneo = True
        L.val.ir(valor, instantaneo)
        self.animar()

    def indeterminado(self, chave):
        self.linhas[chave].indet = 0.0
        self.animar()

    def anel(self, valor=None, txt=None, legenda=None, cores=None, indeterminado=False):
        self.anel_indet = indeterminado
        if valor is not None:
            self.anel_val.ir(max(0.0, min(1.0, valor)), valor < self.anel_val.v - 0.02)
        if txt is not None:
            self.anel_txt = txt
        if legenda is not None:
            self.anel_leg = legenda
        if cores:
            self.anel_cores = cores
        self.animar()

    def em_andamento(self, sim):
        self.ativo = sim
        self.animar()

    def paintEvent(self, e):
        p = pintor(self)
        self.pintar_fundo(p)
        cartao = self.fundo is not None
        m = 24 if cartao else 0
        r = QRectF(self.rect()).adjusted(m, 0, -m, 0)
        if self.com_anel:
            d = min(148.0, self.height() - 44)
            ra = QRectF(r.x(), (self.height() - d) / 2, d, d)
            self._anel(p, ra)
            r.setLeft(ra.right() + 30)
        n = len(self.ordem)
        alt_linha = 58
        y = (self.height() - alt_linha * n) / 2 + 4
        brilho = (self._fase % 2.2) / 1.6 if self.ativo else None
        if brilho is not None and brilho > 1:
            brilho = None
        self._barras = QRectF(r.x(), y + 30, r.width(), alt_linha * (n - 1) + 10)
        for chave in self.ordem:
            L = self.linhas[chave]
            texto(p, QRectF(r.x(), y, r.width() * 0.62, 22), L.rotulo, fonte(10.5, 600), "tinta")
            texto(p, QRectF(r.x() + r.width() * 0.3, y, r.width() * 0.7, 22), L.direita,
                  fonte(9.5, 400, tabular=True), "muda", DIR)
            if L.barra:
                desenhar_barra(p, QRectF(r.x(), y + 30, r.width(), 10), L.val.v, L.c1, L.c2,
                               L.indet, brilho)
            y += alt_linha

    def _anel(self, p, r):
        esp = 11.0
        rr = r.adjusted(esp / 2, esp / 2, -esp / 2, -esp / 2)
        caneta = QtGui.QPen(cor("trilho"), esp)
        caneta.setCapStyle(Qt.RoundCap)
        p.setPen(caneta)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rr)
        c1, c2 = self.anel_cores
        if self.anel_indet:
            t = self._fase
            inicio = -(t * 300) % 360
            tamanho = 60 + 70 * (1 + math.sin(t * 2.4)) / 2
        else:
            inicio, tamanho = 90.0, -360 * self.anel_val.v
            if abs(tamanho) < 0.5:
                tamanho = 0
        if tamanho:
            g = QtGui.QConicalGradient(rr.center(), 90)
            g.setColorAt(0, cor(c2))
            g.setColorAt(0.5, cor(c1))
            g.setColorAt(1, cor(c2))
            caneta = QtGui.QPen(QtGui.QBrush(g), esp)
            caneta.setCapStyle(Qt.RoundCap)
            p.setPen(caneta)
            p.drawArc(rr, int(inicio * 16), int(tamanho * 16))
        texto(p, QRectF(r.x(), r.center().y() - 22, r.width(), 34), self.anel_txt,
              fonte(20, 600, titulo=True, tabular=True), "tinta", CENTRO, elidir=False)
        texto(p, QRectF(r.x() + 14, r.center().y() + 12, r.width() - 28, 18), self.anel_leg,
              fonte(8.5, 500), "muda", CENTRO)


class BarraSimples(PainelProgresso):
    """Uma barra só, sem cartão (para o diálogo de atualização)."""

    def __init__(self, pai=None):
        super().__init__([("b", "destaque", "destaque2")], anel=False, pai=pai, cartao=False)
        self.setMinimumHeight(56)
        self.setMaximumHeight(56)


# --------------------------------------------------------------- gráfico
def maximo_redondo(v):
    """Topo do eixo: 4 divisões de 1/2/2,5/5 × 10^k."""
    passo = max(v, 1) / 4
    base = 10 ** (len(str(int(passo))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if m * base >= passo:
            return m * base * 4
    return passo * 4


class GraficoVelocidade(Animado, Cartao):
    """Velocidade atual em destaque, curva suave com degradê e os tempos embaixo."""
    quadro = QUADRO_AMBIENTE  # contar/deslizar a ~30 quadros/s já fica suave aqui
    LIMITE = 600

    def __init__(self, fmt_bytes, fmt_tempo, pai=None):
        super().__init__(pai, padding=0)
        self.fmt_bytes, self.fmt_tempo = fmt_bytes, fmt_tempo
        self.pontos = []  # (segundos desde o início, bytes/s)
        self.vmax = Suave(1, 4.0, 0.001)
        self.atual = Suave(0, 5.0, 0.002)
        self.revelar = Suave(1, 5.0, 0.002)
        self.info = [("timer", "Decorrido", "—"), ("hourglass", "Restante", "—"),
                     ("folder", "Pastas", "—")]
        self.ativo = False
        self.mouse = None
        self.setMouseTracking(True)
        self.setMinimumHeight(196)
        TEMA.mudou.connect(self.update)

    def suaves(self):
        return (self.vmax, self.atual, self.revelar)

    def regiao_continua(self):
        pt = getattr(self, "_pt_ultimo", None)
        if pt is None or self.mouse is not None:
            return None
        return QtGui.QRegion(int(pt.x()) - 16, int(pt.y()) - 16, 32, 32)

    @property
    def continuo(self):
        return self.ativo and self.isVisible() and len(self.pontos) >= 2

    def limpar(self):
        self.pontos = []
        self.atual.ir(0, True)
        self.vmax.ir(1, True)
        self.update()

    def em_andamento(self, sim):
        self.ativo = sim
        self.animar()

    def adicionar(self, t, v):
        self.pontos.append((t, v))
        if len(self.pontos) > self.LIMITE:  # junta pares: o gráfico cobre a execução toda
            p = self.pontos
            self.pontos = [((p[i][0] + p[i + 1][0]) / 2, (p[i][1] + p[i + 1][1]) / 2)
                           for i in range(0, len(p) - 1, 2)] + ([p[-1]] if len(p) % 2 else [])
        pico = max(v for _, v in self.pontos)  # topo do eixo redondo na unidade exibida
        div = 1024 ** 2 if pico >= 1024 ** 2 else 1024 if pico >= 1024 else 1
        self.vmax.ir(maximo_redondo(pico / div) * div)
        self.atual.ir(v, self.atual.v == 0)
        self.revelar.ir(0, True)
        self.revelar.ir(1)
        self.animar()

    def definir_info(self, itens):
        if itens != self.info:
            self.info = itens
            self.update()

    def _area(self):
        return QRectF(22, 84, self.width() - 44, self.height() - 84 - 80)

    def _xy(self, area):
        p = self.pontos
        if self.revelar.v < 1 and len(p) >= 3:  # o último trecho "cresce" até o ponto novo
            (ta, va), (tb, vb) = p[-2], p[-1]
            k = self.revelar.v
            p = p[:-1] + [(ta + (tb - ta) * k, va + (vb - va) * k)]
        t0, t1 = p[0][0], p[-1][0]
        vmax = max(self.vmax.v, 1)
        return [QPointF(area.x() + (t - t0) / ((t1 - t0) or 1) * area.width(),
                        area.bottom() - min(1.0, v / vmax) * area.height()) for t, v in p]

    def paintEvent(self, e):
        p = pintor(self)
        self.pintar_fundo(p)
        w = self.width()
        tile = QRectF(22, 22, 34, 34)
        p.fillPath(caminho_arredondado(tile, 10), cor("destaque_claro"))
        pintar_icone(p, "lightning-fill", "destaque", tile.adjusted(8, 8, -8, -8))
        texto(p, QRectF(66, 20, 160, 18), "Velocidade", fonte(9, 500), "muda")
        atual = f"{self.fmt_bytes(self.atual.v)}/s" if self.pontos else "—"
        texto(p, QRectF(66, 36, w - 90, 24), atual, fonte(14, 600, titulo=True, tabular=True),
              "tinta")
        if self.pontos:
            vs = [v for _, v in self.pontos]
            resumo = f"média {self.fmt_bytes(sum(vs) / len(vs))}/s · pico {self.fmt_bytes(max(vs))}/s"
            texto(p, QRectF(w * 0.4, 20, w * 0.6 - 22, 18), resumo, fonte(8.5), "muda", DIR)

        area = self._area()
        caneta = QtGui.QPen(cor("grade"), 1, Qt.DashLine)
        caneta.setDashPattern([3, 4])
        p.setPen(caneta)
        for frac in (0, .5, 1):
            y = area.bottom() - area.height() * frac
            p.drawLine(QPointF(area.x(), y), QPointF(area.right(), y))
        if len(self.pontos) < 2:
            texto(p, area, "Aguardando a transferência", fonte(9), "muda", CENTRO)
        else:
            vmax = self.vmax.v
            div, unid = (1024 ** 2, "MB/s") if vmax >= 1024 ** 2 else (1024, "KB/s") \
                if vmax >= 1024 else (1, "B/s")
            for frac in (.5, 1):
                y = area.bottom() - area.height() * frac
                rot = f"{vmax * frac / div:.1f}".rstrip("0").rstrip(".").replace(".", ",")
                texto(p, QRectF(area.x(), y - 16, 120, 14),
                      f"{rot} {unid}" if frac == 1 else rot, fonte(7.5), "muda", elidir=False)
            pts = self._xy(area)
            curva = QtGui.QPainterPath(pts[0])
            for i in range(len(pts) - 1):  # Catmull-Rom → Bézier: curva que passa nos pontos
                a, b = pts[max(0, i - 1)], pts[i]
                c, d = pts[i + 1], pts[min(len(pts) - 1, i + 2)]
                c1 = QPointF(b.x() + (c.x() - a.x()) / 6, b.y() + (c.y() - a.y()) / 6)
                c2 = QPointF(c.x() - (d.x() - b.x()) / 6, c.y() - (d.y() - b.y()) / 6)
                c1.setY(max(area.y(), min(area.bottom(), c1.y())))
                c2.setY(max(area.y(), min(area.bottom(), c2.y())))
                curva.cubicTo(c1, c2, c)
            preenchido = QtGui.QPainterPath(curva)
            preenchido.lineTo(pts[-1].x(), area.bottom())
            preenchido.lineTo(pts[0].x(), area.bottom())
            preenchido.closeSubpath()
            g = QtGui.QLinearGradient(QPointF(0, area.y()), QPointF(0, area.bottom()))
            g.setColorAt(0, cor("destaque", 0.30))
            g.setColorAt(1, cor("destaque", 0.0))
            p.fillPath(preenchido, g)
            gl = QtGui.QLinearGradient(QPointF(area.x(), 0), QPointF(area.right(), 0))
            gl.setColorAt(0, cor("destaque2"))
            gl.setColorAt(1, cor("destaque"))
            caneta = QtGui.QPen(QtGui.QBrush(gl), 2.4)
            caneta.setCapStyle(Qt.RoundCap)
            caneta.setJoinStyle(Qt.RoundJoin)
            p.setPen(caneta)
            p.setBrush(Qt.NoBrush)
            p.drawPath(curva)
            ultimo = self._pt_ultimo = pts[-1]
            if self.ativo:  # pulso no ponto mais recente
                fase = (time.perf_counter() * 0.8) % 1
                p.setPen(Qt.NoPen)
                p.setBrush(cor("destaque", 0.30 * (1 - fase)))
                p.drawEllipse(ultimo, 4 + 9 * fase, 4 + 9 * fase)
            self._ponto(p, ultimo)
            if self.mouse is not None:
                self._dica(p, area, pts)

        y = self.height() - 58
        p.setPen(QtGui.QPen(cor("grade"), 1))
        p.drawLine(QPointF(22, y - 8), QPointF(w - 22, y - 8))
        larg = (w - 44) / len(self.info)
        for i, (nome_icone, rotulo, valor) in enumerate(self.info):
            x = 22 + i * larg
            pintar_icone(p, nome_icone, "muda", QRectF(x, y + 4, 14, 14))
            texto(p, QRectF(x + 20, y + 2, larg - 24, 18), rotulo, fonte(8.5), "muda")
            texto(p, QRectF(x, y + 22, larg - 6, 22), valor, fonte(10.5, 600, tabular=True), "tinta")

    def _ponto(self, p, pt):
        p.setPen(QtGui.QPen(cor("superficie"), 2.5))
        p.setBrush(cor("destaque"))
        p.drawEllipse(pt, 4.5, 4.5)

    def _dica(self, p, area, pts):
        mx = self.mouse.x()
        if not (area.x() - 6 <= mx <= area.right() + 6 and area.y() - 20 <= self.mouse.y() <= area.bottom() + 10):
            return
        i = min(range(len(pts)), key=lambda k: abs(pts[k].x() - mx))
        pt = pts[i]
        t, v = self.pontos[min(i, len(self.pontos) - 1)]
        caneta = QtGui.QPen(cor("borda_forte"), 1, Qt.DashLine)
        p.setPen(caneta)
        p.drawLine(QPointF(pt.x(), area.y()), QPointF(pt.x(), area.bottom()))
        self._ponto(p, pt)
        linhas = (f"{self.fmt_bytes(v)}/s", f"aos {self.fmt_tempo(t)} de execução")
        f1, f2 = fonte(9.5, 600, tabular=True), fonte(8.5)
        larg = max(largura_texto(linhas[0], f1), largura_texto(linhas[1], f2)) + 22
        caixa = QRectF(pt.x() + 12, pt.y() - 26, larg, 44)
        if caixa.right() > self.width() - 8:
            caixa.moveRight(pt.x() - 12)
        caixa.moveTop(max(area.y() - 24, min(caixa.y(), area.bottom() - 44)))
        desenhar_sombra(p, caixa, 9, -0.2)
        p.fillPath(caminho_arredondado(caixa, 9), cor("superficie"))
        p.setPen(QtGui.QPen(cor("borda_forte"), 1))
        p.setBrush(Qt.NoBrush)  # o pincel ainda era o azul do ponto
        p.drawPath(caminho_arredondado(caixa, 9))
        texto(p, caixa.adjusted(11, 5, -8, -22), linhas[0], f1, "tinta")
        texto(p, caixa.adjusted(11, 22, -8, -4), linhas[1], f2, "muda")

    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        self.update()

    def leaveEvent(self, e):
        self.mouse = None
        self.update()


# --------------------------------------------------------------- cartões de modo
class CartaoModo(Animado, Cartao):
    """Cartão clicável que acende ao passar o mouse e fica azul quando escolhido."""
    clicado = QtCore.Signal()

    def __init__(self, titulo, descricao, nome_icone, pai=None):
        super().__init__(pai, padding=0)
        self.titulo, self.descricao, self.nome_icone = titulo, descricao, nome_icone
        self.h, self.s = Suave(0, 12), Suave(0, 12)
        self.escolhido = False
        self._foco = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(138)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        TEMA.mudou.connect(self.update)

    def suaves(self):
        return (self.h, self.s)

    def passo(self, dt):
        continua = super().passo(dt)
        self._avisar_superficie()  # a sombra cresce junto
        return continua

    def elevacao(self):
        return 0.6 * self.h.v + 0.3 * self.s.v

    def selecionar(self, sim):
        self.escolhido = sim
        self.s.ir(1 if sim else 0)
        self.animar()

    def enterEvent(self, e):
        self.h.ir(1)
        self.animar()

    def leaveEvent(self, e):
        self.h.ir(0)
        self.animar()

    def mouseReleaseEvent(self, e):
        if self.rect().contains(e.position().toPoint()):
            self.clicado.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicado.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        self._foco = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        self.update()

    def focusOutEvent(self, e):
        self._foco = False
        self.update()

    def paintEvent(self, e):
        p = pintor(self)
        h, s = self.h.v, self.s.v
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        caminho = caminho_arredondado(r, 16)
        p.fillPath(caminho, misturar(misturar("superficie", "superficie2", h * 0.6), "destaque_claro", s))
        borda = misturar(misturar("borda", "borda_forte", h), "destaque", s)
        p.setPen(QtGui.QPen(borda, 1 + s))
        p.drawPath(caminho_arredondado(r.adjusted(s / 2, s / 2, -s / 2, -s / 2), 16))
        if self._foco:
            p.setPen(QtGui.QPen(cor("destaque", .5), 2))
            p.drawPath(caminho_arredondado(r.adjusted(-0.5, -0.5, 0.5, 0.5), 17))
        bola = QRectF(r.x() + 18, r.y() + 18, 40, 40)
        p.setPen(Qt.NoPen)
        p.setBrush(misturar("destaque_claro", "destaque", s))
        p.drawEllipse(bola)
        pintar_icone(p, self.nome_icone, misturar("destaque", "#ffffff", s), bola.adjusted(10, 10, -10, -10))
        if s > 0.01:  # selo de escolhido
            sel = QRectF(r.right() - 34, r.y() + 16, 20, 20)
            p.setOpacity(s)
            p.setBrush(cor("destaque"))
            p.drawEllipse(sel)
            pintar_icone(p, "check-bold", "#ffffff", sel.adjusted(4, 4, -4, -4))
            p.setOpacity(1)
        texto(p, QRectF(r.x() + 18, bola.bottom() + 12, r.width() - 34, 22),
              self.titulo, fonte(11, 600, titulo=True), misturar("tinta", "destaque_tinta", s))
        p.setFont(fonte(9))
        p.setPen(cor("tinta2"))
        p.drawText(QRectF(r.x() + 18, bola.bottom() + 36, r.width() - 34, r.bottom() - bola.bottom() - 38),
                   Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self.descricao)


# --------------------------------------------------------------- barra lateral
class BarraLateral(Animado, QtWidgets.QWidget):
    """Navegação com pílula que desliza, aviso de atualização e andamento resumido."""
    escolhido = QtCore.Signal(str)
    aviso_clicado = QtCore.Signal()
    ITENS = (("config", "plus-circle", "Nova migração"), ("progresso", "chart-line-up", "Andamento"),
             ("historico", "clock-counter-clockwise", "Histórico"), ("opcoes", "gear-six", "Opções"))
    TOPO, ALTURA = 104, 46

    def __init__(self, versao, pai=None):
        super().__init__(pai)
        self.versao = versao
        self.setFixedWidth(244)
        self.setMouseTracking(True)
        self.ativo, self.hover = "config", None
        self.y = Suave(self.TOPO, 16, 0.001)
        self.situacao = ("Pronto", "", False, None)
        self.pct = Suave(0, 5)
        self.aviso = None  # (título, detalhe, ícone)
        self.av = Suave(0, 10)
        self._fase = 0.0

    def suaves(self):
        return (self.y, self.pct, self.av)

    @property
    def continuo(self):
        return self.situacao[2]

    def passo(self, dt):
        self._fase += dt
        navegando = any([self.y.passo(dt), self.av.passo(dt)])
        andando = self.pct.passo(dt)
        if navegando:
            self.update()
        elif ambiente(self, dt):  # o andamento fica só na caixa de baixo e no selo do item
            self.update(self.regiao_continua())
        return navegando or andando or self.continuo

    def regiao_continua(self):
        w, h = self.width(), self.height()
        regiao = QtGui.QRegion(14, h - 118, w - 28, 66)
        return regiao.united(QtGui.QRegion(14, self.TOPO + self.ALTURA + 4, w - 28, self.ALTURA))

    def _zona(self, pos):
        y = pos.y()
        for i, (chave, _, _) in enumerate(self.ITENS):
            y0 = self.TOPO + i * (self.ALTURA + 4)
            if y0 <= y <= y0 + self.ALTURA:
                return chave
        if self.aviso and self._ret_aviso().contains(pos):
            return "aviso"
        return None

    def _ret_aviso(self):
        return QRectF(14, self.height() - 196, self.width() - 28, 64)

    def ativar(self, chave):
        self.ativo = chave
        i = [c for c, _, _ in self.ITENS].index(chave)
        self.y.ir(self.TOPO + i * (self.ALTURA + 4))
        self.animar()

    def definir_aviso(self, titulo, detalhe="", nome_icone="arrow-circle-up-fill"):
        self.aviso = (titulo, detalhe, nome_icone) if titulo else None
        self.av.ir(1 if titulo else 0, True)
        self.update()

    def estado(self, txt, detalhe="", ativo=False, pct=None):
        nova = (txt, detalhe, ativo, pct)
        if nova == self.situacao:
            return
        self.situacao = nova
        if pct is not None:
            self.pct.ir(pct)
        self.animar()

    def mouseMoveEvent(self, e):
        z = self._zona(e.position())
        if z != self.hover:
            self.hover = z
            self.setCursor(Qt.PointingHandCursor if z else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        z = self._zona(e.position())
        if z == "aviso":
            self.aviso_clicado.emit()
        elif z:
            self.escolhido.emit(z)

    def paintEvent(self, e):
        p = pintor(self)
        w, h = self.width(), self.height()
        p.fillRect(e.rect(), cor("lateral"))
        p.setPen(QtGui.QPen(cor("lateral_borda"), 1))
        p.drawLine(QPointF(w - 0.5, 0), QPointF(w - 0.5, h))
        regiao = e.region()
        if regiao.intersects(QtCore.QRect(0, 0, w, h - 200)):
            self._pintar_topo(p, w, regiao)
        self._pintar_baixo(p, w, h)

    def _pintar_topo(self, p, w, regiao):
        if not regiao.intersects(QtCore.QRect(0, 0, w, self.TOPO + self.ALTURA)) and                 not regiao.intersects(QtCore.QRect(0, self.TOPO + 2 * (self.ALTURA + 4), w, 400)):
            # só o selo do item Andamento: desenha apenas esse item
            self._item(p, w, 1, *self.ITENS[1], QRectF(14, self.y.v, w - 28, self.ALTURA))
            return
        # logotipo
        logo = QRectF(22, 30, 40, 40)
        g = QtGui.QLinearGradient(logo.topLeft(), logo.bottomRight())
        g.setColorAt(0, cor("destaque2"))
        g.setColorAt(1, cor("destaque"))
        p.fillPath(caminho_arredondado(logo, 12), g)
        pintar_icone(p, "envelope-simple-fill", "#ffffff", logo.adjusted(9, 9, -9, -9))
        texto(p, QRectF(74, 30, w - 80, 22), "Migrador", fonte(14, 600, titulo=True), "#ffffff")
        texto(p, QRectF(74, 52, w - 80, 18), "de e-mails IMAP", fonte(9), "lateral_mudo")
        # itens
        sel = QRectF(14, self.y.v, w - 28, self.ALTURA)
        p.fillPath(caminho_arredondado(sel, 12), cor("lateral_ativo"))
        for i, (chave, nome_icone, txt) in enumerate(self.ITENS):
            self._item(p, w, i, chave, nome_icone, txt, None)

    def _item(self, p, w, i, chave, nome_icone, txt, sel):
        y0 = self.TOPO + i * (self.ALTURA + 4)
        r = QRectF(14, y0, w - 28, self.ALTURA)
        if sel is not None:  # redesenho isolado: repõe o fundo do item
            p.fillRect(r.adjusted(-1, -1, 1, 1), cor("lateral"))
            if sel.intersects(r):
                p.fillPath(caminho_arredondado(sel, 12), cor("lateral_ativo"))
        ativo = chave == self.ativo
        if chave == self.hover and not ativo:
            p.fillPath(caminho_arredondado(r, 12), cor("lateral_hover"))
        if ativo:  # marquinha azul à esquerda
            p.fillPath(caminho_arredondado(QRectF(r.x() + 6, r.center().y() - 9, 3, 18), 1.5),
                       cor("destaque2"))
        pintar_icone(p, nome_icone + ("-fill" if ativo else ""),
                     "#ffffff" if ativo else "lateral_mudo",
                     QRectF(r.x() + 18, r.center().y() - 10, 20, 20))
        texto(p, QRectF(r.x() + 50, r.y(), r.width() - 60, r.height()), txt,
              fonte(10, 600 if ativo else 400), "#ffffff" if ativo else "lateral_texto")
        if chave == "progresso" and self.situacao[2]:
            pct = self.situacao[3]
            rot = f"{pct:.0%}" if pct is not None else "●"
            f = fonte(8, 700, tabular=True)
            lw = largura_texto(rot, f) + 14
            rb = QRectF(r.right() - lw - 10, r.center().y() - 10, lw, 20)
            p.fillPath(caminho_arredondado(rb, 10), cor("destaque"))
            texto(p, rb, rot, f, "#ffffff", CENTRO, elidir=False)

    def _pintar_baixo(self, p, w, h):
        # aviso de atualização
        if self.aviso:
            ra = self._ret_aviso()
            g = QtGui.QLinearGradient(ra.topLeft(), ra.bottomRight())
            claro = self.hover == "aviso"
            g.setColorAt(0, cor("destaque2").lighter(108 if claro else 100))
            g.setColorAt(1, cor("destaque").lighter(108 if claro else 100))
            p.fillPath(caminho_arredondado(ra, 14), g)
            titulo, detalhe, nome_icone = self.aviso
            pintar_icone(p, nome_icone, "#ffffff", QRectF(ra.x() + 14, ra.center().y() - 11, 22, 22))
            texto(p, QRectF(ra.x() + 46, ra.y() + 12, ra.width() - 54, 20), titulo, fonte(9.5, 600),
                  "#ffffff")
            texto(p, QRectF(ra.x() + 46, ra.y() + 32, ra.width() - 54, 18), detalhe, fonte(8.5),
                  QtGui.QColor(255, 255, 255, 215))
        # andamento resumido
        txt, detalhe, ativo, pct = self.situacao
        caixa = QRectF(14, h - 118, w - 28, 66)
        p.fillPath(caminho_arredondado(caixa, 14), cor("lateral_hover"))
        anel = QRectF(caixa.x() + 14, caixa.center().y() - 16, 32, 32)
        caneta = QtGui.QPen(cor("lateral_ativo"), 4)
        caneta.setCapStyle(Qt.RoundCap)
        p.setPen(caneta)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(anel.adjusted(2, 2, -2, -2))
        if ativo and pct is not None:
            caneta.setColor(cor("destaque2"))
            p.setPen(caneta)
            p.drawArc(anel.adjusted(2, 2, -2, -2), 90 * 16, int(-360 * 16 * self.pct.v))
        elif ativo:
            caneta.setColor(cor("destaque2"))
            p.setPen(caneta)
            p.drawArc(anel.adjusted(2, 2, -2, -2), int(-(self._fase * 320) % 360 * 16), 100 * 16)
        else:
            pintar_icone(p, "check-bold", "ok2", anel.adjusted(9, 9, -9, -9))
        texto(p, QRectF(anel.right() + 12, caixa.y() + 13, caixa.width() - 66, 20), txt,
              fonte(9.5, 600), "#ffffff")
        texto(p, QRectF(anel.right() + 12, caixa.y() + 33, caixa.width() - 66, 18),
              detalhe or "nenhuma migração rodando", fonte(8.5), "lateral_mudo")
        texto(p, QRectF(24, h - 38, w - 48, 20), self.versao, fonte(8), "lateral_mudo")


# --------------------------------------------------------------- avisos
class Toast(QtWidgets.QWidget):
    """Aviso flutuante no rodapé da janela, que some sozinho."""

    def __init__(self, pai):
        super().__init__(pai)
        self.hide()
        self.txt, self.tipo, self.acao = "", "ok", None
        self.efeito = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.efeito)
        self.anim = QtCore.QPropertyAnimation(self.efeito, b"opacity", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.anim.finished.connect(lambda: self.efeito.opacity() < 0.05 and self.hide())
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.esconder)
        self.setCursor(Qt.ArrowCursor)

    def mostrar(self, txt, tipo="ok", acao=None, segundos=4):
        self.txt, self.tipo, self.acao = txt, tipo, acao
        f = fonte(10, 500)
        larg = largura_texto(txt, f) + 70 + (largura_texto(acao[0], fonte(10, 600)) + 30 if acao else 0)
        self.resize(int(min(larg, self.parentWidget().width() - 80)), 50)
        self._posicionar()
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(self.efeito.opacity() if self.isVisible() else 0)
        self.anim.setEndValue(1)
        self.anim.start()
        self.timer.start(int(segundos * 1000))
        self.update()

    def _posicionar(self):
        pai = self.parentWidget()
        # centrado na área das páginas, acima do rodapé com o botão Iniciar
        self.move((pai.width() - self.width()) // 2 + 122, pai.height() - self.height() - 112)

    def esconder(self):
        self.anim.stop()
        self.anim.setStartValue(self.efeito.opacity())
        self.anim.setEndValue(0)
        self.anim.start()

    def _ret_acao(self):
        if not self.acao:
            return QRectF()
        lw = largura_texto(self.acao[0], fonte(10, 600)) + 22
        return QRectF(self.width() - lw - 10, 10, lw, self.height() - 20)

    def mousePressEvent(self, e):
        if self.acao and self._ret_acao().contains(e.position()):
            self.acao[1]()
        self.esconder()

    def paintEvent(self, e):
        p = pintor(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fundo = QtGui.QColor("#23262d") if not TEMA.escuro() else QtGui.QColor("#2c3039")
        p.fillPath(caminho_arredondado(r, r.height() / 2), fundo)
        nome_icone, c = {"ok": ("check-circle-fill", "ok2"), "erro": ("x-circle-fill", "erro"),
                         "aviso": ("warning-fill", "aviso"), "info": ("info-fill", "destaque2")}[self.tipo]
        pintar_icone(p, nome_icone, c, QRectF(18, r.center().y() - 10, 20, 20))
        ra = self._ret_acao()
        texto(p, QRectF(48, 0, (ra.x() if self.acao else r.right()) - 56, self.height()), self.txt,
              fonte(10, 500), "#ffffff")
        if self.acao:
            p.fillPath(caminho_arredondado(ra, ra.height() / 2), QtGui.QColor(255, 255, 255, 30))
            texto(p, ra, self.acao[0], fonte(10, 600), "destaque2", CENTRO, elidir=False)


class Mensagem(QtWidgets.QDialog):
    """Caixa de mensagem no visual do app (ícone em círculo, título, texto, botões)."""

    def __init__(self, pai, titulo, txt, tipo="info", botoes=(("OK", "primario", True),)):
        super().__init__(pai)
        self.setWindowTitle(titulo)
        self.setModal(True)
        self.resultado = False
        nome_icone, tinta, fundo = {
            "info": ("info-fill", "destaque", "destaque_claro"),
            "pergunta": ("question-fill", "destaque", "destaque_claro"),
            "ok": ("check-circle-fill", "ok", "ok_claro"),
            "aviso": ("warning-fill", "aviso", "aviso_claro"),
            "erro": ("x-circle-fill", "erro", "erro_claro"),
            "perigo": ("warning-octagon-fill", "erro", "erro_claro")}[tipo]
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(16)
        topo = QtWidgets.QHBoxLayout()
        topo.setSpacing(16)
        bola = QtWidgets.QLabel()
        bola.setFixedSize(46, 46)
        pm = QtGui.QPixmap(46 * 2, 46 * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        pp = QtGui.QPainter(pm)
        pp.setRenderHint(QtGui.QPainter.Antialiasing)
        pp.setPen(Qt.NoPen)
        pp.setBrush(cor(fundo))
        pp.drawEllipse(QRectF(0, 0, 46, 46))
        pintar_icone(pp, nome_icone, tinta, QRectF(11, 11, 24, 24))
        pp.end()
        bola.setPixmap(pm)
        topo.addWidget(bola, 0, Qt.AlignTop)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(4)
        t = QtWidgets.QLabel(titulo)
        t.setProperty("papel", "cartao_titulo")
        col.addWidget(t)
        corpo = QtWidgets.QLabel(txt)
        corpo.setWordWrap(True)
        corpo.setTextInteractionFlags(Qt.TextSelectableByMouse)
        corpo.setProperty("papel", "rodape")
        corpo.setMinimumWidth(360)
        corpo.setMaximumWidth(520)
        col.addWidget(corpo)
        topo.addLayout(col, 1)
        lay.addLayout(topo)
        linha = QtWidgets.QHBoxLayout()
        linha.addStretch(1)
        self.padrao = None
        for rotulo, estilo, valor in botoes:
            b = Botao(rotulo, estilo=estilo, comando=lambda v=valor: self._fim(v))
            linha.addWidget(b)
            if estilo in ("primario", "perigo"):
                self.padrao = b
        lay.addLayout(linha)
        self.setSizeGripEnabled(False)
        self.layout().setSizeConstraint(QtWidgets.QLayout.SetFixedSize)

    def _fim(self, valor):
        self.resultado = valor
        self.accept() if valor else self.reject()

    def showEvent(self, e):
        from tema import barra_titulo
        barra_titulo(self)
        if self.padrao:
            self.padrao.setFocus(Qt.OtherFocusReason)
        super().showEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.padrao:
            self.padrao.click()
            return
        super().keyPressEvent(e)


def perguntar(pai, titulo, txt, sim="Sim", nao="Não", perigo=False):
    m = Mensagem(pai, titulo, txt, "perigo" if perigo else "pergunta",
                 ((nao, "secundario", False), (sim, "perigo" if perigo else "primario", True)))
    m.exec()
    return bool(m.resultado)


def avisar(pai, titulo, txt, tipo="aviso"):
    Mensagem(pai, titulo, txt, tipo).exec()


# --------------------------------------------------------------- layout
class Ladrilho(QtWidgets.QWidget):
    """Ícone dentro de um quadrado de cantos arredondados (ou círculo)."""

    def __init__(self, nome_icone, tinta="destaque", fundo="destaque_claro", lado=38,
                 redondo=False, pai=None):
        super().__init__(pai)
        self.nome_icone, self.tinta, self.fundo, self.redondo = nome_icone, tinta, fundo, redondo
        self.setFixedSize(lado, lado)
        TEMA.mudou.connect(self.update)

    def definir(self, nome_icone=None, tinta=None, fundo=None):
        self.nome_icone = nome_icone or self.nome_icone
        self.tinta = tinta or self.tinta
        self.fundo = fundo or self.fundo
        self.update()

    def paintEvent(self, e):
        p = pintor(self)
        r = QRectF(self.rect())
        raio = r.width() / 2 if self.redondo else r.width() * 0.3
        p.fillPath(caminho_arredondado(r, raio), cor(self.fundo))
        m = r.width() * 0.26
        pintar_icone(p, self.nome_icone, self.tinta, r.adjusted(m, m, -m, -m))


def rotulo(txt, papel=None, quebra=False):
    lb = QtWidgets.QLabel(txt)
    if papel:
        lb.setProperty("papel", papel)
    if quebra:
        lb.setWordWrap(True)
    return lb


def mudar_papel(lb, papel):
    lb.setProperty("papel", papel)
    lb.style().unpolish(lb)
    lb.style().polish(lb)


def caixa(direcao="h", *itens, espaco=10, margens=(0, 0, 0, 0)):
    lay = QtWidgets.QHBoxLayout() if direcao == "h" else QtWidgets.QVBoxLayout()
    lay.setSpacing(espaco)
    lay.setContentsMargins(*margens)
    for it in itens:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, QtWidgets.QLayout):
            lay.addLayout(it)
        elif isinstance(it, int):
            lay.addSpacing(it)
        else:
            lay.addWidget(it)
    return lay
