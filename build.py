#!/usr/bin/env python3
"""
Gera o dist/MigradorEmails.exe com o PyInstaller.

    pip install pyinstaller
    python build.py 1.2.0

O GitHub Actions roda isto sozinho ao publicar uma versão (tag v1.2.0); aqui
serve para gerar um .exe de teste. Também grava dist/MigradorEmails.exe.sha256,
que o atualizador usa para conferir o download.
"""
import hashlib
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(RAIZ, "dist")
BUILD = os.path.join(RAIZ, "build")

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

    try:  # a versão entra no .exe; o versao.py do repositório volta como estava
        with open(arq_versao, "w", encoding="utf-8") as f:
            f.write(gerado)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--windowed", "--name", "MigradorEmails",
            "--icon", os.path.join(RAIZ, "icone.ico"),
            "--add-data", f"{os.path.join(RAIZ, 'icone.png')}{os.pathsep}.",
            "--version-file", info,
            "--distpath", DIST, "--workpath", BUILD, "--specpath", BUILD,
            os.path.join(RAIZ, "migrador_gui.py"),
        ], check=True, cwd=RAIZ)
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
