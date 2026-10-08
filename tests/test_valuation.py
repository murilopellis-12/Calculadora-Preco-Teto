import math
import pytest

from src.valuation import (
    FaixaPrecoTeto,
    PrecoTeto,
    bazin,
    calcular_faixa_teto,
    fluxo_caixa_descontado,
    gordon,
    gordon_com_roe,
    graham,
    margem_de_seguranca,
    projetivo,
)


def test_bazin():
    assert bazin(1.2, 0.06).valor == pytest.approx(20.0)
    assert bazin(0).valor is None


def test_projetivo():
    # Via dividendo projetado direto (padrão yield 6%)
    res1 = projetivo(dividendo_projetado=1.8, yield_minimo=0.06)
    assert res1.valor == pytest.approx(30.0)

    # Via LPA projetado e payout projetado: LPA R$ 4.00, Payout 50% -> Dividendo R$ 2.00 / 6% = R$ 33.33
    res2 = projetivo(lpa_projetado=4.0, payout_projetado=0.50, yield_minimo=0.06)
    assert res2.valor == pytest.approx(33.3333333)

    # Yield negativo ou zero
    assert projetivo(dividendo_projetado=2.0, yield_minimo=0.0).valor is None
    # Dividendo não positivo
    assert projetivo(dividendo_projetado=-1.0).valor is None


def test_graham():
    assert graham(2, 20).valor == pytest.approx(30.0)
    assert graham(-1, 20).valor is None


def test_gordon():
    assert gordon(1.0, 0.10, 0.04).valor == pytest.approx(1.04 / 0.06)
    assert gordon(1.0, 0.04, 0.05).valor is None


def test_gordon_com_roe():
    # LPA R$ 3.00, ROE 15%, Payout 60% -> g = 15% * 40% = 6%. Taxa desconto 10%
    # D0 = 3.00 * 0.60 = 1.80. Teto = 1.80 * (1 + 0.06) / (0.10 - 0.06) = 1.908 / 0.04 = 47.70
    res = gordon_com_roe(lpa=3.0, roe=0.15, payout=0.60, taxa_desconto=0.10)
    assert res.valor == pytest.approx(47.70)

    # g >= taxa_desconto deve retornar None
    res_invalido = gordon_com_roe(lpa=3.0, roe=0.30, payout=0.50, taxa_desconto=0.10)
    assert res_invalido.valor is None


def test_dcf():
    # fluxos 100, 100; r=10%; g=0; sem dívida; 10 ações -> EV = 1000 -> R$ 100/ação
    r = fluxo_caixa_descontado([100, 100], 0.10, 0.0, 0.0, 10)
    assert r.valor == pytest.approx(100.0)


def test_margem():
    assert margem_de_seguranca(80, 100) == pytest.approx(0.2)
    assert margem_de_seguranca(80, None) is None
    assert margem_de_seguranca(-10, 100) is None


def test_calcular_faixa_teto():
    metodos = {
        "Bazin": PrecoTeto("Bazin", 20.0),
        "Graham": PrecoTeto("Graham", 30.0),
        "Projetivo": PrecoTeto("Projetivo", 40.0),
        "Invalido": PrecoTeto("Invalido", None, observacao="Não aplicável"),
    }
    preco_atual = 25.0
    faixa = calcular_faixa_teto(metodos, preco_atual=preco_atual)

    assert faixa.minimo == pytest.approx(20.0)
    assert faixa.medio == pytest.approx(30.0)
    assert faixa.maximo == pytest.approx(40.0)
    assert faixa.preco_atual == pytest.approx(25.0)

    # Margem mínima: 1 - 25/20 = -0.25 (preço 25 está 25% acima do teto de 20)
    assert faixa.margem_minima == pytest.approx(-0.25)
    # Margem média: 1 - 25/30 = +0.1667 (16.7% de folga)
    assert faixa.margem_media == pytest.approx(1.0 - 25.0 / 30.0)
    # Margem máxima: 1 - 25/40 = +0.375 (37.5% de folga)
    assert faixa.margem_maxima == pytest.approx(0.375)
