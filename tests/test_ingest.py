"""Testa a ingestão de ponta a ponta com zips SINTÉTICOS no formato da CVM (sem rede)."""
import io
import zipfile

import pandas as pd

from src.db import conectar
from src.ingest_cvm import (
    extrair_mapa_fca, gravar_capital, gravar_demonstracoes, gravar_empresas_e_tickers,
    processar_capital, processar_demonstracoes,
)

CNPJ = "33.000.167/0001-01"


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(sep=";", index=False).encode("latin-1")


def _zip(caminho, arquivos: dict[str, pd.DataFrame]):
    with zipfile.ZipFile(caminho, "w") as zf:
        for nome, df in arquivos.items():
            zf.writestr(nome, _csv(df))


def _dre_linhas():
    base = {"CNPJ_CIA": CNPJ, "DT_REFER": "2024-12-31", "DENOM_CIA": "EMPRESA TESTE SA", "CD_CVM": "9512",
            "MOEDA": "REAL", "ESCALA_MOEDA": "MIL", "DT_INI_EXERC": "2024-01-01", "DT_FIM_EXERC": "2024-12-31",
            "CD_CONTA": "3.11", "DS_CONTA": "Lucro Líquido"}
    return pd.DataFrame([
        {**base, "VERSAO": "1", "ORDEM_EXERC": "ÚLTIMO", "VL_CONTA": "100.00"},     # versão antiga
        {**base, "VERSAO": "2", "ORDEM_EXERC": "ÚLTIMO", "VL_CONTA": "120.50"},     # reapresentada
        {**base, "VERSAO": "2", "ORDEM_EXERC": "PENÚLTIMO", "VL_CONTA": "90.00"},   # comparativo: ignorar
        {**{**base, "CNPJ_CIA": "11.111.111/0001-11"}, "VERSAO": "1", "ORDEM_EXERC": "ÚLTIMO", "VL_CONTA": "5"},  # outra empresa
    ])


def test_ingestao_dfp_sintetica(tmp_path):
    capital = pd.DataFrame([{
        "CNPJ_CIA": CNPJ, "DT_REFER": "2024-12-31", "VERSAO": "1", "DENOM_CIA": "EMPRESA TESTE SA",
        "QT_ACAO_ORDIN_CAP_INTEGR": "1000", "QT_ACAO_PREF_CAP_INTEGR": "2000", "QT_ACAO_TOTAL_CAP_INTEGR": "3000",
        "QT_ACAO_ORDIN_TESOURO": "10", "QT_ACAO_PREF_TESOURO": "20", "QT_ACAO_TOTAL_TESOURO": "30",
    }])
    zp = tmp_path / "dfp_cia_aberta_2024.zip"
    _zip(zp, {
        "dfp_cia_aberta_DRE_con_2024.csv": _dre_linhas(),
        "dfp_cia_aberta_composicao_capital_2024.csv": capital,
    })

    cnpjs = {"33000167000101"}
    demos = processar_demonstracoes(zp, "DFP", 2024, cnpjs, {"con"})
    # Só a versão 2, só 'ÚLTIMO', só a empresa pedida, e escala MIL aplicada.
    assert len(demos) == 1
    assert demos["vl_conta"].iloc[0] == 120_500.0
    assert demos["cnpj"].iloc[0] == "33000167000101"

    con = conectar(tmp_path / "t.duckdb")
    gravar_demonstracoes(con, demos)
    gravar_demonstracoes(con, demos)  # idempotente: rodar de novo não duplica
    assert con.execute("SELECT count(*) FROM demonstracoes").fetchone()[0] == 1

    cap = processar_capital(zp, "DFP", 2024, cnpjs)
    gravar_capital(con, cap)
    assert con.execute("SELECT qt_total FROM composicao_capital").fetchone()[0] == 3000

    mapa = pd.DataFrame({"ticker": ["TSTE3", "TSTE4"], "cnpj": ["33000167000101"] * 2})
    gravar_empresas_e_tickers(con, demos, mapa)
    assert con.execute("SELECT count(*) FROM tickers").fetchone()[0] == 2
    assert con.execute("SELECT nome FROM empresas").fetchone()[0] == "EMPRESA TESTE SA"


def test_mapa_fca_sintetico(tmp_path):
    fca = pd.DataFrame({
        "CNPJ_Companhia": [CNPJ, CNPJ, "11.111.111/0001-11"],
        "Valor_Mobiliario": ["Ações Ordinárias", "Ações Preferenciais", "Debêntures"],
        "Codigo_Negociacao": ["TSTE3", "TSTE4", None],
    })
    zp = tmp_path / "fca_cia_aberta_2025.zip"
    _zip(zp, {"fca_cia_aberta_valor_mobiliario_2025.csv": fca})
    mapa = extrair_mapa_fca(zp)
    assert set(mapa["ticker"]) == {"TSTE3", "TSTE4"}
    assert set(mapa["cnpj"]) == {"33000167000101"}
