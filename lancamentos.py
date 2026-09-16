"""Geração dos lançamentos contábeis propostos: fechamento mensal (a partir do relatório de
Fechamento de Estoque do IW) e bonificações (a partir das NFs do IW)."""
from __future__ import annotations

import pandas as pd

CONTA_POR_TIPOMATERIAL = {
    "Dietas": 115010003,
    "Mat. Enfermagem": 115010002,
    "Materiais Diversos": 115010004,
    "Medicamentos": 115010001,
}

CONTA_TRANSITORIA = 115010005
CONTA_DESPESA_CONSUMO = 411010002
CONTA_DESPESA_MOVIMENTO_AJUSTE_DESCARTE = 411060001
CONTA_RECEITA_BONIFICACAO = 321010003

# (nome do movimento, coluna no Fechamento, débito é a conta do grupo?)
_REGRAS_MOVIMENTO = [
    ("Compras", "COMPRAS", True),
    ("Consumo", "CONSUMO", False),
    ("Movimento", "MOVIMENTO", False),
    ("Ajuste", "AJUSTE", False),
    ("Descarte", "PERDAS", False),
]

_CONTRAPARTIDA_DESPESA = {
    "Consumo": CONTA_DESPESA_CONSUMO,
    "Movimento": CONTA_DESPESA_MOVIMENTO_AJUSTE_DESCARTE,
    "Ajuste": CONTA_DESPESA_MOVIMENTO_AJUSTE_DESCARTE,
    "Descarte": CONTA_DESPESA_MOVIMENTO_AJUSTE_DESCARTE,
}


def gerar_lancamentos_propostos(fechamento_mes: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for _, row in fechamento_mes.iterrows():
        grupo = row["TIPOMATERIAL"]
        conta_grupo = CONTA_POR_TIPOMATERIAL.get(grupo)
        if conta_grupo is None:
            continue

        for nome_movimento, coluna, debito_e_conta_grupo in _REGRAS_MOVIMENTO:
            valor = row[coluna]
            if pd.isna(valor) or valor == 0:
                continue

            if debito_e_conta_grupo:
                debito, credito = conta_grupo, CONTA_TRANSITORIA
            else:
                debito, credito = _CONTRAPARTIDA_DESPESA[nome_movimento], conta_grupo

            if valor < 0:
                debito, credito = credito, debito

            linhas.append(
                {
                    "Filial": row["FILIAL"],
                    "Grupo": grupo,
                    "Movimento": nome_movimento,
                    "Conta Débito": debito,
                    "Conta Crédito": credito,
                    "Valor": round(abs(valor), 2),
                    "Histórico": f"Fechamento Estoque {nome_movimento} - {grupo} - {row['PERIODO']}",
                }
            )

    colunas = ["Filial", "Grupo", "Movimento", "Conta Débito", "Conta Crédito", "Valor", "Histórico"]
    return pd.DataFrame(linhas, columns=colunas)


def gerar_lancamentos_bonificacao(nfs_iw: pd.DataFrame) -> pd.DataFrame:
    bonificacao = nfs_iw[nfs_iw["TIPONF"] == "Bonificação"].copy()
    return pd.DataFrame(
        {
            "Filial": bonificacao["FILIAL"],
            "NRNF": bonificacao["NRNF"],
            "Fornecedor": bonificacao["FULLNAME"],
            "Data Lançamento IW": bonificacao["DATALANCTO"],
            "Conta Débito": CONTA_TRANSITORIA,
            "Conta Crédito": CONTA_RECEITA_BONIFICACAO,
            "Valor": bonificacao["VLRNF"],
            "Histórico": "Bonificação NF " + bonificacao["NRNF"].astype(str) + " - " + bonificacao["FULLNAME"].astype(str),
        }
    )
