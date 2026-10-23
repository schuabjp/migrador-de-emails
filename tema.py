"""
Tema visual do Migrador: paleta clara/escura, fontes, ícones e folha de estilo (QSS).

COR é um dicionário único, trocado no lugar quando o tema muda; os componentes
desenhados à mão leem as cores na hora de pintar, então basta pedir um repaint.
"""
import ctypes
import sys

from PySide6 import QtCore, QtGui, QtWidgets

CLARO = {
    "fundo": "#f4f4f2", "superficie": "#ffffff", "superficie2": "#f8f8f6",
    "borda": "#e6e6e1", "borda_forte": "#d4d4ce", "trilho": "#ebebe7", "grade": "#efefeb",
    "tinta": "#1a1b1e", "tinta2": "#55565b", "muda": "#8b8c90",
    "destaque": "#2a78d6", "destaque2": "#4ea1ff", "destaque_hover": "#2569bd",
    "destaque_press": "#1c5cab", "destaque_claro": "#e8f1fd", "destaque_tinta": "#1c5cab",
    "ok": "#1c9f2c", "ok2": "#43c35a", "ok_texto": "#1f7a2b", "ok_claro": "#e5f5e8",
    "aviso": "#d99a00", "aviso_texto": "#8f620a", "aviso_claro": "#fcf2da",
    "erro": "#d23c3c", "erro_texto": "#b02a2a", "erro_claro": "#fbe8e8",
    "roxo": "#7a5af5", "roxo_claro": "#efeafe",
    "lateral": "#17191e", "lateral_hover": "#22252c", "lateral_ativo": "#2c3039",
    "lateral_texto": "#d3d4d8", "lateral_mudo": "#7f838c", "lateral_borda": "#262930",
    "hover_linha": "#f5f8fd", "sombra": "#101828", "sombra_forca": "1.0",
}
ESCURO = {
    "fundo": "#101216", "superficie": "#191c22", "superficie2": "#1f2229",
    "borda": "#272b33", "borda_forte": "#363b45", "trilho": "#2a2e37", "grade": "#23262d",
    "tinta": "#e9eaee", "tinta2": "#b3b6bd", "muda": "#7f838c",
    "destaque": "#4b8ff0", "destaque2": "#6fb0ff", "destaque_hover": "#5c9cf3",
    "destaque_press": "#3b7ddb", "destaque_claro": "#1c2a3f", "destaque_tinta": "#8ab8ff",
    "ok": "#32b548", "ok2": "#5fd472", "ok_texto": "#6bd37c", "ok_claro": "#16301d",
    "aviso": "#e3a72b", "aviso_texto": "#f1c35f", "aviso_claro": "#33290f",
    "erro": "#e5534b", "erro_texto": "#ff8a80", "erro_claro": "#3a1b1b",
    "roxo": "#9b82ff", "roxo_claro": "#272044",
    "lateral": "#0b0c0f", "lateral_hover": "#17191e", "lateral_ativo": "#22252c",
    "lateral_texto": "#d3d4d8", "lateral_mudo": "#767a83", "lateral_borda": "#1b1d22",
    "hover_linha": "#1e2330", "sombra": "#000000", "sombra_forca": "2.2",
}
COR = dict(CLARO)
TEMAS = {"claro": "Claro", "escuro": "Escuro", "sistema": "Automático"}


def _familia(*opcoes):
    disponiveis = set(QtGui.QFontDatabase.families())
    return next((f for f in opcoes if f in disponiveis), opcoes[-1])


class _Fontes:
    """Segoe UI Variable (Windows 11) quando existe; senão a Segoe UI clássica."""
    texto = titulo = mono = "Segoe UI"

    def carregar(self):
        self.texto = _familia("Segoe UI Variable Text", "Segoe UI")
        self.titulo = _familia("Segoe UI Variable Display", "Segoe UI")
        self.mono = _familia("Cascadia Mono", "Consolas")
        _fontes.clear()


FONTES = _Fontes()
_fontes = {}


def fonte(tam=10, peso=400, titulo=False, mono=False, tabular=False):
    """Fonte do app (com cache: é pedida a cada quadro pintado). Não altere a devolvida."""
    k = (tam, peso, titulo, mono, tabular)
    if k not in _fontes:
        _fontes[k] = _criar_fonte(*k)
    return _fontes[k]


