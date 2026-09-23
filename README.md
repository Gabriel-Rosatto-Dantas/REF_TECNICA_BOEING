# VERIFICAR REF TECNICA - Boeing Toolbox

Automacao em Python para consultar Part Numbers (PNs) no Boeing Toolbox, localizar a documentacao tecnica disponivel, selecionar o documento PDF conforme uma ordem de prioridade e baixar a revisao/publicacao mais recente diretamente para a pasta local `downloads`.

O projeto foi desenvolvido para trabalhar com a planilha Google Sheets **VERIFICAR REF TECNICA**, utilizando a aba **Boeing**.

---

## Objetivo

O programa automatiza o processo de verificacao de referencias tecnicas dos PNs Boeing:

1. conecta-se ao Google Sheets por uma **Service Account**;
2. le os PNs da aba `Boeing`;
3. ignora registros ja processados conforme as regras do `config_boeing.ini`;
4. abre o Boeing Toolbox;
5. realiza o login Boeing;
6. realiza o login Microsoft;
7. aguarda o **MFA Microsoft manualmente**;
8. seleciona **Login with Authy App**;
9. aguarda a entrada no Boeing Toolbox;
10. pesquisa cada PN pela interface real do Toolbox;
11. captura a chamada oficial da API `/search/data-unit-contents` gerada pelo proprio Boeing;
12. valida que a resposta capturada corresponde ao PN atualmente processado;
13. identifica os documentos disponiveis;
14. filtra os PDFs elegiveis;
15. aplica a prioridade **CMM -> GPSD -> IPL -> AMM**;
16. seleciona a publicacao/revisao mais recente dentro do tipo escolhido;
17. abre o viewer apenas para descobrir o `contentObjectId`/endpoint de conteudo;
18. baixa o PDF diretamente pelo endpoint `contentFile`;
19. salva o arquivo na pasta `downloads` sem criar subpastas;
20. atualiza `STATUS` e `DETALHE_STATUS` na planilha.

---

## Estrutura do projeto

```text
REF_TECNICA_BOEING_PLANNING/
│
├── app.py
├── config_boeing.ini
├── credentials.json
├── requirements.txt
│
├── downloads/
│   └── PDFs baixados
│
├── boeing_debug/
│   └── arquivos de diagnostico, quando utilizados
│
└── boeing_automacao.log
```

### Arquivos principais

| Arquivo | Finalidade |
|---|---|
| `app.py` | Codigo principal da automacao |
| `config_boeing.ini` | Configuracoes de acesso, timeouts e processamento |
| `credentials.json` | Credenciais da Google Service Account |
| `requirements.txt` | Dependencias Python |
| `downloads/` | Destino de todos os PDFs |
| `boeing_automacao.log` | Log persistente da execucao |

---

## Requisitos

### Sistema

- Windows 10/11
- Python 3.10 ou superior
- Google Chrome instalado
- acesso corporativo ao Boeing Toolbox
- acesso a conta Microsoft/LATAM utilizada pelo Boeing Toolbox
- celular/dispositivo para concluir o MFA Microsoft/Authy
- acesso a internet/rede corporativa conforme exigido pelo ambiente Boeing

### Python

O projeto foi utilizado em Python 3.13.

---

## Instalação

Recomenda-se utilizar um ambiente virtual.

### 1. Criar o ambiente virtual

No terminal do VS Code:

```powershell
python -m venv venv
```

### 2. Ativar o ambiente virtual

PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

Caso o PowerShell bloqueie a execucao do script de ativacao, ajuste a politica de execucao de acordo com as regras da sua maquina corporativa ou ative o ambiente por outro terminal.

### 3. Instalar as dependencias

```powershell
pip install -r requirements.txt
```

Caso o arquivo de requisitos seja especifico do projeto Boeing, tambem pode ser usado:

```powershell
pip install -r requirements_boeing.txt
```

---

## Google Sheets

A automacao utiliza uma **Google Service Account**, nao `gspread.oauth()`.

O arquivo deve se chamar:

```text
credentials.json
```

e ficar na mesma pasta do `app.py`, salvo se outro caminho for configurado em `config_boeing.ini`.

A planilha deve estar compartilhada com o e-mail da Service Account.

No projeto utilizado durante o desenvolvimento, a Service Account aparece como:

```text
ia-cargo-heroes@ia-cargo-heroes.iam.gserviceaccount.com
```

O script informa esse e-mail no terminal ao iniciar para permitir a conferencia da conta utilizada.

> **Importante:** nunca versionar `credentials.json` no Git.

---

## Configuracao

Todas as configuracoes operacionais ficam no arquivo:

```text
config_boeing.ini
```

A versao atual usada pelo projeto contem as secoes:

