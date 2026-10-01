
SELECT
    C.NUMPED,
    C.CODFILIAL,
    C.CODUSUR,
    C.DATA,
    C.POSICAO
FROM PCPEDC C
WHERE C.POSICAO = 'F'
  AND C.DATA >= (SYSDATE - :janela_dias)  
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
