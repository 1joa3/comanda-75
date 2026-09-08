"""
Template padrao de comanda para impressoras termicas de 48 colunas.

Implementa a assinatura exigida pelo motor principal:

    gerar_layout_comanda(num_cupom, data_hora, lista_produtos, nome_caixa,
                         numero_caixa, nome_operador) -> str

Para criar um layout alternativo, copie este arquivo com outro nome
dentro de templates/ e aponte "template" no config_comanda.json para
ele (sem a extensao .py).
"""

from __future__ import annotations

import textwrap
from typing import Any

LARGURA = 48


def _linha_separadora(caractere: str = "-") -> str:
    return caractere * LARGURA


def _formatar_quantidade(qtd: float) -> str:
    # Itens vendidos por peso (ex: pao/salgado por kg) costumam vir com
    # casas decimais; itens por unidade vem como inteiro.
    qtd = float(qtd)
    if qtd.is_integer():
        return f"{int(qtd)}"
    return f"{qtd:.3f}".rstrip("0").rstrip(".")


def _formatar_item(descricao: str, quantidade: float) -> list[str]:
    qtd_texto = _formatar_quantidade(quantidade)
    prefixo = f"{qtd_texto}x "
    largura_descricao = max(LARGURA - len(prefixo), 10)

    linhas_desc = textwrap.wrap((descricao or "PRODUTO").upper(), width=largura_descricao) or [""]

    linhas = [f"{prefixo}{linhas_desc[0]}"]
    for continuacao in linhas_desc[1:]:
        linhas.append(" " * len(prefixo) + continuacao)
    return linhas


def gerar_layout_comanda(
    num_cupom: Any,
    data_hora: str,
    lista_produtos: list[dict[str, Any]],
    nome_caixa: str = "?",       # mantido por compatibilidade
    numero_caixa: str = "?",
    nome_operador: str = "?",
) -> str:
    linhas: list[str] = []

    linhas.append(_linha_separadora("="))
    linhas.append("COMANDA DE PRODUCAO".center(LARGURA))
    linhas.append(_linha_separadora("="))
    linhas.append(f"Cupom NFC-e: {num_cupom}")
    linhas.append(f"Data/Hora : {data_hora}")
    linhas.append(f"Caixa     : {numero_caixa}")
    linhas.append(f"Operador  : {nome_operador}")
    linhas.append(_linha_separadora())

    for item in lista_produtos:
        linhas.extend(_formatar_item(item.get("descricao", "PRODUTO"), item.get("quantidade", 1)))
        linhas.append("")  # espaco entre itens, facilita leitura na cozinha

    linhas.append(_linha_separadora())
    linhas.append(f"Total de itens: {len(lista_produtos)}")
    linhas.append(_linha_separadora("="))

    # avanco de papel extra, facilita o corte/rasgo
    linhas.extend(["", "", ""])

    return "\n".join(linhas)
