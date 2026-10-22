# Migrador de E-mails

Migra caixas de e-mail entre servidores IMAP, sem MailStore e sem instalar nada além do Python. Funciona como **aplicativo desktop** (com gráficos de andamento) ou pelo **terminal**.

- **Servidor → Servidor**: copia de um IMAP para outro.
- **Exportar backup**: salva as mensagens em arquivos `.eml`, uma pasta por pasta do e-mail.
- **Importar backup**: envia uma árvore de arquivos `.eml` para um servidor IMAP.
- **Várias contas**: migra uma lista de contas a partir de um CSV.

Preserva a estrutura de pastas, as marcações (lido, respondido…) e a data original das mensagens. **Nada é duplicado**: antes de copiar, compara Message-ID, data, assunto e remetente com o que já está no destino. Por isso dá para interromper e rodar de novo, e a migração continua de onde parou.

## Requisitos

- Python 3 (testado no 3.14), com o Tkinter que já vem no instalador oficial do Windows.
- Nenhuma biblioteca externa.

## Aplicativo desktop

```
pythonw migrador_gui.py
```

1. Escolha o tipo de migração.
2. Preencha servidor, usuário e senha de cada conta e clique em **Testar conexão**.
3. (Opcional) Clique em **Carregar pastas da origem** e desmarque as que não quiser migrar.
4. Clique em **Iniciar**.

A tela **Andamento** mostra as mensagens copiadas, as que já existiam, os erros, a velocidade de transferência, o resultado por pasta e o log detalhado. O botão **Parar** interrompe com segurança.

As configurações ficam salvas em `%APPDATA%\MigradorEmails\perfil.json`. **As senhas nunca são gravadas em disco.**

### Modo demonstração

Para conhecer o app sem ter contas de e-mail, use:

```
pythonw demo_gui.py
```

Ele abre o aplicativo conectado a dois servidores IMAP falsos em memória (`demo.origem` e `demo.destino`), com cerca de 1.000 mensagens de exemplo. Nada sai do computador.

## Terminal

```
python migrar_email.py imap2imap \
    --src-host imap.antigo.com --src-user a@x.com \
    --dst-host imap.novo.com   --dst-user a@x.com

python migrar_email.py export --src-host imap.antigo.com --src-user a@x.com --dir ./backup

python migrar_email.py import --dst-host imap.novo.com --dst-user a@x.com --dir ./backup
```

As senhas são pedidas sem aparecer na tela. Também podem vir das variáveis de ambiente `SRC_PASS` e `DST_PASS` ou das opções `--src-pass` e `--dst-pass`.

### Várias contas (CSV)

```
python migrar_email.py imap2imap --src-host imap.antigo.com --dst-host imap.novo.com --csv contas.csv
```

O `contas.csv` precisa ter as colunas `src_user,src_pass,dst_user,dst_pass`, com uma conta por linha.

### Outras opções

| Opção | Para que serve |
|---|---|
| `--pasta NOME` | Migra só esta pasta (pode repetir) |
| `--src-port` / `--dst-port` | Porta IMAP, se não for a padrão 993 |
| `--timeout 60` | Segundos sem resposta do servidor antes de dar erro |
| `--log migracao.log` | Arquivo de log |
| `--webhook URL` | Avisa o resultado no Slack, Discord ou Mattermost |
| `--notify-email` + `--smtp-*` | Envia o resultado por e-mail |

## Dicas

- **Gmail, Outlook e Yahoo** exigem uma *senha de app* em vez da senha normal quando a verificação em duas etapas está ativa.
- Para um primeiro teste com uma conta real, use **Exportar backup**: ele só lê o servidor e nunca altera nada nele.
- Para gerar um `.exe` que abre com duplo clique:
  ```
  pip install pyinstaller
  pyinstaller --onefile --windowed --name MigradorEmails migrador_gui.py
  ```
