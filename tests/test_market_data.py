"""Testes unitários e de integração para o módulo market_data."""
from datetime import date

import duckdb
import pytest

from src.db import ESQUEMA
from src.market_data import (
    calcular_dividend_yield,
    calcular_dividendo_medio,
    calcular_preco_lucro,
    calcular_preco_vpa,
    gravar_cotacao,
    gravar_provento,
    obter_cotacao_mais_recente,
    obter_proventos_ultimos_anos,
)


# --------------------------------------------------------------------------- #
# Testes das funções PURAS
# --------------------------------------------------------------------------- #
def test_calcular_dividend_yield():
    # Provento R$ 3.00, Preço R$ 50.00 -> 6% DY
    assert calcular_dividend_yield(3.0, 50.0) == pytest.approx(0.06)
    assert calcular_dividend_yield(0.0, 50.0) == pytest.approx(0.0)
    # Preço inválido
    assert calcular_dividend_yield(3.0, 0.0) is None
    assert calcular_dividend_yield(3.0, -10.0) is None
    # Provento negativo
    assert calcular_dividend_yield(-1.0, 50.0) is None


def test_calcular_preco_lucro():
    # Preço R$ 20.00, LPA R$ 2.00 -> P/L 10.0x
    assert calcular_preco_lucro(20.0, 2.0) == pytest.approx(10.0)
    # LPA zero ou prejuízo
    assert calcular_preco_lucro(20.0, 0.0) is None
    assert calcular_preco_lucro(20.0, -2.0) is None
    assert calcular_preco_lucro(20.0, None) is None


def test_calcular_preco_vpa():
    # Preço R$ 30.00, VPA R$ 15.00 -> P/VP 2.0x
    assert calcular_preco_vpa(30.0, 15.0) == pytest.approx(2.0)
    # VPA zero ou negativo
    assert calcular_preco_vpa(30.0, 0.0) is None
    assert calcular_preco_vpa(30.0, -5.0) is None
    assert calcular_preco_vpa(30.0, None) is None


def test_calcular_dividendo_medio():
    assert calcular_dividendo_medio([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert calcular_dividendo_medio([1.0, 0.0, None, 3.0]) == pytest.approx(2.0)
    assert calcular_dividendo_medio([]) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Testes de persistência no DuckDB
# --------------------------------------------------------------------------- #
@pytest.fixture
def banco_teste(tmp_path):
    caminho = tmp_path / "teste_market.duckdb"
    con = duckdb.connect(str(caminho))
    for cmd in ESQUEMA:
        con.execute(cmd)
    yield con
    con.close()


def test_gravar_e_obter_cotacao_idempotente(banco_teste):
    hoje = date(2026, 10, 8)
    # Grava cotação
    gravar_cotacao(banco_teste, "TEST3", 50.0, data_ref=hoje)
    preco, dt = obter_cotacao_mais_recente(banco_teste, "TEST3")
    assert preco == pytest.approx(50.0)
    assert dt == hoje

    # Gravar novamente na mesma data deve atualizar sem duplicar (idempotente)
    gravar_cotacao(banco_teste, "TEST3", 52.5, data_ref=hoje)
    total_linhas = banco_teste.execute(
        "SELECT count(*) FROM cotacoes WHERE ticker = 'TEST3'"
    ).fetchone()[0]
    assert total_linhas == 1

    preco_atual, _ = obter_cotacao_mais_recente(banco_teste, "TEST3")
    assert preco_atual == pytest.approx(52.5)


def test_gravar_e_obter_proventos_idempotente(banco_teste):
    d1 = date(2024, 12, 31)
    d2 = date(2025, 12, 31)

    gravar_provento(banco_teste, "TEST3", d1, "DIVIDENDO", 2.0)
    gravar_provento(banco_teste, "TEST3", d2, "DIVIDENDO", 3.0)

    # Re-inserção não duplica
    gravar_provento(banco_teste, "TEST3", d2, "DIVIDENDO", 3.0)

    provs = obter_proventos_ultimos_anos(banco_teste, "TEST3", anos=5)
    # Ordenado do mais recente para o mais antigo: 3.0, 2.0
    assert provs == [3.0, 2.0]
    assert calcular_dividendo_medio(provs) == pytest.approx(2.5)
