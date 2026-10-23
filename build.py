#!/usr/bin/env python3
"""
Gera o dist/MigradorEmails.exe com o PyInstaller.

    pip install -r requirements.txt pyinstaller
    python build.py 1.2.0

O GitHub Actions roda isto sozinho ao publicar uma versão (tag v1.2.0); aqui
serve para gerar um .exe de teste. Também grava dist/MigradorEmails.exe.sha256,
que o atualizador usa para conferir o download.

O .exe leva só a parte do Qt que o app usa: sem isso ele passaria de 70 MB e
demoraria bem mais para abrir (um .exe de arquivo único se descompacta a cada vez).
"""
import hashlib
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(RAIZ, "dist")
BUILD = os.path.join(RAIZ, "build")

# módulos que o app não usa (o PyInstaller os puxaria por dependências opcionais)
EXCLUIR = ["tkinter", "PIL", "numpy", "matplotlib", "OpenGL", "PySide6.QtNetwork",
           "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg", "PySide6.QtQml",
           "PySide6.QtQuick", "PyQt5", "PyQt6"]
# arquivos do Qt que ficam de fora: renderização por software, rede, PDF, formatos de
# imagem que o app não abre e as traduções de outros idiomas
LIXO = ["opengl32sw", "qdirect2d", "Qt6Network", "Qt6OpenGL", "Qt6Pdf", "Qt6Qml", "Qt6Quick",
        "Qt6Svg", "Qt6VirtualKeyboard", "qtvirtualkeyboard", "networkinformation", "/tls/",
        "/generic/", "qminimal", "qoffscreen", "qpdf", "qsvg", "qwebp", "qtiff", "qjpeg", "qgif",
        "qicns", "qtga", "qwbmp"]

SPEC = """
lixo = {lixo!r}
def fica(caminho):
    c = caminho.replace("\\\\", "/")
    if "/translations/" in c:
        return "qtbase_pt" in c  # só a tradução em português (menus de contexto etc.)
    if "qtawesome/fonts/" in c:
        return "phosphor" in c  # o app só usa os ícones Phosphor
    return not any(x in c for x in lixo)
a = Analysis([{script!r}], pathex=[{raiz!r}], datas={datas!r}, excludes={excluir!r})
a.binaries = [b for b in a.binaries if fica(b[0])]
a.datas = [d for d in a.datas if fica(d[0])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="MigradorEmails", console=False,
          upx=False, icon={icone!r}, version={info!r})
"""

INFO_WINDOWS = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('FileDescription', 'Migrador de E-mails'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'MigradorEmails'),
      StringStruct('OriginalFilename', 'MigradorEmails.exe'),
      StringStruct('ProductName', 'Migrador de E-mails'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"v?\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("uso: python build.py 1.2.0")
    versao = sys.argv[1].lstrip("vV")
    url = sys.argv[2] if len(sys.argv) > 2 else None  # só para testar o atualizador

    arq_versao = os.path.join(RAIZ, "versao.py")
    with open(arq_versao, encoding="utf-8") as f:
        original = f.read()
    gerado = re.sub(r'VERSAO = ".*"', f'VERSAO = "{versao}"', original)
    if url:
        gerado = re.sub(r'URL_ATUALIZACAO = ".*"', f'URL_ATUALIZACAO = "{url}"', gerado)

    os.makedirs(BUILD, exist_ok=True)
    info = os.path.join(BUILD, "versao_windows.txt")
    with open(info, "w", encoding="utf-8") as f:
        f.write(INFO_WINDOWS.format(v=versao, t=tuple(map(int, versao.split("."))) + (0,)))

    spec = os.path.join(BUILD, "MigradorEmails.spec")
    with open(spec, "w", encoding="utf-8") as f:
        f.write(SPEC.format(lixo=LIXO, script=os.path.join(RAIZ, "migrador_gui.py"), raiz=RAIZ,
                            datas=[(os.path.join(RAIZ, n), ".") for n in ("icone.png", "icone.ico")],
                            excluir=EXCLUIR, icone=os.path.join(RAIZ, "icone.ico"), info=info))

    try:  # a versão entra no .exe; o versao.py do repositório volta como estava
        with open(arq_versao, "w", encoding="utf-8") as f:
            f.write(gerado)
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--log-level", "WARN", "--distpath", DIST, "--workpath", BUILD, spec],
                       check=True, cwd=RAIZ)
    finally:
        with open(arq_versao, "w", encoding="utf-8") as f:
            f.write(original)

    exe = os.path.join(DIST, "MigradorEmails.exe")
    h = hashlib.sha256()
    with open(exe, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    with open(exe + ".sha256", "w", encoding="ascii") as f:
        f.write(f"{h.hexdigest()}  MigradorEmails.exe\n")
    print(f"\nPronto: {exe} (versão {versao}, {os.path.getsize(exe) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
