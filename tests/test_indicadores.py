"""Testes da camada de indicadores e integração com o banco."""
import duckdb
import pandas as pd
import pytest

from src.db import ESQUEMA
from src.indicadores import (
    calcular_cagrs,
    calcular_indicadores_ano,
    eh_instituicao_financeira,
    formatar_tabela_exibicao,
    identificar_empresa,
    obter_historico_empresa,
)


@pytest.fixture
def banco_teste(tmp_path):
    caminho = tmp_path / "teste_indicadores.duckdb"
    con = duckdb.connect(str(caminho))
    for cmd in ESQUEMA:
        con.execute(cmd)

    # Inserir empresas e tickers
    con.execute("INSERT INTO empresas VALUES ('11111111000111', '100', 'EMPRESA IND SA')")
    con.execute("INSERT INTO empresas VALUES ('22222222000122', '200', 'BANCO TESTE SA')")
    con.execute("INSERT INTO tickers VALUES ('TEST3', '11111111000111')")
    con.execute("INSERT INTO tickers VALUES ('BANC4', '22222222000122')")

    # Inserir demonstrações para EMPRESA IND SA (2023 e 2024)
    # Contas: 3.01 Receita (1000), 3.02 Custo (600), 3.03 Bruto (400), 3.05 EBIT (200),
    # 3.11 Lucro (100), 1.01.01 Caixa (50), 2.01.04 Div CP (30), 2.02.01 Div LP (70),
    # 2.03 PL (500)
    for ano in (2023, 2024):
        fator = 1.0 if ano == 2023 else 1.2
        linhas_demo = [
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "DRE", "con", 1, "REAL",
             f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", "3.01", "Receita", 1000.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "DRE", "con", 1, "REAL",
             f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", "3.02", "Custo", 600.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "DRE", "con", 1, "REAL",
             f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", "3.03", "Resultado Bruto", 400.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "DRE", "con", 1, "REAL",
             f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", "3.05", "EBIT", 200.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "DRE", "con", 1, "REAL",
             f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", "3.11", "Lucro Liquido", 100.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "BPA", "con", 1, "REAL",
             f"{ano}-12-31", None, f"{ano}-12-31", "1.01.01", "Caixa e Equivalentes", 50.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "BPP", "con", 1, "REAL",
             f"{ano}-12-31", None, f"{ano}-12-31", "2.01.04", "Emprestimos CP", 30.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "BPP", "con", 1, "REAL",
             f"{ano}-12-31", None, f"{ano}-12-31", "2.02.01", "Emprestimos LP", 70.0 * fator),
            ("11111111000111", "100", "EMPRESA IND SA", "DFP", ano, "BPP", "con", 1, "REAL",
             f"{ano}-12-31", None, f"{ano}-12-31", "2.03", "Patrimonio Liquido Consolidado", 500.0 * fator),
        ]
        con.executemany("INSERT INTO demonstracoes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", linhas_demo)

        # Capital: 100 ações, 0 tesouraria
        con.execute(
            "INSERT INTO composicao_capital VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["11111111000111", "DFP", ano, f"{ano}-12-31", 1, 100.0, 0.0, 100.0, 0.0, 0.0, 0.0]
        )

    # Inserir demonstracao para BANCO TESTE SA: PL na conta 2.07
    con.execute("""
        INSERT INTO demonstracoes VALUES
        ('22222222000122', '200', 'BANCO TESTE SA', 'DFP', 2024, 'BPP', 'con', 1, 'REAL',
         '2024-12-31', NULL, '2024-12-31', '2.07', 'Patrimônio Líquido Consolidado', 2000.0),
        ('22222222000122', '200', 'BANCO TESTE SA', 'DFP', 2024, 'DRE', 'con', 1, 'REAL',
         '2024-12-31', '2024-01-01', '2024-12-31', '3.01', 'Receitas de Intermediação', 800.0),
        ('22222222000122', '200', 'BANCO TESTE SA', 'DFP', 2024, 'DRE', 'con', 1, 'REAL',
         '2024-12-31', '2024-01-01', '2024-12-31', '3.11', 'Lucro Líquido', 300.0)
    """)
    con.execute(
        "INSERT INTO composicao_capital VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["22222222000122", "DFP", 2024, "2024-12-31", 1, 500.0, 0.0, 500.0, 0.0, 0.0, 0.0]
    )

    yield con
    con.close()


def test_identificar_empresa(banco_teste):
    cnpj, nome = identificar_empresa(banco_teste, "TEST3")
    assert cnpj == "11111111000111"
    assert nome == "EMPRESA IND SA"

    cnpj_dir, _ = identificar_empresa(banco_teste, "11.111.111/0001-11")
    assert cnpj_dir == "11111111000111"

    with pytest.raises(ValueError):
        identificar_empresa(banco_teste, "INEXISTENTE")


def test_eh_instituicao_financeira(banco_teste):
    assert eh_instituicao_financeira(banco_teste, "22222222000122") is True
    assert eh_instituicao_financeira(banco_teste, "11111111000111") is False


def test_calcular_indicadores_empresa_normal(banco_teste):
    m = calcular_indicadores_ano(banco_teste, "11111111000111", 2023)
    # Lucro 100 / 100 ações = LPA 1.0
    assert m.lpa == pytest.approx(1.0)
    # PL 500 / 100 ações = VPA 5.0
    assert m.vpa == pytest.approx(5.0)
    # ROE 100 / 500 = 20%
    assert m.roe == pytest.approx(0.20)
    # Margens: 400/1000 = 40% bruta; 200/1000 = 20% ebit; 100/1000 = 10% líquida
    assert m.margem_bruta == pytest.approx(0.40)
    assert m.margem_ebit == pytest.approx(0.20)
    assert m.margem_liquida == pytest.approx(0.10)
    # Dívida: (30 + 70) - 50 = 50. EBITDA = 200. Div/EBITDA = 50/200 = 0.25x
    assert m.divida_liquida == pytest.approx(50.0)
    assert m.divida_liquida_ebitda == pytest.approx(0.25)


def test_calcular_indicadores_banco(banco_teste):
    m = calcular_indicadores_ano(banco_teste, "22222222000122", 2024)
    # Lucro 300 / 500 ações = LPA 0.60
    assert m.lpa == pytest.approx(0.60)
    # PL 2000 / 500 ações = VPA 4.0
    assert m.vpa == pytest.approx(4.0)
    # ROE 300 / 2000 = 15%
    assert m.roe == pytest.approx(0.15)
    # Não calcula ROIC e Dívida Líq/EBITDA para bancos
    assert m.roic is None
    assert m.divida_liquida_ebitda is None


def test_obter_historico_e_cagr(banco_teste):
    df = obter_historico_empresa(banco_teste, "TEST3")
    assert len(df) == 2
    cagrs = calcular_cagrs(df)
    # LPA cresceu de 1.0 para 1.2 em 1 ano = 20% a.a.
    assert cagrs["cagr_lpa_1a"] == pytest.approx(0.20)


def test_formatar_tabela_exibicao(banco_teste):
    df = obter_historico_empresa(banco_teste, "TEST3")
    tabela = formatar_tabela_exibicao(df, eh_financeira=False)
    assert "LPA" in tabela.columns
    assert "ROE" in tabela.columns
    assert "ROIC" in tabela.columns
    assert tabela["LPA"].iloc[0] == "R$ 1.00"
