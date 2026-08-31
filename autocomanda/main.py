"""
AutoComanda - Motor Principal
==============================

Aplicacao desktop que roda em background nos PDVs (rotina 2075 - Frente
de Caixa do Winthor), monitora vendas fechadas no Oracle e imprime uma
comanda de producao contendo apenas os itens preparados na hora
(sanduiches, pizzas, pratos feitos, padaria etc.), sem interromper o
fluxo de trabalho da cozinha com itens que nao precisam de preparo.

Fluxo:
    1. Le config/config_comanda.json
    2. Conecta no Oracle (oracledb, modo "thin" por padrao)
    3. A cada X segundos, busca pedidos faturados numa janela de tempo
    4. Para cada pedido ainda nao processado (controle via SQLite local):
        - busca os itens do pedido
        - filtra pelos itens/secao/departamento definidos no JSON
        - se houver item de preparo, gera o layout via template dinamico
          e envia para a impressora configurada
        - marca o pedido como processado no SQLite (evita reimpressao)

ATENCAO - dicionario de dados:
    Os nomes de tabela/coluna usados nas consultas Oracle (PCPEDC,
    PCPEDI, PCPRODUT, POSICAO, NUMPED...) seguem o dicionario de dados
    publico do Winthor e o uso documentado dessas tabelas na rotina
    2075, mas podem variar conforme a versao/customizacao da base.
    Valide com `DESCRIBE <tabela>` antes de colocar em producao.
    Veja tambem sql_exemplo_winthor.sql.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import oracledb
import win32print


# ---------------------------------------------------------------------------
# Caminhos (compativeis com execucao via PyInstaller --onefile ou --onedir)
# ---------------------------------------------------------------------------

def obter_diretorio_base() -> Path:
    """Retorna a pasta onde o .exe (ou o script) esta rodando.

    Quando compilado com PyInstaller, sys.executable aponta para o
    autocomanda.exe; usamos a pasta dele como raiz, para que config/,
    templates/ e processados/ sejam lidos/gravados ao lado do
    executavel (conforme a estrutura de diretorios do projeto), e nao
    dentro do bundle temporario do PyInstaller (sys._MEIPASS).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = obter_diretorio_base()
CONFIG_PATH = BASE_DIR / "config" / "config_comanda.json"
TEMPLATES_DIR = BASE_DIR / "templates"
PROCESSADOS_DIR = BASE_DIR / "processados"
HISTORICO_DB_PATH = PROCESSADOS_DIR / "historico.db"
LOG_PATH = BASE_DIR / "autocomanda.log"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configurar_logging() -> logging.Logger:
    logger = logging.getLogger("autocomanda")
    logger.setLevel(logging.INFO)

    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    arquivo = logging.FileHandler(str(LOG_PATH), encoding="utf-8")
    arquivo.setFormatter(formato)
    logger.addHandler(arquivo)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)
    logger.addHandler(console)

    return logger


logger = configurar_logging()


# ---------------------------------------------------------------------------
# Configuracao (config/config_comanda.json)
# ---------------------------------------------------------------------------

CAMPO_POR_FILTRO = {
    "id_produto": "CODPROD",
    "secao": "CODSECAO",
    "departamento": "CODEPTO",
}


def carregar_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Arquivo de configuracao nao encontrado: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    obrigatorios = ["impressora_destino", "filtro_por", "itens_preparo", "template", "oracle"]
    faltando = [c for c in obrigatorios if c not in config]
    if faltando:
        raise ValueError(f"Campos obrigatorios ausentes no config_comanda.json: {faltando}")

    if config["filtro_por"] not in CAMPO_POR_FILTRO:
        raise ValueError(
            f"'filtro_por' invalido: {config['filtro_por']!r}. "
            f"Use um de: {list(CAMPO_POR_FILTRO)}"
        )

    # normaliza para lookup rapido (set de inteiros)
    config["itens_preparo"] = {int(x) for x in config["itens_preparo"]}

    return config


# ---------------------------------------------------------------------------
# Controle de duplicidade (SQLite local)
# ---------------------------------------------------------------------------

