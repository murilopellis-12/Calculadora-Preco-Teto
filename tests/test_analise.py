"""Testes de integração do motor completo de valuation em src/analise.py."""
from datetime import date

import duckdb
import pytest

from src.analise import avaliar_ativo, formatar_tabela_valuation
from src.db import ESQUEMA
from src.market_data import gravar_cotacao, gravar_provento


@pytest.fixture
def banco_valuation(tmp_path):
    caminho = tmp_path / "teste_valuation_engine.duckdb"
    con = duckdb.connect(str(caminho))
    for cmd in ESQUEMA:
        con.execute(cmd)

    # Inserir empresa industrial: IND3
    con.execute("INSERT INTO empresas VALUES ('11111111000111', '100', 'INDUSTRIA SA')")
    con.execute("INSERT INTO tickers VALUES ('IND3', '11111111000111')")

    # Inserir banco: BANC3
    con.execute("INSERT INTO empresas VALUES ('22222222000122', '200', 'BANCO TESTE SA')")
    con.execute("INSERT INTO tickers VALUES ('BANC3', '22222222000122')")

    # Dados da INDUSTRIA SA: LPA R$ 2.00, VPA R$ 20.00, Provento R$ 1.20
    # Demonstrações DFP 2024
    con.execute("""
        INSERT INTO demonstracoes VALUES
        ('11111111000111', '100', 'INDUSTRIA SA', 'DFP', 2024, 'DRE', 'con', 1, 'REAL',
         '2024-12-31', '2024-01-01', '2024-12-31', '3.11', 'Lucro Líquido', 200.0),
        ('11111111000111', '100', 'INDUSTRIA SA', 'DFP', 2024, 'BPP', 'con', 1, 'REAL',
         '2024-12-31', NULL, '2024-12-31', '2.03', 'Patrimônio Líquido Consolidado', 2000.0)
    """)
    con.execute(
        "INSERT INTO composicao_capital VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["11111111000111", "DFP", 2024, "2024-12-31", 1, 100.0, 0.0, 100.0, 0.0, 0.0, 0.0]
    )
    gravar_provento(con, "IND3", date(2024, 12, 31), "DIVIDENDO", 1.20)
    gravar_cotacao(con, "IND3", 25.0, data_ref=date(2026, 10, 8))

    # Dados do BANCO TESTE SA: PL em 2.07
    con.execute("""
        INSERT INTO demonstracoes VALUES
        ('22222222000122', '200', 'BANCO TESTE SA', 'DFP', 2024, 'DRE', 'con', 1, 'REAL',
         '2024-12-31', '2024-01-01', '2024-12-31', '3.11', 'Lucro Líquido', 300.0),
        ('22222222000122', '200', 'BANCO TESTE SA', 'DFP', 2024, 'BPP', 'con', 1, 'REAL',
         '2024-12-31', NULL, '2024-12-31', '2.07', 'Patrimônio Líquido Consolidado', 2000.0)
    """)
    con.execute(
        "INSERT INTO composicao_capital VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["22222222000122", "DFP", 2024, "2024-12-31", 1, 100.0, 0.0, 100.0, 0.0, 0.0, 0.0]
    )
    gravar_provento(con, "BANC3", date(2024, 12, 31), "DIVIDENDO", 1.50)
    gravar_cotacao(con, "BANC3", 18.0, data_ref=date(2026, 10, 8))

    yield con
    con.close()


def test_avaliar_ativo_nao_financeiro(banco_valuation):
    rel = avaliar_ativo(banco_valuation, "IND3", yield_minimo_projetivo=0.06, yield_bazin=0.06)

    assert rel.eh_financeira is False
    assert rel.preco_atual == pytest.approx(25.0)

    # Bazin: 1.20 / 0.06 = R$ 20.00
    assert rel.metodos["Bazin"].valor == pytest.approx(20.0)

    # Graham: sqrt(22.5 * 2 * 20) = sqrt(900) = R$ 30.00
    assert rel.metodos["Graham"].valor == pytest.approx(30.0)

    # Faixa consolidada
    assert rel.faixa.minimo is not None
    assert rel.faixa.maximo is not None
    assert rel.faixa.minimo <= rel.faixa.medio <= rel.faixa.maximo