```ini
[BOEING]
[GOOGLE]
[SELENIUM]
[BOEING_API]
[PROCESSING]
[PATHS]
```

### Exemplo de configuracao

```ini
[BOEING]
home_url = https://toolbox.boeing.com/webui/Home
email = gabriel.dantas@latam.com
password =

[GOOGLE]
credentials_file = credentials.json
sheet_url = https://docs.google.com/spreadsheets/d/17tXzBd-mVXk9BjdrlUqltmLrEPPHmr9MzlNmH-3-dco/edit
sheet_tab = Boeing

[SELENIUM]
headless = false
page_load_strategy = none
page_timeout = 30
element_timeout = 60
microsoft_2fa_timeout = 600
aviation_id_timeout = 120
toolbox_ready_timeout = 600
maximize_window = true
ignore_certificate_errors = true
post_login_wait = 2

[BOEING_API]
search_path = /search/data-unit-contents
content_file_path = /product/fileRepo/contentFile/{content_object_id}
search_size = 50
exact_match = true

[PROCESSING]
update_sheet = true
process_only_blank_status = true
retry_statuses = ERRO,SEM PDF
max_items = 3
delay_between_items = 0.3
download_timeout = 180
update_link_pdf = false

[PATHS]
download_dir = downloads
debug_dir = boeing_debug
log_file = boeing_automacao.log
```

### `max_items`

Controla quantos PNs serao processados em uma execucao.

Para testes:

```ini
max_items = 3
```

Para processamento completo:

```ini
max_items = 0
```

`0` significa processar toda a fila pendente.

### `process_only_blank_status`

Com:

```ini
process_only_blank_status = true
```

o programa ignora linhas que ja possuem um status final, exceto os status definidos em `retry_statuses`.

### `retry_statuses`

Os status abaixo podem ser novamente processados:

```ini
retry_statuses = ERRO,SEM PDF
```

Isso permite reprocessar registros que falharam em uma execucao anterior.

---

## Fluxo de autenticacao

### Boeing

O script preenche:

- Email / User ID Boeing
- NEXT

### Microsoft

Depois o fluxo continua na tela da Microsoft:

- email
- Avancar
- senha
- Entrar

### MFA Microsoft

O MFA **nao e automatizado**.

Depois de enviar usuario e senha, o programa aguarda a conclusao do MFA no dispositivo do usuario.

O timeout e definido por:

```ini
microsoft_2fa_timeout = 600
```

ou seja, 10 minutos na configuracao padrao.

### Authy Boeing

Quando a tela Boeing Aviation ID aparece, o script seleciona:

```text
Login with Authy App
```

e aciona:

```text
NEXT
```

Depois aguarda a entrada no Boeing Toolbox.

---

## Pesquisa dos PNs

A pesquisa nao e feita por um `POST` manual montado pelo script.

O programa utiliza a interface real do Boeing Toolbox para disparar a pesquisa. A propria aplicacao Angular cria a chamada:

```text
POST /search/data-unit-contents
```

Essa abordagem e importante porque o Toolbox controla os cabecalhos e os tokens de autenticacao da sessao.

A automacao captura a chamada oficial no navegador e valida o campo `terms` para garantir que a resposta realmente pertence ao PN em processamento.

Por exemplo, se o programa estiver processando:

```text
0FL1200A06G01
```

uma chamada antiga referente a:

```text
725177
```

nao pode ser utilizada.

Isso evita um problema que ocorreu durante o desenvolvimento, em que a resposta de uma pesquisa anterior era interpretada como resultado do PN atual.

---

## Selecao do documento

A prioridade atual e:

```text
1. CMM
2. GPSD
3. IPL
4. AMM
```

Portanto, caso o PN possua simultaneamente CMM, GPSD, IPL e AMM, o programa utiliza o CMM.

Se nao existir CMM elegivel, procura GPSD. Depois IPL. Por ultimo AMM.

### PDF

O documento precisa ser identificado como PDF pelos dados retornados pelo Boeing.

A rotina tambem verifica a assinatura do arquivo baixado para garantir que o conteudo inicia como um PDF valido.

---

## Selecao da revisao mais recente

Quando existem varias versoes do mesmo tipo, o programa compara principalmente:

1. data de publicacao/emissao;
2. nivel de revisao.

Assim, entre varias revisoes de um CMM, o programa procura a versao mais recente disponivel de acordo com os metadados retornados pelo Toolbox.

Exemplo conceitual:

```text
CMM Rev 27 | 2025
CMM Rev 28 | 2025
CMM Rev 31 | 2026
```

Nesse cenario, a revisao de maior data/publicacao e nivel de revisao e selecionada.

---

## Download via API

