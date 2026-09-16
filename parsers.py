"""Leitura e normalização dos 3 arquivos de entrada: NFs do IW, Fechamento de Estoque do IW
e Razão Contábil de Estoque do MXM.

Os 3 arquivos são cumulativos (crescem a cada mês) e a pasta de trabalho contém exatamente
1 arquivo de cada tipo. A descoberta é feita pela estrutura de colunas, não pelo nome do arquivo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

FILIAL_POR_EMPRESA_NF = {"SOLAR": "RJ", "SOLAR SP": "SP"}
FILIAL_POR_NOME_FECHAMENTO = {"SOLAR RJ": "RJ", "SOLAR SP": "SP"}
FILIAL_POR_COD_EMPRESA_MXM = {1: "RJ", 6: "SP"}

CONTA_TRANSITORIA = 115010005
LOTES_INTEGRACAO_AUTOMATICA = {"SCP_PR", "SCP_BX", "SGP_PR"}

_PREFIXO_NAO_NF = re.compile(r"^(AD|CC)-")


class ArquivoInvalidoError(Exception):
    """Levantado quando um arquivo de entrada está ausente ou não tem a estrutura esperada."""


@dataclass(frozen=True)
class ArquivosEntrada:
    nfs_iw: Path
    fechamento: list[Path]
    razao_mxm: Path


def descobrir_arquivos(pasta: Path) -> ArquivosEntrada:
    """Descobre os 3 tipos de arquivo de entrada na pasta pela estrutura de colunas."""
    csvs = sorted(pasta.glob("*.csv"))
    xlsxs = sorted(p for p in pasta.glob("*.xlsx") if not p.name.startswith("~$"))

    nfs_iw_candidatos: list[Path] = []
    fechamento_candidatos: list[Path] = []
    for csv_path in csvs:
        colunas = _ler_cabecalho_csv(csv_path)
        if {"NRNF", "STATUS", "TIPONF"} <= colunas:
            nfs_iw_candidatos.append(csv_path)
        elif {"PERIODO", "TIPOMATERIAL", "FILIAL"} <= colunas:
            fechamento_candidatos.append(csv_path)

    xlsxs = [p for p in xlsxs if _parece_razao_mxm(p)]

    if len(nfs_iw_candidatos) != 1:
        raise ArquivoInvalidoError(
            "Esperado exatamente 1 CSV de NFs do IW (colunas NRNF/STATUS/TIPONF) na pasta "
            f"'{pasta}', encontrados {len(nfs_iw_candidatos)}: {[p.name for p in nfs_iw_candidatos]}"
        )
    if not fechamento_candidatos:
        raise ArquivoInvalidoError(
            "Nenhum CSV de Fechamento de Estoque (colunas PERIODO/TIPOMATERIAL/FILIAL) "
            f"encontrado na pasta '{pasta}'"
        )
    if len(xlsxs) != 1:
        raise ArquivoInvalidoError(
            f"Esperado exatamente 1 arquivo .xlsx (Razão Contábil MXM) na pasta '{pasta}', "
            f"encontrados {len(xlsxs)}: {[p.name for p in xlsxs]}"
        )

    return ArquivosEntrada(nfs_iw=nfs_iw_candidatos[0], fechamento=fechamento_candidatos, razao_mxm=xlsxs[0])


def _ler_cabecalho_csv(path: Path) -> set[str]:
    with path.open(encoding="latin-1") as f:
        primeira_linha = f.readline()
    return {c.strip().strip('"') for c in primeira_linha.strip().split(";")}


def _parece_razao_mxm(path: Path) -> bool:
    """Confirma que o .xlsx é o Razão Contábil MXM (e não, por exemplo, o próprio Excel gerado
    por este script numa execução anterior) checando as colunas esperadas."""
    try:
        colunas = set(pd.read_excel(path, nrows=0).columns)
    except Exception:
        return False
    return {"Cód. Empresa", "Conta Contábil", "No.Título", "Lote"} <= colunas


def ler_nfs_iw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        sep=";",
        encoding="latin-1",
        decimal=",",
        thousands=".",
        dtype={"NRNF": str, "CNPJ": str},
    )
    df.columns = [c.strip() for c in df.columns]

    df["FILIAL"] = df["EMPRESA"].map(FILIAL_POR_EMPRESA_NF)
    if df["FILIAL"].isna().any():
        desconhecidas = sorted(df.loc[df["FILIAL"].isna(), "EMPRESA"].unique())
        raise ArquivoInvalidoError(f"Valores de EMPRESA não mapeados para filial no NFs IW: {desconhecidas}")

    df["NRNF"] = df["NRNF"].str.strip().astype("int64")
    df["DATALANCTO"] = pd.to_datetime(df["DATALANCTO"], dayfirst=True)
    df["DATANOTA"] = pd.to_datetime(df["DATANOTA"], dayfirst=True)
    df["VLRNF"] = df["VLRNF"].round(2)
    df["VALOR"] = df["VALOR"].round(2)
    return df


def ler_fechamento(paths: list[Path]) -> pd.DataFrame:
    partes = []
    for path in paths:
        parte = pd.read_csv(path, sep=";", encoding="latin-1", decimal=",", thousands=".")
        parte.columns = [c.strip() for c in parte.columns]
        partes.append(parte)
    df = pd.concat(partes, ignore_index=True)

    df["FILIAL"] = df["FILIAL"].map(FILIAL_POR_NOME_FECHAMENTO)
    if df["FILIAL"].isna().any():
        desconhecidas = sorted(df.loc[df["FILIAL"].isna(), "FILIAL"].unique())
        raise ArquivoInvalidoError(f"Valores de FILIAL não mapeados no Fechamento de Estoque: {desconhecidas}")
    return df


def _extrair_nrnf(no_titulo: object) -> int | None:
    """Extrai o número da NF de 'No.Título' (ex.: '000054297.63927.1' -> 54297,
    'AD-000002502.67059.1' -> 2502). Retorna None se não for possível extrair."""
    if pd.isna(no_titulo):
        return None
    primeiro_segmento = str(no_titulo).split(".")[0]
    primeiro_segmento = _PREFIXO_NAO_NF.sub("", primeiro_segmento)
    try:
        return int(primeiro_segmento)
    except ValueError:
        return None


def ler_razao_mxm(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [c.strip() for c in df.columns]

    # A última linha do relatório é um total geral (todas as colunas-chave em branco).
    df = df.dropna(subset=["Cód. Empresa", "Conta Contábil"]).copy()

    df["FILIAL"] = df["Cód. Empresa"].astype(int).map(FILIAL_POR_COD_EMPRESA_MXM)
    if df["FILIAL"].isna().any():
        desconhecidos = sorted(df.loc[df["FILIAL"].isna(), "Cód. Empresa"].unique())
        raise ArquivoInvalidoError(f"Cód. Empresa não mapeado para filial no Razão MXM: {desconhecidos}")

    df["Conta Contábil"] = df["Conta Contábil"].astype("int64")
    df["NRNF"] = df["No.Título"].apply(_extrair_nrnf)
    df["Valor"] = df["Valor"].round(2)
    df["Valor Débito"] = df["Valor Débito"].fillna(0).round(2)
    df["Valor Crédito"] = df["Valor Crédito"].fillna(0).round(2)
    return df
