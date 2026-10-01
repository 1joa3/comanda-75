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
    2. Conecta no Oracle (oracledb em modo "thick", via Oracle Instant
       Client, para suportar o Oracle XE 10g do caixa)
    3. A cada X segundos, busca os pedidos liberados do dia (PCPEDCECF)
    4. Para cada pedido ainda nao processado (controle via SQLite local):
        - busca os itens do pedido (PCPEDIECF)
        - filtra pelos itens/secao/departamento definidos no JSON
        - se houver item de preparo, gera o layout via template dinamico
          e envia para a impressora configurada
        - marca o pedido como processado no SQLite (evita reimpressao)

ATENCAO - dicionario de dados:
    Os nomes de tabela/coluna usados nas consultas Oracle (PCPEDCECF,
    PCPEDIECF, PCPRODUT, PCEMPR, POSICAO, NUMPEDECF...) seguem o
    dicionario de dados do Winthor, mas podem variar conforme a
    versao/customizacao da base. Valide com `DESCRIBE <tabela>` antes de
    colocar em producao. Veja tambem sql_exemplo_winthor.sql.
"""

from __future__ import annotations

import importlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import oracledb


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

# Rotacao do log: max 2 MB por arquivo, mantem ate 3 backups
LOG_TAMANHO_MAX_BYTES = 2 * 1024 * 1024
LOG_QTD_BACKUPS = 3

# Quantas vezes um pedido pode falhar (erro de dados/template) antes de
# ser descartado, para nao travar a fila dos pedidos seguintes.
MAX_TENTATIVAS_PEDIDO = 3


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("autocomanda")


def configurar_logging() -> None:
    """Configura arquivo + console. Idempotente: chamar de novo nao
    duplica os handlers."""
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)

    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    arquivo = RotatingFileHandler(
        str(LOG_PATH),
        maxBytes=LOG_TAMANHO_MAX_BYTES,
        backupCount=LOG_QTD_BACKUPS,
        encoding="utf-8",
    )
    arquivo.setFormatter(formato)
    logger.addHandler(arquivo)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)
    logger.addHandler(console)


# ---------------------------------------------------------------------------
# Instancia unica (evita duas copias imprimindo cada comanda em dobro)
# ---------------------------------------------------------------------------

NOME_MUTEX = "AutoComanda_InstanciaUnica"

# Referencia mantida enquanto o processo vive; o sistema libera a trava
# sozinho quando o processo termina (inclusive se travar ou for morto).
_trava_instancia: Any = None


def garantir_instancia_unica() -> bool:
    """Retorna False se ja houver outro AutoComanda rodando na maquina."""
    global _trava_instancia

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

        # "Global\" vale para todas as sessoes de usuario da maquina;
        # se o Windows recusar, cai para a sessao atual ("Local\").
        for prefixo in ("Global\\", "Local\\"):
            handle = kernel32.CreateMutexW(None, False, prefixo + NOME_MUTEX)
            erro = ctypes.get_last_error()
            if handle:
                _trava_instancia = handle
                return erro != ERROR_ALREADY_EXISTS
        # Nao conseguiu criar o mutex: nao impede a execucao.
        logger.warning("Nao foi possivel verificar instancia unica (erro %d).", erro)
        return True

    # Fora do Windows (desenvolvimento/testes): trava por arquivo.
    import fcntl

    PROCESSADOS_DIR.mkdir(parents=True, exist_ok=True)
    _trava_instancia = open(PROCESSADOS_DIR / "autocomanda.lock", "w")
    try:
        fcntl.flock(_trava_instancia, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Configuracao (config/config_comanda.json)
# ---------------------------------------------------------------------------

CAMPO_POR_FILTRO = {
    "id_produto": "CODPROD",
    "secao": "CODSECAO",
    "departamento": "CODEPTO",
}

PADROES_CONFIG = {
    "intervalo_verificacao_segundos": 5,
    "encoding_impressora": "cp850",
    "schema_oracle": "",
    "dias_historico": 7,
    "dias_comandas_txt": 3,
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

    for chave, padrao in PADROES_CONFIG.items():
        config.setdefault(chave, padrao)

    # normaliza para lookup rapido (set de inteiros)
    config["itens_preparo"] = {int(x) for x in config["itens_preparo"]}

    # prefixo de schema pronto para uso nas consultas (ex: "WINTHOR.")
    schema = config["schema_oracle"]
    if schema and not schema.endswith("."):
        config["schema_oracle"] = schema + "."

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


def purgar_historico_antigo(historico: sqlite3.Connection, dias: int) -> None:
    """Remove registros com mais de N dias do historico SQLite.

    O limite e calculado em hora local, no mesmo formato ISO usado em
    marcar_processado (o datetime('now') do SQLite seria UTC)."""
    limite = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    cur = historico.execute(
        "DELETE FROM cupons_processados WHERE data_processado < ?", (limite,)
    )
    historico.commit()
    if cur.rowcount:
        logger.info("Historico: %d registro(s) com mais de %d dias removido(s).", cur.rowcount, dias)


def limpar_comandas_antigas(dias: int) -> None:
    """Remove arquivos .txt de comandas com mais de N dias da pasta processados/."""
    limite = time.time() - (dias * 86400)
    removidos = 0
    for arquivo in PROCESSADOS_DIR.glob("comanda_*.txt"):
        try:
            if arquivo.stat().st_mtime < limite:
                arquivo.unlink()
                removidos += 1
        except OSError:
            pass
    if removidos:
        logger.info("Limpeza: %d arquivo(s) .txt com mais de %d dias removido(s).", removidos, dias)


def ja_processado(historico: sqlite3.Connection, num_pedido: Any) -> bool:
    cur = historico.execute(
        "SELECT 1 FROM cupons_processados WHERE num_pedido = ?", (str(num_pedido),)
    )
    return cur.fetchone() is not None


def tem_registro_hoje(historico: sqlite3.Connection) -> bool:
    """Indica se o historico ja tem algum pedido registrado hoje, ou seja,
    se o AutoComanda ja rodou hoje (data_processado e ISO local)."""
    hoje = datetime.now().date().isoformat()
    cur = historico.execute(
        "SELECT 1 FROM cupons_processados WHERE data_processado >= ? LIMIT 1", (hoje,)
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


def buscar_pedidos_faturados(
    conn: oracledb.Connection, config: dict[str, Any], inicio_execucao: datetime
) -> list[dict[str, Any]]:
    """Busca pedidos com POSICAO = 'L' (liberado) a partir do dia em que
    o AutoComanda foi iniciado.

    Usa TRUNC(:data_inicio) para compatibilidade com colunas DATA que
    armazenam apenas a data (sem hora). O controle fino de reimpressao
    fica a cargo do SQLite local (historico.db).
    """
    schema = config["schema_oracle"]

    query = f"""
        SELECT
            C.NUMPEDECF  AS NUMPED,
            C.NUMCUPOM,
            C.CODFILIAL,
            C.CODFUNCCX,
            C.NUMCAIXA,
            E.NOME        AS NOMEOPERADOR,
            C.DATA,
            C.POSICAO
        FROM {schema}PCPEDCECF C
        LEFT JOIN {schema}PCEMPR E ON E.MATRICULA = C.CODFUNCCX
        WHERE C.POSICAO = 'L'
          AND C.DATA >= TRUNC(:data_inicio)
        ORDER BY C.DATA, C.NUMPEDECF
    """
    with conn.cursor() as cur:
        cur.execute(query, data_inicio=inicio_execucao)
        colunas = [c[0] for c in cur.description]
        return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def buscar_itens_pedido(conn: oracledb.Connection, num_pedido: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Busca os itens de um pedido, com departamento/secao do produto
    (necessarios para os modos de filtro 'departamento' e 'secao')."""
    schema = config["schema_oracle"]

    # Departamento/secao so sao lidos quando o filtro precisa deles, para
    # que o modo "id_produto" nao dependa dessas colunas. No Winthor a
    # secao do produto fica em PCPRODUT.CODSEC (nao CODSECAO).
    filtro = config["filtro_por"]
    col_depto = "P.CODEPTO" if filtro == "departamento" else "NULL"
    col_secao = "P.CODSEC" if filtro == "secao" else "NULL"

    query = f"""
        SELECT
            I.CODPROD,
            P.DESCRICAO,
            I.QT,
            {col_depto} AS CODEPTO,
            {col_secao} AS CODSECAO
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

def carregar_template(nome_template: str) -> ModuleType:
    if str(TEMPLATES_DIR) not in sys.path:
        sys.path.insert(0, str(TEMPLATES_DIR))

    modulo = importlib.import_module(nome_template)

    if not hasattr(modulo, "gerar_layout_comanda"):
        raise AttributeError(
            f"O template '{nome_template}' precisa definir a funcao "
            "gerar_layout_comanda(num_cupom, data_hora, lista_produtos, "
            "nome_caixa, numero_caixa, nome_operador)"
        )
    return modulo


# ---------------------------------------------------------------------------
# Impressao (arquivo .txt enviado a impressora pelo Windows)
# ---------------------------------------------------------------------------

def salvar_comanda(texto: str, num_pedido: Any, encoding: str) -> Path:
    """Grava a comanda em processados/. O nome inclui o numero do pedido,
    para que dois pedidos no mesmo segundo nao sobrescrevam um ao outro."""
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho_txt = PROCESSADOS_DIR / f"comanda_{num_pedido}_{carimbo}.txt"

    with open(caminho_txt, "w", encoding=encoding, errors="replace") as f:
        f.write(texto)

    logger.info("Comanda salva em: %s", caminho_txt)
    return caminho_txt


def enviar_para_impressora(caminho_txt: Path, nome_impressora: str) -> None:
    """Imprime o .txt de forma silenciosa pelo Windows."""
    if not nome_impressora or nome_impressora.lower() == "default":
        # Imprime na impressora padrao
        os.startfile(str(caminho_txt), "print")
    else:
        # Imprime em impressora especifica
        subprocess.run(["notepad.exe", "/pt", str(caminho_txt), nome_impressora], check=True)


# ---------------------------------------------------------------------------
# Processamento de um pedido
# ---------------------------------------------------------------------------

def horario_venda(pedido: dict[str, Any]) -> datetime:
    """Horario da venda para exibir na comanda.

    Usa C.DATA quando ela traz hora; se vier so a data (meia-noite), cai
    para o horario atual, que e o mais proximo disponivel."""
    data = pedido.get("DATA")
    if isinstance(data, datetime) and data.time() != datetime.min.time():
        return data
    return datetime.now()


def processar_pedido(
    ora_conn: oracledb.Connection,
    historico: sqlite3.Connection,
    config: dict[str, Any],
    template_mod: ModuleType,
    pedido: dict[str, Any],
) -> None:
    num_pedido = pedido["NUMPED"]

    if ja_processado(historico, num_pedido):
        return

    itens = buscar_itens_pedido(ora_conn, num_pedido, config)
    itens_preparo = filtrar_itens_preparo(itens, config)

    if not itens_preparo:
        marcar_processado(historico, num_pedido, qtd_itens=0, impresso=False)
        return

    num_cupom_nfce = str(pedido.get("NUMCUPOM") or num_pedido)
    cod_operador = pedido.get("CODFUNCCX") or "?"
    nome_operador = pedido.get("NOMEOPERADOR") or "OPERADOR"

    texto_comanda = template_mod.gerar_layout_comanda(
        num_cupom=num_cupom_nfce,
        data_hora=horario_venda(pedido).strftime("%d/%m/%Y %H:%M:%S"),
        lista_produtos=itens_preparo,
        nome_caixa=str(pedido.get("CODFILIAL") or "?"),
        numero_caixa=str(pedido.get("NUMCAIXA") or "?"),
        nome_operador=f"{cod_operador} - {nome_operador}",
    )

    try:
        caminho_txt = salvar_comanda(texto_comanda, num_pedido, config["encoding_impressora"])
        enviar_para_impressora(caminho_txt, config["impressora_destino"])
    except Exception:
        logger.exception("Falha ao imprimir comanda do pedido %s", num_pedido)
        # Nao marca como processado: tenta novamente no proximo ciclo
        # (impressora desligada/sem papel nao deve perder o pedido).
        return

    logger.info("Comanda impressa: pedido %s, %d item(ns) de preparo", num_pedido, len(itens_preparo))
    marcar_processado(historico, num_pedido, qtd_itens=len(itens_preparo), impresso=True)


def processar_pedidos(
    ora_conn: oracledb.Connection,
    historico: sqlite3.Connection,
    config: dict[str, Any],
    template_mod: ModuleType,
    pedidos: list[dict[str, Any]],
    falhas: dict[str, int],
) -> None:
    """Processa cada pedido isoladamente: um pedido com erro nao impede
    os seguintes. Depois de MAX_TENTATIVAS_PEDIDO falhas o pedido e
    registrado como nao impresso e sai da fila.

    Erros do Oracle sobem para o loop principal, que reconecta."""
    for pedido in pedidos:
        num_pedido = str(pedido["NUMPED"])
        try:
            processar_pedido(ora_conn, historico, config, template_mod, pedido)
        except oracledb.DatabaseError:
            raise
        except Exception:
            falhas[num_pedido] = falhas.get(num_pedido, 0) + 1
            tentativa = falhas[num_pedido]
            if tentativa < MAX_TENTATIVAS_PEDIDO:
                logger.exception(
                    "Erro ao processar pedido %s (tentativa %d de %d).",
                    num_pedido, tentativa, MAX_TENTATIVAS_PEDIDO,
                )
                continue
            logger.exception(
                "Pedido %s falhou %d vezes e foi DESCARTADO, sem imprimir. "
                "Verifique o pedido manualmente.", num_pedido, tentativa,
            )
            marcar_processado(historico, num_pedido, qtd_itens=0, impresso=False)
            del falhas[num_pedido]


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def executar() -> None:
    print("=" * 60)
    print("                 AutoComanda PDV")
    print("=" * 60)
    print("Monitorando novos Pedidos... Pressione Ctrl+C para sair.\n")

    inicio_execucao = datetime.now()
    logger.info("=== AutoComanda iniciado ===")
    logger.debug("Diretorio base: %s", BASE_DIR)

    config = carregar_config()
    template_mod = carregar_template(config["template"])
    historico = abrir_historico()

    # Limpeza de dados antigos ao iniciar
    purgar_historico_antigo(historico, dias=int(config["dias_historico"]))
    limpar_comandas_antigas(dias=int(config["dias_comandas_txt"]))

    intervalo_segundos = int(config["intervalo_verificacao_segundos"])

    ora_conn: Optional[oracledb.Connection] = None
    falhas: dict[str, int] = {}

    # A consulta busca desde o inicio do dia (a coluna DATA nao tem hora).
    # Se o AutoComanda ainda nao rodou hoje (instalacao nova ou PDV ligado
    # no meio do expediente), os pedidos ja existentes sao apenas
    # registrados, sem imprimir, para nao mandar uma rajada de comandas
    # antigas para a cozinha. Num reinicio no meio do dia o historico ja
    # tem registros de hoje, entao pedidos feitos durante a parada ainda
    # sao impressos.
    ignorar_pendentes = not tem_registro_hoje(historico)

    while True:
        try:
            if ora_conn is None:
                ora_conn = conectar_oracle(config)
                logger.info("Conectado com sucesso.")

            pedidos = buscar_pedidos_faturados(ora_conn, config, inicio_execucao)

            if ignorar_pendentes:
                ignorados = 0
                for pedido in pedidos:
                    if not ja_processado(historico, pedido["NUMPED"]):
                        marcar_processado(historico, pedido["NUMPED"], qtd_itens=0, impresso=False)
                        ignorados += 1
                ignorar_pendentes = False
                logger.info(
                    "Primeira execucao do dia: %d pedido(s) anteriores ao inicio "
                    "registrados sem imprimir.", ignorados,
                )
            else:
                processar_pedidos(ora_conn, historico, config, template_mod, pedidos, falhas)

        except oracledb.DatabaseError:
            logger.exception("Erro de conexao/consulta. Reconectando no proximo ciclo.")
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
    configurar_logging()

    if not garantir_instancia_unica():
        logger.warning("Outra copia do AutoComanda ja esta em execucao. Encerrando esta.")
        print("\n" + "=" * 60)
        print("  O AutoComanda ja esta aberto neste computador.")
        print("  Esta janela sera fechada automaticamente.")
        print("=" * 60)
        time.sleep(5)
        sys.exit(0)

    try:
        executar()
    except KeyboardInterrupt:
        logger.info("AutoComanda encerrado pelo usuario.")
    except Exception:
        logger.exception("Erro fatal ao iniciar o AutoComanda.")
        print("\n" + "=" * 60)
        print("  ERRO: O AutoComanda nao conseguiu iniciar.")
        print("  Verifique o arquivo autocomanda.log para detalhes.")
        print("=" * 60)
        input("\nPressione ENTER para fechar...")
