# Documentação Completa - AutoComanda

## 1. Visão Geral
O **AutoComanda** é uma aplicação desktop desenvolvida em Python para atuar como um motor em segundo plano nos PDVs que rodam o **Winthor (rotina 2075 - Frente de Caixa)**.

Seu principal objetivo é monitorar as vendas realizadas (pedidos faturados) em tempo real, filtrar itens específicos (como sanduíches, pratos feitos ou itens de padaria que exigem preparo imediato) e imprimir automaticamente uma **comanda de produção** em uma impressora térmica, sem que seja necessário interromper o fluxo do caixa com itens comuns (que não necessitam de preparo).

## 2. Estrutura de Diretórios
A estrutura do projeto está organizada de forma a facilitar tanto a execução em código-fonte quanto a distribuição compilada:

```text
autocomanda/
├── main.py                       # Ponto de entrada e motor principal (loop, conexão Oracle, filtro, impressão).
├── requirements.txt              # Dependências Python.
├── sql_exemplo_winthor.sql       # Consultas de referência para o Winthor.
├── AutoComanda.spec              # Arquivo de configuração de build do PyInstaller.
├── README.md                     # Documentação inicial com visão geral.
├── config/
│   └── config_comanda.json       # Arquivo de configuração principal (banco, impressora, filtros).
├── templates/
│   ├── __init__.py               # Definição do pacote de templates.
│   └── comanda_padrao.py         # Layout padrão (48 colunas) da comanda de impressão.
├── processados/
│   └── historico.db              # [Gerado Automaticamente] Banco SQLite que controla cupons já impressos.
└── autocomanda.log               # [Gerado Automaticamente] Log de execução.
```

## 3. Requisitos e Dependências
- **Linguagem**: Python 3.8+ (Foi incluído um instalador `python-3.8.10.exe` no histórico do repositório).
- **Sistema Operacional**: Windows (obrigatório, pois utiliza a biblioteca `win32print`).
- **Banco de Dados**: Oracle (Winthor). A conexão é feita via `oracledb` em modo *thin* (não exige instalação do Oracle Instant Client no PDV, mas possui a pasta `instantclient_19_24` caso o modo *thick* seja necessário para servidores legados).

**Bibliotecas Python (`requirements.txt`):**
- `oracledb`: Para conexão com o Oracle.
- `pywin32` (fornece `win32print`): Para comunicação direta com a fila de impressão do Windows.
- `pyinstaller` (opcional, para compilação).

## 4. Configuração (`config_comanda.json`)
A configuração da aplicação fica em `config/config_comanda.json`, para facilitar ajustes sem necessidade de recompilar.

| Parâmetro | Descrição |
|-----------|-----------|
| `oracle.usuario` | Usuário do banco Oracle (ex: `caixa`). |
| `oracle.senha` | Senha do banco Oracle. |
| `oracle.dsn` | Endereço do banco no formato Easy Connect (`host:porta/service_name`). |
| `impressora_destino` | Nome da impressora no Windows (ou `"Default"` para usar a impressora padrão do sistema). |
| `filtro_por` | Tipo de filtro usado. Opções: `"id_produto"`, `"secao"` ou `"departamento"`. |
| `itens_preparo` | Lista numérica dos códigos a serem monitorados (produtos, seções ou departamentos, dependendo do campo anterior). |
| `template` | Nome do arquivo de layout dentro da pasta `templates/` (sem o `.py`). |
| `intervalo_verificacao_segundos`| Tempo de espera entre cada consulta ao banco (padrão: 5 segundos). |
| `janela_busca_minutos` | Quantos minutos para trás o sistema verifica. O SQLite garante que não haja re-impressão (padrão: 60). |
| `encoding_impressora` | Codificação da impressora térmica. Usar `cp850` ou `cp860`. Se não funcionar os acentos, testar `latin1`. |

## 5. Arquitetura de Funcionamento (`main.py`)
O `main.py` roda num laço infinito (loop) com o seguinte ciclo de vida:

1. **Inicialização**: Lê as configurações, localiza o diretório do executável para salvar logs e carregar templates.
2. **Conexão**: Abre uma conexão com o banco local SQLite (`processados/historico.db`) e com o banco Oracle do Winthor.
3. **Loop de Monitoramento**:
   - Consulta pedidos fechados (`POSICAO = 'F'`) nas tabelas `PCPEDC` dentro da janela de tempo.
   - Para cada pedido, verifica no SQLite se ele já foi processado. Se sim, pula para o próximo.
   - Se é um pedido novo, busca seus itens na `PCPEDI` unida à `PCPRODUT`.
   - Aplica o filtro de preparo (`filtro_por`).
   - Se houver produtos de preparo, carrega dinamicamente a função `gerar_layout_comanda()` do template configurado (`comanda_padrao.py`).
   - Converte o texto gerado para `bytes` usando o encoding da impressora e despacha via `win32print` para o Spool do Windows na impressora destino.
   - Registra o pedido no SQLite como concluído para nunca mais imprimir.
   - Aguarda o intervalo de X segundos e repete.

## 6. Bancos de Dados
### 6.1. Oracle (Winthor)
As consultas utilizam as tabelas padronizadas do Winthor (`PCPEDC` para capa do pedido, `PCPEDI` para os itens e `PCPRODUT` para cadastro do produto). O campo chave para identificar a conclusão do pedido na rotina 2075 é `POSICAO = 'F'`.
*Atenção: verifique `sql_exemplo_winthor.sql` para garantir que o seu dicionário de dados específico utiliza essa mesma estrutura, pois customizações podem alterar campos.*

### 6.2. SQLite (Controle Local)
O arquivo `processados/historico.db` é criado na primeira execução. Ele mantém registro dos `NUMPED` (Número do Pedido) já avaliados.
Essa abordagem descentralizada permite que o serviço seja reiniciado, ou o PDV desligado em uma queda de energia, sem risco de perder o fio da meada ou imprimir cupons duplicados.

## 7. Como Compilar (Build)
Para transformar o projeto num executável fechado (`.exe`) para enviar aos computadores dos caixas:

1. Instale os requerimentos:
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   ```
2. Rode o comando de build do PyInstaller. Use `--console` em homologação para ver os logs saltando na tela, e troque para `--windowed` (para esconder a tela do prompt) quando for colocar em produção oficial.
   ```bash
   pyinstaller --onefile --name autocomanda --console main.py
   ```
3. Na pasta `dist/` gerada, estará o `autocomanda.exe`.

## 8. Implantação e Execução no PDV
Ao implantar no caixa, a estrutura em volta do `.exe` deve ser respeitada. Ou seja, você não envia só o `.exe`.

1. Copie o `autocomanda.exe` para o PDV (Ex: `C:\AutoComanda\`).
2. Copie as pastas `config/` e `templates/` para junto dele.
3. Crie a pasta vazia `processados/`.
4. Configure o `config_comanda.json` específico daquele caixa (com a impressora local).
5. Para inicialização automática: adicione um atalho do executável na pasta "Inicializar" do Windows do caixa, ou registre como serviço via **NSSM**, ou configure no Agendador de Tarefas. Acompanhe erros em produção através do arquivo `autocomanda.log`.
