# Migrador de E-mails

Migra caixas de e-mail entre servidores IMAP, sem MailStore e sem instalar nada além do Python. Funciona como **aplicativo desktop** (com gráficos de andamento) ou pelo **terminal**.

- **Servidor → Servidor**: copia de um IMAP para outro.
- **Exportar backup**: salva as mensagens em arquivos `.eml`, uma pasta por pasta do e-mail.
- **Importar backup**: envia uma árvore de arquivos `.eml` para um servidor IMAP.
- **Várias contas**: migra uma lista de contas a partir de um CSV.

Preserva a estrutura de pastas, as marcações (lido, respondido…) e a data original das mensagens. **Nada é duplicado**: antes de copiar, compara Message-ID, data, assunto e remetente com o que já está no destino. Por isso dá para interromper e rodar de novo, e a migração continua de onde parou.

## Baixar e usar (Windows 10/11)

Baixe o **[MigradorEmails.exe](https://github.com/schuabjp/migrador-de-emails/releases/latest/download/MigradorEmails.exe)** e salve numa pasta sua, por exemplo `Documentos\Migrador` ou a Área de Trabalho. Não use `Arquivos de Programas`, porque ali o programa não consegue se atualizar. Depois é só abrir com dois cliques: não precisa instalar o Python nem nada mais.

- **Na primeira vez, o Windows pode mostrar "O Windows protegeu o computador"**, porque o programa ainda não tem assinatura digital. Clique em **Mais informações** e depois em **Executar assim mesmo**.
- **Atualizações:** ao abrir, o programa verifica se há versão nova (e repete a cada 6 horas). Quando houver, ele mostra as novidades e um botão **Atualizar agora**. O download é conferido (SHA-256) e, ao reiniciar, o programa já abre na versão nova. Se você preferir fechar sem reiniciar, a atualização é instalada ao fechar. Configurações e histórico continuam.
- O que acontece nas atualizações fica registrado em `%APPDATA%\MigradorEmails\atualizacao.log`.

## Rodar pelo código

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

A tela **Andamento** mostra, em tempo real:

- os totais de mensagens copiadas, já existentes e com erro, o volume transferido e o tempo restante;
- o progresso da pasta atual e o progresso total;
- os gráficos de velocidade e de resultado por pasta;
- a tabela de pastas, com as pendentes, a em andamento e as concluídas;
- a aba **Problemas**, que junta todos os erros e avisos;
- o registro completo.

O botão **Parar** interrompe com segurança.

O **Histórico** guarda um relatório de cada execução, com o resultado de cada pasta, de cada conta e de cada problema. O relatório é salvo durante a migração, a cada pasta concluída, então nada se perde se o computador desligar. Ele pode ser exportado em CSV, que abre direto no Excel.

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

## Publicar uma versão nova

1. Faça o commit das mudanças e envie: `git push`.
2. Crie a tag da versão e envie:
   ```
   git tag v1.2.0
   git push origin v1.2.0
   ```

O GitHub Actions ([publicar.yml](.github/workflows/publicar.yml)) compila o `.exe` num Windows, confere se ele abre e cria a página da versão em [Releases](https://github.com/schuabjp/migrador-de-emails/releases), com as notas geradas a partir dos commits. Em poucos minutos, os programas da equipe mostram o aviso de atualização. Acompanhe a compilação na aba **Actions** do repositório.

Use números crescentes no formato `vX.Y.Z`. O programa só oferece versões maiores que a que está rodando.

Para gerar um `.exe` de teste na sua máquina:

```
pip install pyinstaller
python build.py 1.2.0
```

O executável sai em `dist\MigradorEmails.exe`. Os arquivos que o build usa são `build.py`, `versao.py`, `icone.ico` e `icone.png`.
