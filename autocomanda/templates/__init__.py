"""Pacote de templates de comanda do AutoComanda.

Cada modulo aqui deve expor uma funcao com a assinatura:

    def gerar_layout_comanda(num_cupom, data_hora, lista_produtos, nome_caixa,
                             numero_caixa, nome_operador) -> str:
        ...

`lista_produtos` e uma lista de dicionarios no formato:
    {"descricao": str, "quantidade": int | float}
"""
