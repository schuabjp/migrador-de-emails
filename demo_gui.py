#!/usr/bin/env python3
"""
Modo demonstração do Migrador de E-mails.

Abre o aplicativo normal, mas conectado a dois servidores IMAP FALSOS em
memória — dá para testar todas as telas sem ter contas de e-mail reais.
Nada sai do computador; ao fechar, tudo é descartado.

    python demo_gui.py

Servidores de teste (já vêm preenchidos):
    demo.origem   -> caixa com ~1.000 mensagens em 5 pastas
    demo.destino  -> já tem parte da pasta "Enviados" (para ver o "já existiam")
Qualquer usuário/senha funciona; use a senha "errada" para ver um login recusado.
"""
import os
import random
import sys
import tempfile
import time

# perfil e log da demonstração ficam separados dos de verdade
os.environ["APPDATA"] = os.path.join(tempfile.gettempdir(), "MigradorEmails-demo")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrar_email as me  # noqa: E402
import migrador_gui as gui  # noqa: E402

ATRASO = 0.02  # segundos por mensagem: lento o bastante para acompanhar os gráficos


def mensagem(i, pasta):
    corpo = b"Lorem ipsum dolor sit amet. " * random.randint(150, 4000)
    return (f"Message-ID: <{pasta}-{i}@demo>\r\n"
            f"Date: Mon, 1 Jan 2024 10:{i // 60 % 60:02d}:{i % 60:02d} +0000\r\n"
            f"Subject: Mensagem de teste {i}\r\nFrom: cliente{i % 7}@exemplo.com\r\n"
            f"\r\n").encode() + corpo


def sem_aspas(nome):
    return nome.strip('"').replace('\\"', '"').replace("\\\\", "\\")


class ImapFalso:
    """Imita só o pedaço do imaplib.IMAP4_SSL que o motor usa."""

    def __init__(self, pastas):
        self.pastas, self.sel = pastas, None

    def login(self, user, pwd):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]

    def list(self):
        return "OK", [f'(\\HasNoChildren) "/" "{n}"'.encode() for n in self.pastas]

    def subscribe(self, nome):
        return "OK", [b""]

    def create(self, nome):
        self.pastas.setdefault(sem_aspas(nome), [])
        return "OK", [b""]

    def status(self, nome, _):
        return "OK", [f"{nome} (MESSAGES {len(self.pastas[sem_aspas(nome)])})".encode()]

    def select(self, nome, readonly=False):
        if sem_aspas(nome) not in self.pastas:
            return "NO", [b"pasta inexistente"]
        self.sel = sem_aspas(nome)
        return "OK", [str(len(self.pastas[self.sel])).encode()]

    def uid(self, comando, *args):
        msgs = self.pastas[self.sel]
        if comando == "search":
            return "OK", [" ".join(str(i + 1) for i in range(len(msgs))).encode()]
        resp = []
        for uid in map(int, args[0].split(",")):
            raw = msgs[uid - 1]
            if "HEADER.FIELDS" in args[1]:
                cab = raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
                resp += [(f"{uid} (UID {uid} RFC822.SIZE {len(raw)} "
                          f"BODY[HEADER.FIELDS (X)] {{{len(cab)}}}".encode(), cab), b")"]
            else:
                resp += [(f'{uid} (UID {uid} FLAGS (\\Seen) INTERNALDATE '
                          f'"01-Jan-2024 10:00:00 +0000" BODY[] {{{len(raw)}}}'.encode(), raw), b")"]
        time.sleep(ATRASO * 2)
        return "OK", resp

    def append(self, nome, flags, data, raw):
        time.sleep(ATRASO * random.uniform(0.3, 2))
        if b"Subject: Mensagem de teste 113\r\n" in raw:  # uma mensagem "rejeitada"
            return "NO", [b"mensagem rejeitada pelo servidor"]
        self.pastas.setdefault(sem_aspas(nome), []).append(raw)
        return "OK", [b""]


def montar_servidores():
    random.seed(42)
    origem = {"INBOX": [mensagem(i, "inbox") for i in range(600)],
              "Enviados": [mensagem(i, "enviados") for i in range(250)],
              "Lixo Eletr&APQ-nico": [mensagem(i, "lixo") for i in range(40)],
              "Clientes/2024": [mensagem(i, "clientes") for i in range(150)],
              "Rascunhos": []}
    destino = {"INBOX": [], "Enviados": origem["Enviados"][:100]}
    return {"demo.origem": origem, "demo.destino": destino}


SERVIDORES = montar_servidores()


def conectar_falso(self, tentativas=3):
    if self.host not in SERVIDORES:
        raise RuntimeError(f"servidor {self.host} não existe no modo demonstração "
                           f"(use demo.origem ou demo.destino)")
    if self.pwd == "errada":
        raise RuntimeError(f"login recusado em {self.host} para {self.user} "
                           "(usuário/senha errados ou acesso bloqueado)")
    time.sleep(0.4)  # parece uma conexão de verdade
    return ImapFalso(SERVIDORES[self.host])


me.Conta.conectar = conectar_falso


def main():
    app = gui.App()
    app.titulo_base = "Migrador de E-mails — MODO DEMONSTRAÇÃO (servidores falsos)"
    app.title(app.titulo_base)
    for k, v in {"src_host": "demo.origem", "src_user": "voce@antigo.com", "src_pass": "123",
                 "dst_host": "demo.destino", "dst_user": "voce@novo.com", "dst_pass": "123"}.items():
        if not app.v[k].get():
            app.v[k].set(v)
    app.mainloop()


if __name__ == "__main__":
    main()
