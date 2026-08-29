-- =============================================================================
-- AutoComanda - Exemplo de consultas Oracle no padrao Winthor (rotina 2075)
-- =============================================================================
--
-- ATENCAO - LEIA ANTES DE USAR EM PRODUCAO:
-- As tabelas abaixo (PCPEDC, PCPEDI, PCPRODUT) e o uso do campo POSICAO
-- para identificar pedido faturado sao o padrao documentado do Winthor
-- (TOTVS/PC Sistemas) e aparecem associadas a rotina 2075 na documentacao
-- oficial. Porem, colunas especificas (qual campo identifica o caixa/
-- terminal fisico, se DATA carrega a hora junto, chaves compostas com
-- CODFILIAL etc.) VARIAM conforme a versao e as customizacoes da base.
-- Antes de colocar em producao:
--   1) rode DESCRIBE PCPEDC / DESCRIBE PCPEDI / DESCRIBE PCPRODUT na
--      base de homologacao;
--   2) confirme com o DBA ou consultor Winthor os valores possiveis de
--      POSICAO na sua versao e qual campo identifica o caixa/terminal;
--   3) ajuste os nomes abaixo conforme necessario.
-- =============================================================================


-- -----------------------------------------------------------------------
-- 1) Pedidos/cupons faturados (fechados) numa janela de tempo recente
-- -----------------------------------------------------------------------
-- POSICAO = 'F' -> Faturado (venda concluida). Outros valores comuns na
-- documentacao Winthor: L = Liberado, P = Pendente, B = Bloqueado,
-- M = Montado, E = Entregue, C = Cancelado.
SELECT
    C.NUMPED,
    C.CODFILIAL,
    C.CODUSUR,      -- operador/vendedor; use como referencia do "caixa"
                     -- se nao houver campo especifico de terminal na sua base
    C.DATA,
    C.POSICAO
FROM PCPEDC C
WHERE C.POSICAO = 'F'
  AND C.DATA >= (SYSDATE - :janela_dias)   -- ex: 60/1440 = ultimos 60 minutos
ORDER BY C.DATA;


-- -----------------------------------------------------------------------
-- 2) Itens de um pedido especifico, com departamento/secao do produto
-- -----------------------------------------------------------------------
-- Necessario para os modos de filtro "id_produto", "secao" e
-- "departamento" do config_comanda.json.
SELECT
    I.CODPROD,
    P.DESCRICAO,
    I.QT,
    P.CODEPTO,      -- departamento (para filtro_por = "departamento")
    P.CODSECAO      -- secao        (para filtro_por = "secao")
FROM PCPEDI I
JOIN PCPRODUT P ON P.CODPROD = I.CODPROD
WHERE I.NUMPED = :num_pedido;


-- -----------------------------------------------------------------------
-- 3) Variante "ultima venda do caixa" (consulta pontual/ad-hoc)
-- -----------------------------------------------------------------------
-- Util para testar manualmente, fora do loop do AutoComanda, qual foi o
-- ultimo pedido faturado por uma filial/operador especifico.
SELECT *
FROM (
    SELECT
        C.NUMPED,
        C.CODFILIAL,
        C.CODUSUR,
        C.DATA,
        C.POSICAO
    FROM PCPEDC C
    WHERE C.POSICAO = 'F'
      AND C.CODFILIAL = :codfilial
    ORDER BY C.DATA DESC
)
WHERE ROWNUM = 1;
