import math

import pytest

from src.valuation import bazin, fluxo_caixa_descontado, gordon, graham, margem_de_seguranca, projetivo


def test_bazin():
    assert bazin(1.2, 0.06).valor == pytest.approx(20.0)
    assert bazin(0).valor is None


def test_projetivo():
    assert projetivo(2.0, 0.08).valor == pytest.approx(25.0)


def test_graham():
    assert graham(2, 20).valor == pytest.approx(30.0)
    assert graham(-1, 20).valor is None


def test_gordon():
    assert gordon(1.0, 0.10, 0.04).valor == pytest.approx(1.04 / 0.06)
    assert gordon(1.0, 0.04, 0.05).valor is None


def test_dcf():
    # fluxos 100, 100; r=10%; g=0; sem dívida; 10 ações -> EV = 1000 -> R$ 100/ação
    r = fluxo_caixa_descontado([100, 100], 0.10, 0.0, 0.0, 10)
    assert r.valor == pytest.approx(100.0)


def test_margem():
    assert margem_de_seguranca(80, 100) == pytest.approx(0.2)
    assert margem_de_seguranca(80, None) is None
