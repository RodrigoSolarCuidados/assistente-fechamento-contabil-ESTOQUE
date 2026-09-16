"""Classificação das NFs do IW e conciliação com os lançamentos do MXM na conta transitória.

Ordem de precedência de classificação de cada NF do IW:
1. TIPONF == "Bonificação"  -> nunca entra na conciliação (lançamento próprio, ver lancamentos.py)
2. STATUS == "CANCELADA"    -> crítico se casar com um débito vivo no MXM; senão, ignorada (esperado)
3. STATUS == "ATIVA"        -> conciliação normal (deve casar com um débito vivo no MXM)

A chave de conciliação é Filial + NRNF + |Valor|. Um mesmo NRNF pode ter mais de uma linha no
Razão (ex.: débito original SCP_PR + estorno de cancelamento SCP_BX) -- por isso o valor "vivo"
de cada chave é o líquido (soma respeitando o sinal) dessas linhas, não uma linha isolada.
Líquido == 0 significa que o lançamento foi revertido (ou nunca existiu); líquido == |Valor|
significa que o débito ainda está de pé na transitória.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from parsers import CONTA_TRANSITORIA, LOTES_INTEGRACAO_AUTOMATICA

_CHAVE = ["FILIAL", "NRNF", "ValorAbs"]


@dataclass
class ResultadoConciliacao:
    nfs_somente_iw: pd.DataFrame
    nfs_canceladas_criticas: pd.DataFrame
    lancamentos_somente_mxm: pd.DataFrame


def _base_mxm_integracao(razao_mxm: pd.DataFrame) -> pd.DataFrame:
    return razao_mxm[
        razao_mxm["Lote"].isin(LOTES_INTEGRACAO_AUTOMATICA) & (razao_mxm["Conta Contábil"] == CONTA_TRANSITORIA)
    ].copy()


def _agrupar_mxm(base_mxm: pd.DataFrame) -> pd.DataFrame:
    """Agrupa por Filial+NRNF+|Valor| e soma o valor líquido dos lançamentos."""
    com_nrnf = base_mxm[base_mxm["NRNF"].notna()].copy()
    com_nrnf["NRNF"] = com_nrnf["NRNF"].astype("int64")
    com_nrnf["ValorAbs"] = com_nrnf["Valor"].abs().round(2)
    agrupado = com_nrnf.groupby(_CHAVE, as_index=False).agg(
        ValorLiquido=("Valor", "sum"), QtdLancamentos=("Valor", "size")
    )
    agrupado["ValorLiquido"] = agrupado["ValorLiquido"].round(2)
    return agrupado


def _marcar_duplicidade(df: pd.DataFrame, chave: list[str]) -> pd.Series:
    return df.duplicated(subset=chave, keep=False)


def conciliar(nfs_iw: pd.DataFrame, razao_mxm: pd.DataFrame) -> ResultadoConciliacao:
    nfs = nfs_iw.copy()

    bonificacao = nfs[nfs["TIPONF"] == "Bonificação"]
    resto = nfs[nfs["TIPONF"] != "Bonificação"]
    canceladas = resto[resto["STATUS"] == "CANCELADA"].copy()
    ativas = resto[resto["STATUS"] == "ATIVA"].copy()

    for df in (ativas, canceladas, bonificacao):
        df["ChaveDuplicada"] = _marcar_duplicidade(df, ["FILIAL", "NRNF", "VLRNF"])

    base_mxm = _base_mxm_integracao(razao_mxm)
    grupos_mxm = _agrupar_mxm(base_mxm)

    def casar_com_mxm(df: pd.DataFrame) -> pd.DataFrame:
        return df.merge(
            grupos_mxm,
            left_on=["FILIAL", "NRNF", "VLRNF"],
            right_on=_CHAVE,
            how="left",
            suffixes=("", "_mxm"),
        )

    ativas_casadas = casar_com_mxm(ativas)
    nfs_somente_iw = ativas_casadas[
        ativas_casadas["ValorLiquido"].isna() | (ativas_casadas["ValorLiquido"] == 0)
    ].drop(columns=["ValorAbs", "QtdLancamentos"], errors="ignore")

    canceladas_casadas = casar_com_mxm(canceladas)
    nfs_canceladas_criticas = canceladas_casadas[
        canceladas_casadas["ValorLiquido"].notna() & (canceladas_casadas["ValorLiquido"] != 0)
    ].drop(columns=["ValorAbs", "QtdLancamentos"], errors="ignore")

    # Lançamentos com débito vivo (líquido != 0) que não correspondem a nenhuma NF conhecida do IW
    # (ativa, cancelada ou bonificação) são lançamentos indevidos / sem NF no IW.
    todas_chaves = (
        pd.concat(
            [
                ativas[["FILIAL", "NRNF", "VLRNF"]],
                canceladas[["FILIAL", "NRNF", "VLRNF"]],
                bonificacao[["FILIAL", "NRNF", "VLRNF"]],
            ]
        )
        .rename(columns={"VLRNF": "ValorAbs"})
        .drop_duplicates()
    )
    grupos_vivos = grupos_mxm[grupos_mxm["ValorLiquido"] != 0]
    lancamentos_somente_mxm = grupos_vivos.merge(todas_chaves, on=_CHAVE, how="left", indicator=True)
    lancamentos_somente_mxm = lancamentos_somente_mxm[lancamentos_somente_mxm["_merge"] == "left_only"].drop(
        columns="_merge"
    )

    return ResultadoConciliacao(
        nfs_somente_iw=nfs_somente_iw,
        nfs_canceladas_criticas=nfs_canceladas_criticas,
        lancamentos_somente_mxm=lancamentos_somente_mxm,
    )
