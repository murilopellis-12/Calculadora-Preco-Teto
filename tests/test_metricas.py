"""Testes unitários das funções puras de métricas e indicadores."""
import pytest

from src.metricas import (
    MetricasAno,
    ajustar_escala_acoes,
    cagr,
    capital_investido,
    divida_liquida,
    divida_liquida_ebitda,
    ebitda,
    lpa,
    margem_bruta,
    margem_ebit,
    margem_liquida,
    payout,
    roe,
    roic,
    vpa,
)


def test_lpa_calculo_basico():
    # Lucro R$ 10.000, 1.000 ações -> R$ 10/ação
    assert lpa(10_000.0, 1_000.0) == pytest.approx(10.0)


def test_lpa_acoes_invalidas_retorna_none():
    assert lpa(10_000.0, 0.0) is None
    assert lpa(10_000.0, -50.0) is None


def test_vpa_calculo_basico():
    # PL R$ 50.000, 2.000 ações -> R$ 25/ação
    assert vpa(50_000.0, 2_000.0) == pytest.approx(25.0)


def test_vpa_acoes_invalidas_retorna_none():
    assert vpa(50_000.0, 0.0) is None
    assert vpa(50_000.0, -100.0) is None


def test_roe_calculo_basico():
    # Lucro R$ 15M, PL R$ 100M -> 15%
    assert roe(15_000_000.0, 100_000_000.0) == pytest.approx(0.15)


def test_roe_lucro_negativo():
    # Prejuízo R$ -10M, PL R$ 100M -> -10%
    assert roe(-10_000_000.0, 100_000_000.0) == pytest.approx(-0.10)


def test_roe_patrimonio_liquido_negativo_ou_zero():
    # Passivo a descoberto
    assert roe(10_000_000.0, 0.0) is None
    assert roe(10_000_000.0, -5_000_000.0) is None


def test_capital_investido():
    # PL 100 + Dívida Líquida positiva 20 = 120
    assert capital_investido(100.0, 20.0) == pytest.approx(120.0)
    # Caixa líquido (dívida líquida negativa) não reduz o PL para baixo
    assert capital_investido(100.0, -30.0) == pytest.approx(100.0)


def test_roic_calculo_basico():
    # EBIT 100, imposto 34% -> NOPAT 66. Capital investido 330 -> ROIC = 20%
    assert roic(100.0, 330.0, aliquota_imposto=0.34) == pytest.approx(0.20)


def test_roic_capital_ou_aliquota_invalida():
    assert roic(100.0, 0.0) is None
    assert roic(100.0, -50.0) is None
    assert roic(100.0, 100.0, aliquota_imposto=1.1) is None
    assert roic(100.0, 100.0, aliquota_imposto=-0.1) is None


def test_margens():
    # Receita 1.000, Bruto 400, EBIT 200, Líquido 100
    assert margem_bruta(400.0, 1_000.0) == pytest.approx(0.40)
    assert margem_ebit(200.0, 1_000.0) == pytest.approx(0.20)
    assert margem_liquida(100.0, 1_000.0) == pytest.approx(0.10)


def test_margens_receita_invalida():
    assert margem_bruta(100.0, 0.0) is None
    assert margem_ebit(100.0, -500.0) is None
    assert margem_liquida(100.0, 0.0) is None


def test_divida_liquida_e_ebitda():
    # Dívida Bruta 500, Caixa 200 -> Dívida Líquida 300
    assert divida_liquida(500.0, 200.0) == pytest.approx(300.0)
    # Caixa líquido
    assert divida_liquida(200.0, 500.0) == pytest.approx(-300.0)

    # EBITDA: EBIT 150 + Depreciação 50 = 200
    assert ebitda(150.0, 50.0) == pytest.approx(200.0)
    assert ebitda(150.0, -50.0) == pytest.approx(200.0)

    # Dívida Líquida / EBITDA: 300 / 200 = 1.5x
    assert divida_liquida_ebitda(300.0, 200.0) == pytest.approx(1.5)
    # EBITDA zero ou negativo -> None
    assert divida_liquida_ebitda(300.0, 0.0) is None
    assert divida_liquida_ebitda(300.0, -100.0) is None


def test_payout():
    # Proventos 40M, Lucro 100M -> 40%
    assert payout(40_000_000.0, 100_000_000.0) == pytest.approx(0.40)
    # Lucro zero ou prejuízo -> None
    assert payout(10_000_000.0, 0.0) is None
    assert payout(10_000_000.0, -20_000_000.0) is None
    # Proventos negativos -> None
    assert payout(-5.0, 100.0) is None


def test_cagr():
    # 100 crescendo para 121 em 2 anos = 10% a.a.
    assert cagr(100.0, 121.0, 2) == pytest.approx(0.10)
    # Períodos inválidos ou bases negativas
    assert cagr(100.0, 121.0, 0) is None
    assert cagr(100.0, 121.0, -1) is None
    assert cagr(-50.0, 100.0, 3) is None
    assert cagr(100.0, -50.0, 3) is None


def test_ajustar_escala_acoes():
    # Empresa em unidades: WEG com 4,19B ações e 1M em tesouraria
    acoes_weg = ajustar_escala_acoes(4_197_317_998.0, 1_780_620.0, lucro_liquido=6_318_763_000.0)
    assert acoes_weg == pytest.approx(4_195_537_378.0)

    # Empresa reportando em milhares: Vale com 4.539.007 reportado e lucro de 30 bilhões
    acoes_vale = ajustar_escala_acoes(4_539_008.0, 270_288.0, lucro_liquido=30_431_000_000.0)
    # Deve detectar a escala em milhares e multiplicar por 1.000:
    assert acoes_vale == pytest.approx(4_268_720_000.0)


def test_metricas_ano_dataclass():
    m = MetricasAno(
        ano=2024,
        lpa=2.5,
        vpa=20.0,
        roe=0.125,
        roic=0.15,
        margem_bruta=0.35,
        margem_ebit=0.20,
        margem_liquida=0.15,
        divida_liquida=100_000.0,
        ebitda=50_000.0,
        divida_liquida_ebitda=2.0,
        payout=0.40,
    )
    d = m.para_dicionario()
    assert d["ano"] == 2024
    assert d["lpa"] == 2.5
    assert d["roe"] == 0.125
    assert d["divida_liquida_ebitda"] == 2.0