def test_avaliar_ativo_financeiro(banco_valuation):
    rel = avaliar_ativo(banco_valuation, "BANC3")

    assert rel.eh_financeira is True
    # Graham e DCF não devem se aplicar
    assert rel.metodos["Graham"].valor is None
    assert "não se aplica" in rel.metodos["Graham"].observacao
    assert rel.metodos["DCF"].valor is None
    assert "não se aplica" in rel.metodos["DCF"].observacao

    # Bazin deve ser aplicado: 1.50 / 0.06 = R$ 25.00
    assert rel.metodos["Bazin"].valor == pytest.approx(25.0)

    # Gordon DDM deve ser aplicado para bancos
    assert rel.metodos["Gordon"].valor is not None

    # Faixa consolidada gerada apenas sobre métodos válidos
    assert rel.faixa.minimo is not None


def test_avaliar_ativo_projecao_manual(banco_valuation):
    rel = avaliar_ativo(
        banco_valuation,
        "IND3",
        lpa_projetado_manual=4.0,
        payout_projetado_manual=0.60,
        yield_minimo_projetivo=0.08,
    )
    # Dividendo = 4.0 * 0.60 = 2.40. Teto projetivo = 2.40 / 0.08 = 30.00
    assert rel.metodos["Projetivo"].valor == pytest.approx(30.0)
    assert rel.opcoes_lpa.manual == pytest.approx(4.0)


def test_formatar_tabela_valuation(banco_valuation):
    rel = avaliar_ativo(banco_valuation, "IND3")
    tabela = formatar_tabela_valuation(rel)
    assert "Bazin" in tabela
    assert "Graham" in tabela
    assert "Preço Teto" in tabela


def test_avaliar_ativo_com_ttm(banco_valuation):
    # Inserir 4 trimestres para IND3 de forma que o TTM seja calculado
    linhas_itr = [
        ("11111111000111", "100", "INDUSTRIA SA", "ITR", 2024, "DRE", "con", 1, "REAL",
         "2024-03-31", "2024-01-01", "2024-03-31", "3.11", "Lucro Líquido", 50.0),
        ("11111111000111", "100", "INDUSTRIA SA", "ITR", 2024, "DRE", "con", 1, "REAL",
         "2024-06-30", "2024-01-01", "2024-06-30", "3.11", "Lucro Líquido", 110.0),
        ("11111111000111", "100", "INDUSTRIA SA", "ITR", 2024, "DRE", "con", 1, "REAL",
         "2024-09-30", "2024-01-01", "2024-09-30", "3.11", "Lucro Líquido", 180.0),
        ("11111111000111", "100", "INDUSTRIA SA", "DFP", 2024, "DRE", "con", 2, "REAL",
         "2024-12-31", "2024-01-01", "2024-12-31", "3.11", "Lucro Líquido", 260.0),
    ]
    banco_valuation.executemany("INSERT INTO demonstracoes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", linhas_itr)

    rel = avaliar_ativo(banco_valuation, "IND3")
    assert rel.metricas_ttm is not None
    assert rel.metricas_ttm.lucro_ttm == pytest.approx(260.0)
    assert rel.metricas_ttm.lpa_ttm == pytest.approx(2.60)
    assert rel.metricas_ttm.pl_ttm == pytest.approx(25.0 / 2.60)
    assert rel.lpa_atual == pytest.approx(2.60)
    # Projeção com crescimento deve usar 2.60 * 1.05 = 2.73
    assert rel.opcoes_lpa.historico_crescimento == pytest.approx(2.60 * 1.05)

