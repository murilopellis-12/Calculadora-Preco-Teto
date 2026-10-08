"""
Funções PURAS de cálculo de métricas e indicadores fundamentalistas.

Regras do módulo:
  * NADA de rede, disco ou banco aqui. Recebe floats/int, devolve floats/None.
  * Divisões por zero ou operações matematicamente sem sentido financeiro
    (ex.: ROE com PL negativo, CAGR com base negativa) devolvem None.
  * Todas as entradas monetárias devem estar na mesma unidade (unidade da moeda).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


def lpa(lucro_liquido: float, total_acoes: float) -> float | None:
    """Lucro por Ação: Lucro Líquido / Quantidade Total de Ações em Circulação."""
    if total_acoes <= 0:
        return None
    return lucro_liquido / total_acoes


def vpa(patrimonio_liquido: float, total_acoes: float) -> float | None:
    """Valor Patrimonial por Ação: Patrimônio Líquido / Quantidade Total de Ações."""
    if total_acoes <= 0:
        return None
    return patrimonio_liquido / total_acoes


def roe(lucro_liquido: float, patrimonio_liquido: float) -> float | None:
    """Return on Equity (ROE): Lucro Líquido / Patrimônio Líquido.

    Se o patrimônio líquido for menor ou igual a zero, a empresa tem passivo
    a descoberto e o indicador perde o significado financeiro tradicional -> None.
    """
    if patrimonio_liquido <= 0:
        return None
    return lucro_liquido / patrimonio_liquido


def capital_investido(
    patrimonio_liquido: float,
    divida_liquida: float,
) -> float:
    """Capital Investido = Patrimônio Líquido + Dívida Líquida (se positiva)."""
    return patrimonio_liquido + max(0.0, divida_liquida)


def roic(
    ebit: float,
    capital_investido: float,
    aliquota_imposto: float = 0.34,
) -> float | None:
    """Return on Invested Capital (ROIC): NOPAT / Capital Investido.

    NOPAT = EBIT * (1 - alíquota_imposto). Alíquota padrão Brasil: 34% (IR + CSLL).
    Se o capital investido for menor ou igual a zero -> None.
    """
    if capital_investido <= 0 or aliquota_imposto >= 1.0 or aliquota_imposto < 0:
        return None
    nopat = ebit * (1.0 - aliquota_imposto)
    return nopat / capital_investido


def margem_bruta(resultado_bruto: float, receita_liquida: float) -> float | None:
    """Margem Bruta: Resultado Bruto / Receita Líquida."""
    if receita_liquida <= 0:
        return None
    return resultado_bruto / receita_liquida


def margem_ebit(ebit: float, receita_liquida: float) -> float | None:
    """Margem EBIT (Operacional): EBIT / Receita Líquida."""
    if receita_liquida <= 0:
        return None
    return ebit / receita_liquida


def margem_liquida(lucro_liquido: float, receita_liquida: float) -> float | None:
    """Margem Líquida: Lucro Líquido / Receita Líquida."""
    if receita_liquida <= 0:
        return None
    return lucro_liquido / receita_liquida


def divida_liquida(divida_bruta: float, caixa_equivalentes: float) -> float:
    """Dívida Líquida = Dívida Bruta (CP + LP) - Caixa e Equivalentes.

    Pode ser negativa quando a empresa tem mais caixa que dívida (caixa líquido).
    """
    return divida_bruta - caixa_equivalentes


def ebitda(ebit: float, depreciacao_amortizacao: float = 0.0) -> float:
    """EBITDA = EBIT + Depreciação e Amortização."""
    return ebit + abs(depreciacao_amortizacao)


def divida_liquida_ebitda(divida_liquida: float, ebitda: float) -> float | None:
    """Alavancagem financeira: Dívida Líquida / EBITDA.

    Se o EBITDA for menor ou igual a zero, o múltiplo é indefinido -> None.
    """
    if ebitda <= 0:
        return None
    return divida_liquida / ebitda


def payout(proventos_totais: float, lucro_liquido: float) -> float | None:
    """Payout: Proventos Distribuídos (Dividendos + JCP) / Lucro Líquido.

    Se o lucro for menor ou igual a zero, o payout é indefinido -> None.
    """
    if lucro_liquido <= 0 or proventos_totais < 0:
        return None
    return proventos_totais / lucro_liquido


def cagr(valor_inicial: float, valor_final: float, periodos: int) -> float | None:
    """Taxa de Crescimento Anual Composta (CAGR): (final / inicial) ** (1/n) - 1.

    Exige períodos > 0 e valores positivos para que a taxa geométrica seja real.
    """
    if periodos <= 0 or valor_inicial <= 0 or valor_final <= 0:
        return None
    return (valor_final / valor_inicial) ** (1.0 / periodos) - 1.0


def ajustar_escala_acoes(
    qt_total: float,
    qt_tesouro: float = 0.0,
    lucro_liquido: float | None = None,
    lpa_referencia: float | None = None,
) -> float:
    """Ajusta a quantidade de ações descontando tesouraria e corrigindo escala se necessário.

    Quirk CVM: algumas companhias reportam a quantidade de ações do formulário
    de capital em milhares (ex.: Vale reportou 4,5M em vez de 4,5B; Bradesco em 2023
    reportou 10,6M e em 2024 reportou 10,6B).
    Se as ações líquidas forem muito baixas (< 50 milhões) para uma empresa com lucro
    bilionário (lucro > R$ 100M), o LPA direto ficaria em milhares de reais por ação.
    Nesses casos, a quantidade é multiplicada por 1.000 para refletir unidades reais.
    """
    liquidas = max(0.0, qt_total - max(0.0, qt_tesouro))
    if liquidas <= 0:
        return 0.0

    # Heurística automática de escala:
    # Se uma empresa tem lucro > 100 milhões e menos de 50 milhões de ações,
    # ou se temos lpa_referencia e a proporção sugere fator 1.000:
    if lpa_referencia and lpa_referencia > 0 and lucro_liquido and lucro_liquido > 0:
        lpa_bruto = lucro_liquido / liquidas
        if 500 <= (lpa_bruto / lpa_referencia) <= 2000:
            return liquidas * 1_000.0
    elif lucro_liquido and abs(lucro_liquido) >= 100_000_000 and liquidas < 50_000_000:
        # Empresas de grande porte com capital em milhares
        return liquidas * 1_000.0

    return liquidas


@dataclass(frozen=True)
class MetricasAno:
    """Conjunto de indicadores calculados para um período (ano)."""
    ano: int
    lpa: float | None = None
    vpa: float | None = None
    roe: float | None = None
    roic: float | None = None
    margem_bruta: float | None = None
    margem_ebit: float | None = None
    margem_liquida: float | None = None
    divida_liquida: float | None = None
    ebitda: float | None = None
    divida_liquida_ebitda: float | None = None
    payout: float | None = None
    observacoes: list[str] = field(default_factory=list)

    def para_dicionario(self) -> dict[str, float | int | None]:
        return {
            "ano": self.ano,
            "lpa": self.lpa,
            "vpa": self.vpa,
            "roe": self.roe,
            "roic": self.roic,
            "margem_bruta": self.margem_bruta,
            "margem_ebit": self.margem_ebit,
            "margem_liquida": self.margem_liquida,
            "divida_liquida": self.divida_liquida,
            "ebitda": self.ebitda,
            "divida_liquida_ebitda": self.divida_liquida_ebitda,
            "payout": self.payout,
        }