Depois que o documento e selecionado, o programa utiliza o viewer apenas como parte da descoberta do identificador necessario para o conteudo.

O download final e feito diretamente pelo endpoint:

```text
/product/fileRepo/contentFile/{content_object_id}
```

A vantagem e que o PDF nao depende da renderizacao visual do PDF.js para ser salvo.

O arquivo e baixado em streaming, evitando carregar documentos grandes inteiros em memoria.

---

## Pasta de downloads

Todos os arquivos sao gravados diretamente em:

```text
downloads/
```

O programa **nao cria uma pasta para cada PN**.

Os nomes seguem um padrao semelhante a:

```text
0FL1200A06G01__CMM__CMM-25-46-01-S5065-22414-22448__Rev_031__2026-06-01.pdf
```

Isso facilita localizar os documentos depois e manter todos os PDFs em um unico diretorio.

---

## Google Sheets - colunas utilizadas

A aba configurada deve ter este cabecalho:

```text
Part Number | STATUS | DETALHE_STATUS | LINK_PDF
```

O script le o PN da coluna `Part Number`.

Por padrao, atualiza:

- `STATUS`
- `DETALHE_STATUS`

A coluna `LINK_PDF` permanece sem alteracao quando:

```ini
update_link_pdf = false
```

Caso futuramente seja necessario preencher a coluna `LINK_PDF`, essa opcao pode ser habilitada depois que houver uma URL apropriada para os arquivos.

---

## Status utilizados

### `PDF BAIXADO`

Documento PDF elegivel encontrado e baixado com sucesso.

O detalhe normalmente registra:

- tipo do documento;
- numero do documento;
- revisao;
- data de publicacao;
- tamanho do arquivo;
- nome original do arquivo, quando disponivel.

### `SEM PDF`

Nenhum documento PDF elegivel foi localizado entre os tipos aceitos pelo projeto.

### `ERRO`

Ocorreu uma falha durante a pesquisa, descoberta do documento, descoberta do endpoint ou download.

---

## Exemplo de terminal

A saida normal e propositalmente compacta:

```text
13:52:01 | INFO    | Iniciando VERIFICAR REF TECNICA - Boeing
13:52:02 | OK      | Google Service Account: ia-cargo-heroes@ia-cargo-heroes.iam.gserviceaccount.com
13:52:04 | OK      | Google Sheets conectado | aba=Boeing | PNs=396
13:52:04 | OK      | Fila preparada | 396 PN(s)
13:52:06 | OK      | Chrome iniciado
13:52:15 | OK      | Email Boeing enviado
13:52:21 | OK      | Email Microsoft enviado
13:52:22 | OK      | Senha Microsoft enviada
13:52:22 | 2FA     | Conclua o MFA Microsoft manualmente. Timeout=600s
13:52:42 | OK      | Microsoft MFA concluido
13:52:43 | OK      | Authy enviado
13:52:53 | OK      | Boeing Toolbox carregado
13:52:53 | INFO    | [1/396] PN 00495-109-000
13:53:05 | OK      | 00495-109-000 | CMM | ...
```

O objetivo dos logs de producao e mostrar apenas informacoes uteis para acompanhamento.

---

## Log em arquivo

O arquivo padrao e:

```text
boeing_automacao.log
```

Ele fica no diretorio do projeto, salvo se outro caminho for configurado.

Erros detalhados sao registrados no log sem deixar o terminal excessivamente poluido.

---

## Execucao

Com o ambiente virtual ativado:

```powershell
python -u app.py
```

O navegador sera aberto porque, por padrao:

```ini
headless = false
```

Isso e necessario para o usuario conseguir concluir o MFA Microsoft e o fluxo Authy.

---

## Primeira execucao recomendada

Antes de processar toda a fila, recomenda-se testar com poucos PNs:

```ini
max_items = 3
```

Depois de validar os downloads e os status da planilha:

```ini
max_items = 0
```

---

## Exemplo de um teste conhecido

Durante o desenvolvimento, o PN abaixo foi utilizado como teste:

```text
0FL1200A06G01
```

A pesquisa retornou diversos documentos e o processo conseguiu localizar um CMM, descobrir o endpoint `contentFile` e baixar o PDF.

Esse tipo de teste e util para validar futuras alteracoes no projeto antes de executar a fila completa.

---

## Troubleshooting

### Erro ao abrir o Chrome

Verifique se o Google Chrome esta instalado e se o executavel esta em um dos caminhos padrao do Windows.

### Erro `credentials.json nao encontrado`

Confirme se:

```text
credentials.json
```

esta na pasta do projeto ou se o caminho configurado em `[GOOGLE]` esta correto.

### A Service Account consegue autenticar, mas nao abre a planilha

