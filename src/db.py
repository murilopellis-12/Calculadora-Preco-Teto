"""Conexão e esquema do banco local (DuckDB = um arquivo, sem servidor)."""
from __future__ import annotations

from pathlib import Path

import duckdb

# Nota: sem PRIMARY KEY de propósito. A ingestão é idempotente via "apaga o
# recorte e reinsere", e isso evita problemas de chave duplicada no DuckDB.
ESQUEMA = [
    """CREATE TABLE IF NOT EXISTS empresas (
        cnpj    VARCHAR,   -- só dígitos
        cd_cvm  VARCHAR,
        nome    VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS tickers (
        ticker  VARCHAR,
        cnpj    VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS demonstracoes (
        cnpj          VARCHAR,
        cd_cvm        VARCHAR,
        nome          VARCHAR,
        fonte         VARCHAR,   -- 'DFP' (anual) ou 'ITR' (trimestral)
        ano_arquivo   INTEGER,   -- ano do zip de origem
        demonstracao  VARCHAR,   -- BPA, BPP, DRE, DFC_MD, DFC_MI, DVA
        escopo        VARCHAR,   -- 'con' (consolidado) ou 'ind' (individual)
        versao        INTEGER,
        moeda         VARCHAR,
        dt_refer      DATE,
        dt_ini_exerc  DATE,      -- nulo no balanço
        dt_fim_exerc  DATE,
        cd_conta      VARCHAR,   -- ex.: '3.01' = Receita; '3.11' = Lucro/Prejuízo
        ds_conta      VARCHAR,
        vl_conta      DOUBLE     -- já convertido para a unidade da moeda
    )""",
    """CREATE TABLE IF NOT EXISTS composicao_capital (
        cnpj                      VARCHAR,
        fonte                     VARCHAR,
        ano_arquivo               INTEGER,
        dt_refer                  DATE,
        versao                    INTEGER,
        qt_ordinarias             DOUBLE,
        qt_preferenciais          DOUBLE,
        qt_total                  DOUBLE,
        qt_tesouro_ordinarias     DOUBLE,
        qt_tesouro_preferenciais  DOUBLE,
        qt_tesouro_total          DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS cotacoes (
        ticker      VARCHAR,
        data        DATE,
        abertura    DOUBLE,
        maxima      DOUBLE,
        minima      DOUBLE,
        fechamento  DOUBLE,
        volume      DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS proventos (
        ticker      VARCHAR,
        data        DATE,
        tipo        VARCHAR,   -- 'DIVIDENDO', 'JCP', etc.
        valor       DOUBLE     -- valor por ação já em reais
    )""",
]


def conectar(caminho: str | Path) -> duckdb.DuckDBPyConnection:
    """Abre (criando se preciso) o banco e garante que as tabelas existem."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(caminho))
    for comando in ESQUEMA:
        con.execute(comando)
    return con
