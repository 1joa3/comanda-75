# AutoComanda

Monitor de vendas para o Winthor (rotina 2075 - Frente de Caixa) que
imprime automaticamente uma comanda de producao apenas para os itens
preparados na hora (sanduiches, pizzas, pratos feitos, padaria...),
sem interromper o fluxo da cozinha com itens que nao precisam de preparo.

## Estrutura (codigo-fonte)

```
autocomanda/
├── main.py                       # motor principal (loop, Oracle, filtro, impressao)
├── requirements.txt
├── sql_exemplo_winthor.sql       # consultas de referencia (leia o ATENCAO no arquivo)
├── config/
│   └── config_comanda.json
└── templates/
    ├── __init__.py
    └── comanda_padrao.py         # layout da comanda (48 colunas)
```

Depois do build com PyInstaller, a pasta fica assim ao lado do
`autocomanda.exe` (igual a estrutura que voce especificou):

```
dist/
├── autocomanda.exe
├── autocomanda.log               # criado automaticamente
├── config/
│   └── config_comanda.json
├── templates/
│   ├── __init__.py
│   └── comanda_padrao.py
└── processados/
    └── historico.db              # criado automaticamente (SQLite)
```

## Configuracao (config/config_comanda.json)

| Campo | Descricao |
|---|---|
| `oracle.usuario` / `oracle.senha` / `oracle.dsn` | Credenciais do Oracle. `dsn` no formato Easy Connect: `host:porta/service_name` |
| `impressora_destino` | Nome exato da impressora no Windows, ou `"Default"` para usar a impressora padrao do PDV |
| `filtro_por` | `"id_produto"`, `"secao"` ou `"departamento"` |
| `itens_preparo` | Lista de codigos numericos correspondentes ao `filtro_por` |
| `template` | Nome do arquivo (sem `.py`) dentro de `templates/` |
| `intervalo_verificacao_segundos` | Frequencia do loop (padrao: 5) |
| `janela_busca_minutos` | Quantos minutos para tras cada ciclo revarre (padrao: 60) — o SQLite evita reimpressao, entao pode ser generoso |
| `encoding_impressora` | Codificacao enviada a impressora termica (padrao: `cp850`; troque para `cp860` ou `latin1` se os acentos saírem errados na sua impressora) |

## Build (PyInstaller)

```bash
pip install -r requirements.txt
pip install pyinstaller

pyinstaller --onefile --name autocomanda --console main.py
```

Depois do build, copie manualmente `config/`, `templates/` e crie a
pasta `processados/` ao lado do `autocomanda.exe` gerado em `dist/` —
elas devem ficar FORA do executavel (nao use `--add-data` para essas
pastas), justamente para permitir editar o JSON e os templates sem
recompilar.

Use `--console` durante os testes (para ver os logs em tempo real) e
troque para `--windowed` quando for rodar oculto em producao — nesse
caso, acompanhe pelo `autocomanda.log`.

Para iniciar automaticamente com o PDV, registre o `.exe` como tarefa
agendada (Agendador de Tarefas do Windows) disparada na inicializacao,
ou rode como servico via NSSM.

## Pontos de atencao

- **Nomes de tabela/coluna do Winthor**: o `sql_exemplo_winthor.sql`
  usa PCPEDC/PCPEDI/PCPRODUT e o campo POSICAO = 'F', que e o padrao
  documentado do Winthor associado a rotina 2075 — mas confirme contra
  o dicionario de dados real da sua base (versao/customizacao mudam
  alguns campos) antes de ir para producao.
- **oracledb em modo thin**: por padrao a conexao nao exige Oracle
  Instant Client instalado no PDV, o que simplifica a distribuicao do
  .exe. So ative o modo thick (comentado em `main.py`) se precisar de
  algum recurso que o thin mode nao cubra, ou se o Oracle Server for
  anterior a 12.1.
- **Itens vendidos por peso**: o template ja lida com quantidades
  fracionadas (ex: 0.485 kg de pao), formatando sem zeros a esquerda.
- **Reimpressao apos queda de energia/reinicio**: como o controle de
  duplicidade e feito pelo SQLite (nao por timestamp em memoria), o
  AutoComanda pode reiniciar a qualquer momento sem reimprimir cupons
  ja processados.
- **win32print e Windows-only**: o codigo so roda (e so pode ser
  testado de ponta a ponta) em Windows, ja que win32print depende do
  pywin32.