Compartilhe a planilha com o e-mail da Service Account.

### Erro durante Microsoft MFA

O MFA e manual. Verifique se o dispositivo esta disponivel e conclua a aprovacao antes do timeout configurado.

### Erro `403` relacionado a CSRF

A busca nao deve ser reconstruida com `fetch()` manual. A versao atual utiliza a interface real do Boeing para disparar a chamada oficial e capturar a requisicao autenticada.

### O PN aparece como `SEM PDF`, mas existe documento no Boeing

Execute primeiro com um unico PN conhecido.

Confira:

```text
boeing_automacao.log
```

e, para diagnostico temporario, habilite a estrategia de debug usada durante a investigacao da API.

### O PDF nao foi baixado

Verifique:

- se o documento e realmente PDF;
- se o endpoint `contentFile` foi localizado;
- se a sessao Boeing ainda esta autenticada;
- se existe espaco em disco;
- se o timeout de download e suficiente para documentos grandes.

---

## Seguranca

Nunca envie ou versiona estes arquivos:

```text
credentials.json
config_boeing.ini
boeing_automacao.log
```

Dependendo da configuracao, `config_boeing.ini` pode conter credenciais ou outros dados de acesso.

Uma regra minima para `.gitignore` e:

```gitignore
venv/
__pycache__/
*.pyc
credentials.json
config_boeing.ini
boeing_automacao.log
boeing_debug/
downloads/
```

Se a senha for armazenada no `config_boeing.ini`, o arquivo deve ser tratado como informacao sensivel.

---

## Boas praticas de operacao

- use `max_items = 1` ou `3` para testar alteracoes;
- confira um PDF baixado antes de liberar a fila completa;
- mantenha `headless = false` para os fluxos com MFA manual;
- nao altere a logica de autenticacao sem validar novamente a captura da API;
- nao reutilize respostas de API de um PN diferente;
- mantenha todos os PDFs em `downloads/` para facilitar conferencia;
- faca backup da planilha antes de grandes execucoes se houver necessidade operacional.

---

## Dependencias principais

O projeto utiliza, entre outras, as seguintes bibliotecas:

- `selenium` para automacao do navegador;
- `gspread` para acesso ao Google Sheets;
- `google-auth` para autenticacao da Service Account;
- `requests` para download dos PDFs;
- `urllib3` para comunicacao HTTP e controle de avisos TLS.

A lista oficial deve ser mantida no `requirements.txt`.

---

## Manutencao

As configuracoes que normalmente precisam ser alteradas ficam no `config_boeing.ini`, principalmente:

```text
[BOEING]
[SELENIUM]
[PROCESSING]
[PATHS]
```

O codigo do `app.py` concentra a logica de integracao com o Boeing e deve ser alterado somente quando houver necessidade de adaptar o fluxo da aplicacao.

Como o Boeing Toolbox e uma aplicacao web corporativa, elementos HTML, rotas internas ou endpoints podem mudar ao longo do tempo. Em caso de mudanca no sistema, recomenda-se primeiro reproduzir manualmente a operacao no navegador e capturar novamente a chamada de rede antes de alterar o downloader.

---

## Historico resumido do desenvolvimento

### Etapa 1 - Login

Foi implementado:

- login Boeing;
- login Microsoft;
- MFA Microsoft manual;
- selecao `Login with Authy App`;
- entrada no Toolbox.

### Etapa 2 - Descoberta da API de pesquisa

Foi identificado o endpoint:

```text
POST /search/data-unit-contents
```

### Etapa 3 - Descoberta do PDF

Foi identificado o fluxo do viewer e o endpoint de conteudo:

```text
/product/fileRepo/contentFile/{content_object_id}
```

### Etapa 4 - Selecao de documento

Foi implementada a prioridade:

```text
CMM -> GPSD -> IPL -> AMM
```

com selecao da versao mais recente conforme os metadados retornados.

### Etapa 5 - Operacao em lote

O processamento passou a utilizar os PNs da aba `Boeing` da planilha e gravar os resultados no Sheets.

---

## Licenca e uso

Este projeto foi desenvolvido para uso interno e integracao com sistemas corporativos. Antes de distribuir ou reutilizar o codigo fora do ambiente corporativo, verifique as politicas de seguranca, acesso e uso aplicaveis ao Boeing Toolbox, a conta Microsoft e ao Google Workspace.

---

## Autor / manutencao

Projeto: **VERIFICAR REF TECNICA - Boeing Toolbox**

Arquivo principal:

```text
app.py
```

Configuracao:

```text
config_boeing.ini
```

Planilha:

```text
VERIFICAR REF TECNICA
```

Aba:

```text
Boeing
```