def _criar_fonte(tam, peso, titulo, mono, tabular):
    f = QtGui.QFont(FONTES.mono if mono else FONTES.titulo if titulo else FONTES.texto)
    f.setPointSizeF(tam)
    f.setWeight(QtGui.QFont.Weight(peso))
    f.setHintingPreference(QtGui.QFont.PreferNoHinting)
    if tabular:  # algarismos de largura igual: o número não "dança" enquanto conta
        try:
            f.setFeature(QtGui.QFont.Tag("tnum"), 1)
        except (AttributeError, TypeError):
            pass
    return f


def cor(chave, alfa=None):
    c = QtGui.QColor(COR.get(chave, chave))
    if alfa is not None:
        c.setAlphaF(alfa)
    return c


def misturar(a, b, t):
    """Cor entre a e b (0 = a, 1 = b); a e b são chaves do tema ou QColor."""
    a = a if isinstance(a, QtGui.QColor) else cor(a)
    b = b if isinstance(b, QtGui.QColor) else cor(b)
    t = max(0.0, min(1.0, t))
    return QtGui.QColor.fromRgbF(*(x + (y - x) * t for x, y in zip(a.getRgbF(), b.getRgbF())))


_icones = {}
_pixmaps = {}
_fonte_icones = None


def _phosphor():
    """Só a fonte Phosphor do qtawesome: carregar as 12 famílias de ícones atrasa a abertura."""
    global _fonte_icones
    if _fonte_icones is None:
        import qtawesome
        from qtawesome.iconic_font import IconicFont
        _fonte_icones = IconicFont(next(f for f in qtawesome._BUNDLED_FONTS if f[0] == "ph"))
    return _fonte_icones


def icone(nome, chave_cor="tinta2", **kw):
    """Ícone Phosphor (qtawesome), com cache por nome e cor."""
    c = chave_cor if isinstance(chave_cor, QtGui.QColor) else cor(chave_cor)
    k = (nome, c.name(QtGui.QColor.HexArgb), tuple(sorted(kw.items())))
    if k not in _icones:
        _icones[k] = _phosphor().icon(f"ph.{nome}", color=c, **kw)
    return _icones[k]


def pintar_icone(p, nome, chave_cor, ret):
    """Desenha o ícone centrado em ret (QRectF), nítido em qualquer escala de tela."""
    lado = int(min(ret.width(), ret.height()))
    c = chave_cor if isinstance(chave_cor, QtGui.QColor) else cor(chave_cor)
    dpr = p.device().devicePixelRatioF()
    k = (nome, c.rgba(), lado, dpr)
    pm = _pixmaps.get(k)
    if pm is None:  # desenhar o glifo é caro; o resultado fica guardado
        pm = _pixmaps[k] = icone(nome, c).pixmap(QtCore.QSize(lado, lado), dpr)
    x = ret.x() + (ret.width() - lado) / 2
    y = ret.y() + (ret.height() - lado) / 2
    p.drawPixmap(QtCore.QRectF(x, y, lado, lado), pm, QtCore.QRectF(pm.rect()))


class GerenciadorTema(QtCore.QObject):
    mudou = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.escolha = "claro"

    def escuro(self):
        if self.escolha == "sistema":
            return QtWidgets.QApplication.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark
        return self.escolha == "escuro"

    def aplicar(self, escolha=None):
        if escolha:
            self.escolha = escolha if escolha in TEMAS else "claro"
        COR.clear()
        COR.update(ESCURO if self.escuro() else CLARO)
        _icones.clear()
        _pixmaps.clear()
        app = QtWidgets.QApplication.instance()
        app.setPalette(paleta())
        app.setStyleSheet(qss())
        for w in app.topLevelWidgets():
            barra_titulo(w)
        self.mudou.emit()


TEMA = GerenciadorTema()


def paleta():
    p = QtGui.QPalette()
    for papel, chave in ((QtGui.QPalette.Window, "fundo"), (QtGui.QPalette.Base, "superficie"),
                         (QtGui.QPalette.AlternateBase, "superficie2"),
                         (QtGui.QPalette.Text, "tinta"), (QtGui.QPalette.WindowText, "tinta"),
                         (QtGui.QPalette.Button, "superficie"), (QtGui.QPalette.ButtonText, "tinta"),
                         (QtGui.QPalette.Highlight, "destaque"),
                         (QtGui.QPalette.HighlightedText, "#ffffff"),
                         (QtGui.QPalette.ToolTipBase, "superficie"),
                         (QtGui.QPalette.ToolTipText, "tinta"),
                         (QtGui.QPalette.PlaceholderText, "muda")):
        p.setColor(papel, cor(chave))
    p.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, cor("muda"))
    p.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, cor("muda"))
    return p


