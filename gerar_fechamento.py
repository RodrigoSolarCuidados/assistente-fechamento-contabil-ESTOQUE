#!/usr/bin/env python
"""CLI: gera a planilha de fechamento contábil de estoque (Lançamentos Propostos + conciliação IW x MXM).

Uso:
    python gerar_fechamento.py --mes 2026-08

Procura os 3 arquivos de entrada (NFs do IW, Fechamento de Estoque, Razão Contábil MXM) na
pasta atual e escreve `fechamento_<mes>.xlsx` também na pasta atual.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from lancamentos import gerar_lancamentos_bonificacao, gerar_lancamentos_propostos
from parsers import (
    ArquivoInvalidoError,
    CONTA_TRANSITORIA,
    LOTES_INTEGRACAO_AUTOMATICA,
    descobrir_arquivos,
    ler_fechamento,
    ler_nfs_iw,
    ler_razao_mxm,
)
from reconciliation import conciliar

NOME_ABA = {
    "lancamentos_propostos": "Lançamentos Propostos",
    "lancamentos_bonificacao": "Lançamentos de Bonificação",
    "nfs_canceladas_criticas": "NFs Canceladas - Risco MXM",
    "nfs_somente_iw": "NFs somente IW",
    "lancamentos_somente_mxm": "Lançamentos somente MXM",
    "resumo": "Resumo por Conta",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera o fechamento contábil de estoque (IW -> MXM) do mês informado.")
    parser.add_argument("--mes", required=True, help="Mês de competência no formato YYYY-MM, ex.: 2026-08")
    return parser.parse_args(argv)


def _validar_mes(mes: str) -> tuple[int, int]:
    try:
        ano_str, mes_str = mes.split("-")
        ano, mes_num = int(ano_str), int(mes_str)
        if not (1 <= mes_num <= 12) or len(ano_str) != 4:
            raise ValueError
    except ValueError as exc:
        raise ArquivoInvalidoError(f"--mes deve estar no formato YYYY-MM, recebido '{mes}'") from exc
    return ano, mes_num


def calcular_saldo_por_filial(razao_mxm: pd.DataFrame, inicio_mes: pd.Timestamp, fim_mes: pd.Timestamp) -> pd.DataFrame:
    """Saldo contábil real (débito - crédito) da conta transitória por filial, imediatamente antes
    do fechamento manual do mês corrente: todo o histórico anterior ao mês, mais os débitos
    automáticos (SCP_PR/SCP_BX/SGP_PR) já integrados dentro do próprio mês. Exclui de propósito
    eventuais lançamentos manuais (CMV/CONTAB) já postados no mês corrente, pois são exatamente
    o que este relatório está propondo gerar."""
    transitoria = razao_mxm[razao_mxm["Conta Contábil"] == CONTA_TRANSITORIA]

    historico_anterior = transitoria[transitoria["Data Lançamento"] < inicio_mes]
    debitos_automaticos_do_mes = transitoria[
        (transitoria["Data Lançamento"] >= inicio_mes)
        & (transitoria["Data Lançamento"] <= fim_mes)
        & (transitoria["Lote"].isin(LOTES_INTEGRACAO_AUTOMATICA))
    ]
    base = pd.concat([historico_anterior, debitos_automaticos_do_mes])

    agg = base.groupby("FILIAL")[["Valor Débito", "Valor Crédito"]].sum()
    agg["SaldoAntes"] = (agg["Valor Débito"] - agg["Valor Crédito"]).round(2)
    return agg[["SaldoAntes"]].reset_index()


def montar_resumo_por_conta(
    razao_mxm: pd.DataFrame, lancamentos_propostos: pd.DataFrame, inicio_mes: pd.Timestamp, fim_mes: pd.Timestamp
) -> pd.DataFrame:
    saldo_antes = calcular_saldo_por_filial(razao_mxm, inicio_mes, fim_mes)
    if lancamentos_propostos.empty:
        creditos_compras = pd.Series(dtype=float, name="CreditosCompras")
    else:
        creditos_compras = (
            lancamentos_propostos[lancamentos_propostos["Movimento"] == "Compras"]
            .groupby("Filial")["Valor"]
            .sum()
            .rename("CreditosCompras")
        )
    resumo = saldo_antes.set_index("FILIAL").join(creditos_compras, how="left").fillna(0.0)
    resumo["SaldoDepois"] = (resumo["SaldoAntes"] - resumo["CreditosCompras"]).round(2)
    return resumo.reset_index().rename(columns={"FILIAL": "Filial"})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pasta = Path.cwd()

    try:
        ano, mes_num = _validar_mes(args.mes)
        arquivos = descobrir_arquivos(pasta)
        nfs_iw = ler_nfs_iw(arquivos.nfs_iw)
        fechamento = ler_fechamento(arquivos.fechamento)
        razao_mxm = ler_razao_mxm(arquivos.razao_mxm)
    except ArquivoInvalidoError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    periodo = f"{ano:04d}/{mes_num:02d}"
    fechamento_mes = fechamento[fechamento["PERIODO"] == periodo]
    if fechamento_mes.empty:
        print(f"Erro: nenhuma linha do Fechamento de Estoque encontrada para PERIODO={periodo}", file=sys.stderr)
        return 1

    lancamentos_propostos = gerar_lancamentos_propostos(fechamento_mes)
    lancamentos_bonificacao = gerar_lancamentos_bonificacao(nfs_iw)
    resultado = conciliar(nfs_iw, razao_mxm)

    inicio_mes = pd.Timestamp(ano, mes_num, 1)
    fim_mes = inicio_mes + pd.offsets.MonthEnd(1)
    resumo = montar_resumo_por_conta(razao_mxm, lancamentos_propostos, inicio_mes, fim_mes)

    saida = pasta / f"fechamento_{args.mes}.xlsx"
    with pd.ExcelWriter(saida, engine="openpyxl") as writer:
        lancamentos_propostos.to_excel(writer, sheet_name=NOME_ABA["lancamentos_propostos"], index=False)
        lancamentos_bonificacao.to_excel(writer, sheet_name=NOME_ABA["lancamentos_bonificacao"], index=False)
        resultado.nfs_canceladas_criticas.to_excel(writer, sheet_name=NOME_ABA["nfs_canceladas_criticas"], index=False)
        resultado.nfs_somente_iw.to_excel(writer, sheet_name=NOME_ABA["nfs_somente_iw"], index=False)
        resultado.lancamentos_somente_mxm.to_excel(writer, sheet_name=NOME_ABA["lancamentos_somente_mxm"], index=False)
        resumo.to_excel(writer, sheet_name=NOME_ABA["resumo"], index=False)

    print(f"Planilha gerada: {saida}")
    print(f"  {NOME_ABA['lancamentos_propostos']}: {len(lancamentos_propostos)}")
    print(f"  {NOME_ABA['lancamentos_bonificacao']}: {len(lancamentos_bonificacao)}")
    print(f"  {NOME_ABA['nfs_canceladas_criticas']} (CRÍTICO): {len(resultado.nfs_canceladas_criticas)}")
    print(f"  {NOME_ABA['nfs_somente_iw']}: {len(resultado.nfs_somente_iw)}")
    print(f"  {NOME_ABA['lancamentos_somente_mxm']}: {len(resultado.lancamentos_somente_mxm)}")
    for _, row in resumo.iterrows():
        print(f"  Saldo transitória {row['Filial']}: antes={row['SaldoAntes']:.2f} depois={row['SaldoDepois']:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