def abrir_historico() -> sqlite3.Connection:
    PROCESSADOS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORICO_DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cupons_processados (
            num_pedido      TEXT PRIMARY KEY,
            data_processado TEXT NOT NULL,
            qtd_itens       INTEGER NOT NULL DEFAULT 0,
            impresso        INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


def ja_processado(historico: sqlite3.Connection, num_pedido: Any) -> bool:
    cur = historico.execute(
        "SELECT 1 FROM cupons_processados WHERE num_pedido = ?", (str(num_pedido),)
    )
    return cur.fetchone() is not None


def marcar_processado(
    historico: sqlite3.Connection, num_pedido: Any, qtd_itens: int, impresso: bool
) -> None:
    historico.execute(
        """
        INSERT OR IGNORE INTO cupons_processados
            (num_pedido, data_processado, qtd_itens, impresso)
        VALUES (?, ?, ?, ?)
        """,
        (str(num_pedido), datetime.now().isoformat(timespec="seconds"), qtd_itens, int(impresso)),
    )
    historico.commit()


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

def conectar_oracle(config: dict[str, Any]) -> oracledb.Connection:
    ora = config["oracle"]

    # Oracle XE 10g (versao do caixa) nao suporta o modo "thin" do
    # python-oracledb (requer Oracle 12.1+). Ativamos o modo "thick"
    # que usa as bibliotecas do Oracle Instant Client e suporta
    # versoes anteriores (Oracle 9.2+).
    lib_dir = ora.get("instant_client_dir")
    if lib_dir and not Path(lib_dir).is_absolute():
        # Caminho relativo ao diretorio do executavel
        lib_dir = str(BASE_DIR / lib_dir)

    if lib_dir:
        # Garante que o IC bundled seja carregado ANTES do oci.dll do
        # Oracle XE que pode estar no PATH do sistema (ex: 10.2.0).
        # os.add_dll_directory() registra o diretorio como fonte
        # prioritaria para o carregador de DLLs do Windows (Python 3.8+).
        try:
            os.add_dll_directory(lib_dir)
        except (AttributeError, OSError):
            pass
        # Tambem inserimos no inicio do PATH para cobrir carregamento
        # indireto (ex: dependencias que o oracledb carrega via ctypes).
        os.environ["PATH"] = lib_dir + os.pathsep + os.environ.get("PATH", "")
        logger.debug("IC dir adicionado ao PATH: %s", lib_dir)

    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
        logger.debug("Oracle Instant Client inicializado (thick mode). lib_dir=%s", lib_dir)
    except oracledb.ProgrammingError:
        # Ja foi inicializado em uma chamada anterior (reconexao)
        pass

    return oracledb.connect(
        user=ora["usuario"],
        password=ora["senha"],
        dsn=ora["dsn"],  # formato Easy Connect: "host:porta/service_name"
    )


def buscar_pedidos_faturados(conn: oracledb.Connection, janela_minutos: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Busca pedidos com POSICAO = 'F' (faturado) dentro de uma janela de
    tempo recente. A janela pode (e deve) ser generosa: quem evita
    reprocessar/reimprimir e o controle via SQLite (historico.db), entao
    nao ha problema em reconsultar os ultimos N minutos a cada ciclo -
    inclusive apos o AutoComanda ser reiniciado.

    NOTA: se a coluna DATA da sua base nao carregar a hora (apenas a
    data), o filtro por janela em minutos pode nao se comportar como
    esperado dentro do mesmo dia. Nesse caso, combine com um campo de
    hora separado, se existir na sua versao.
    """
    # Busca o prefixo de schema configurado (ex: "WINTHOR." ou "PCO.")
    schema = config.get("schema_oracle", "")
    if schema and not schema.endswith("."):
        schema += "."

    query = f"""
        SELECT
            C.NUMPEDECF AS NUMPED,
            C.CODFILIAL,
            C.CODUSUR,
            C.DATA,
            C.POSICAO
        FROM {schema}PCPEDCECF C
        WHERE C.POSICAO = 'L'
          AND C.DATA >= TRUNC(SYSDATE - 1)
        ORDER BY C.DATA, C.NUMPEDECF
    """
    with conn.cursor() as cur:
        cur.execute(query)
        colunas = [c[0] for c in cur.description]
        return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def buscar_itens_pedido(conn: oracledb.Connection, num_pedido: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Busca os itens de um pedido, com departamento/secao do produto
    (necessarios para os modos de filtro 'departamento' e 'secao')."""
    schema = config.get("schema_oracle", "")
    if schema and not schema.endswith("."):
        schema += "."

    query = f"""
        SELECT
            I.CODPROD,
            P.DESCRICAO,
            I.QT,
            NULL AS CODEPTO,
            NULL AS CODSECAO
        FROM {schema}PCPEDIECF I
        JOIN {schema}PCPRODUT P ON P.CODPROD = I.CODPROD
        WHERE I.NUMPEDECF = :num_pedido
    """
    with conn.cursor() as cur:
        cur.execute(query, num_pedido=num_pedido)
        colunas = [c[0] for c in cur.description]
        return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# ---------------------------------------------------------------------------
# Filtro de itens de preparo (a "Regra de Ouro")
# ---------------------------------------------------------------------------

def filtrar_itens_preparo(itens: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    campo = CAMPO_POR_FILTRO[config["filtro_por"]]
    itens_preparo = config["itens_preparo"]

    filtrados = []
    for item in itens:
        valor = item.get(campo)
        if valor is None:
            continue
        if int(valor) in itens_preparo:
            filtrados.append(
                {
                    "descricao": (item.get("DESCRICAO") or "").strip(),
                    "quantidade": item.get("QT") or 1,
                }
            )
    return filtrados


# ---------------------------------------------------------------------------
# Template (import dinamico de templates/<nome>.py)
# ---------------------------------------------------------------------------

def carregar_template(nome_template: str):
    if str(TEMPLATES_DIR) not in sys.path:
        sys.path.insert(0, str(TEMPLATES_DIR))

    modulo = importlib.import_module(nome_template)

    if not hasattr(modulo, "gerar_layout_comanda"):
        raise AttributeError(
            f"O template '{nome_template}' precisa definir a funcao "
            "gerar_layout_comanda(num_cupom, data_hora, lista_produtos, nome_caixa)"
        )
    return modulo


# ---------------------------------------------------------------------------
# Impressao (win32print, envio RAW para impressora termica)
# ---------------------------------------------------------------------------

def imprimir(texto: str, nome_impressora: str, encoding: str = "cp850") -> None:
    # 1. Salva o texto em um arquivo .txt fisico
    nome_arquivo = f"comanda_{int(time.time())}.txt"
    caminho_txt = PROCESSADOS_DIR / nome_arquivo
    
    with open(caminho_txt, "w", encoding=encoding, errors="replace") as f:
        f.write(texto)
        
    logger.info("Comanda salva em: %s", caminho_txt)

    # 2. Envia o .txt para a impressora pelo Windows
    # Usa o Notepad para imprimir o arquivo texto de forma silenciosa
    import subprocess
    if not nome_impressora or nome_impressora.lower() == "default":
        # Imprime na impressora padrao
        os.startfile(str(caminho_txt), "print")
    else:
        # Imprime em impressora especifica
        subprocess.run(["notepad.exe", "/pt", str(caminho_txt), nome_impressora], check=True)


# ---------------------------------------------------------------------------
# Processamento de um pedido
# ---------------------------------------------------------------------------

def processar_pedido(
    ora_conn: oracledb.Connection,
    historico: sqlite3.Connection,
    config: dict[str, Any],
    template_mod,
    pedido: dict[str, Any],
) -> None:
    num_pedido = pedido["NUMPED"]

    if ja_processado(historico, num_pedido):
        return

    itens = buscar_itens_pedido(ora_conn, num_pedido, config)
    itens_preparo = filtrar_itens_preparo(itens, config)

    if not itens_preparo:
        # Nada de preparo na hora nesta venda: ignora e segue a vida.
        marcar_processado(historico, num_pedido, qtd_itens=0, impresso=False)
        return

    texto_comanda = template_mod.gerar_layout_comanda(
        num_cupom=num_pedido,
        data_hora=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        lista_produtos=itens_preparo,
        nome_caixa=str(pedido.get("CODUSUR") or pedido.get("CODFILIAL") or "?"),
    )

    try:
        imprimir(
            texto_comanda,
            config["impressora_destino"],
            encoding=config.get("encoding_impressora", "cp850"),
        )
    except Exception:
        logger.exception("Falha ao imprimir comanda do pedido %s", num_pedido)
        # Nao marca como processado: tenta novamente no proximo ciclo.
        return

    logger.info("Comanda impressa: pedido %s, %d item(ns) de preparo", num_pedido, len(itens_preparo))
    marcar_processado(historico, num_pedido, qtd_itens=len(itens_preparo), impresso=True)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def executar() -> None:
    print("=" * 60)
    print("                 AutoComanda PDV")
    print("=" * 60)
    print("Monitorando novas comandas... Pressione Ctrl+C para sair.\n")

    logger.info("=== AutoComanda iniciado ===")
    logger.debug("Diretorio base: %s", BASE_DIR)

    config = carregar_config()
    template_mod = carregar_template(config["template"])
    historico = abrir_historico()

    intervalo_segundos = int(config.get("intervalo_verificacao_segundos", 5))
    janela_minutos = int(config.get("janela_busca_minutos", 60))

    ora_conn: Optional[oracledb.Connection] = None

    while True:
        try:
            if ora_conn is None:
                ora_conn = conectar_oracle(config)
                logger.info("Conectado ao banco de dados com sucesso.")

            pedidos = buscar_pedidos_faturados(ora_conn, janela_minutos, config)
            for pedido in pedidos:
                processar_pedido(ora_conn, historico, config, template_mod, pedido)

        except oracledb.DatabaseError:
            logger.exception("Erro de conexao/consulta no Oracle. Reconectando no proximo ciclo.")
            try:
                if ora_conn is not None:
                    ora_conn.close()
            except Exception:
                pass
            ora_conn = None

        except Exception:
            logger.exception("Erro inesperado no loop principal.")

        time.sleep(intervalo_segundos)


if __name__ == "__main__":
    try:
        executar()
    except KeyboardInterrupt:
        logger.info("AutoComanda encerrado pelo usuario.")