def barra_titulo(janela):
    """Windows 11: barra de título na cor do app (e escura no tema escuro)."""
    if sys.platform != "win32" or not isinstance(janela, QtWidgets.QWidget):
        return
    try:
        hwnd = int(janela.winId())
        dwm = ctypes.windll.dwmapi
        escuro = ctypes.c_int(1 if TEMA.escuro() else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(escuro), 4)  # modo escuro
        c = cor("fundo")
        corref = ctypes.c_int(c.red() | c.green() << 8 | c.blue() << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(corref), 4)  # cor da barra
    except (AttributeError, OSError):
        pass


def qss():
    c = COR
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {c['tinta']}; font-family: "{FONTES.texto}"; font-size: 10pt; }}
QMainWindow, QDialog, #pagina, #rolagem, #rolagem > QWidget > QWidget {{ background: {c['fundo']}; }}
QScrollArea {{ border: none; background: transparent; }}
QToolTip {{ background: {c['superficie']}; color: {c['tinta']}; border: 1px solid {c['borda_forte']};
           border-radius: 8px; padding: 6px 9px; }}

QLabel[papel="titulo"] {{ font-family: "{FONTES.titulo}"; font-size: 19pt; font-weight: 600; }}
QLabel[papel="sub"] {{ color: {c['muda']}; font-size: 10pt; }}
QLabel[papel="mono"] {{ color: {c['muda']}; font-family: "{FONTES.mono}"; font-size: 9.5pt; }}
QLabel[papel="passo"] {{ color: {c['tinta2']}; font-weight: 600; font-size: 10pt; }}
QLabel[papel="cartao_titulo"] {{ font-family: "{FONTES.titulo}"; font-size: 12pt; font-weight: 600; }}
QLabel[papel="secao"] {{ font-family: "{FONTES.titulo}"; font-size: 12.5pt; font-weight: 600; }}
QLabel[papel="mudo"] {{ color: {c['muda']}; font-size: 9pt; }}
QLabel[papel="rotulo"] {{ color: {c['tinta2']}; font-size: 9pt; font-weight: 500; }}
QLabel[papel="rodape"] {{ color: {c['tinta2']}; }}
QLabel[papel="ok"] {{ color: {c['ok_texto']}; font-size: 9pt; }}
QLabel[papel="erro"] {{ color: {c['erro_texto']}; font-size: 9pt; }}

QLineEdit, QComboBox {{
    background: {c['superficie']}; border: 1px solid {c['borda_forte']}; border-radius: 10px;
    padding: 0 12px; min-height: 36px; selection-background-color: {c['destaque']};
    selection-color: #ffffff;
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {misturar('borda_forte', 'destaque', .45).name()}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border: 2px solid {c['destaque']}; padding: 0 11px; }}
QLineEdit:disabled {{ color: {c['muda']}; background: {c['superficie2']}; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: none; width: 0; }}
QComboBox QAbstractItemView {{
    background: {c['superficie']}; border: 1px solid {c['borda_forte']}; border-radius: 10px;
    padding: 4px; selection-background-color: {c['destaque_claro']}; selection-color: {c['tinta']};
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; border-radius: 6px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {c['borda_forte']}; border-radius: 4px; min-height: 36px;
                              margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muda']}; margin: 0 1px; border-radius: 5px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {c['borda_forte']}; border-radius: 4px; min-width: 36px;
                                margin: 2px 0; }}
QScrollBar::handle:horizontal:hover {{ background: {c['muda']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; width: 0; height: 0; }}

QTableView {{ background: transparent; border: none; gridline-color: transparent;
              selection-background-color: transparent; selection-color: {c['tinta']}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: transparent; color: {c['muda']}; border: none;
    border-bottom: 1px solid {c['borda']}; padding: 0 14px; font-size: 8.5pt; font-weight: 600;
}}
QPlainTextEdit#log {{ font-family: "{FONTES.mono}"; font-size: 9pt; }}
QPlainTextEdit, QTextEdit {{ background: transparent; border: none;
                             selection-background-color: {c['destaque']}; selection-color: #fff; }}
QMenu {{ background: {c['superficie']}; border: 1px solid {c['borda_forte']}; border-radius: 10px;
         padding: 5px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['destaque_claro']}; }}
"""
