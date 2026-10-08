"""
Motor de preço teto: cada método é uma função PURA (entradas -> resultado).

Regras do módulo:
  * Nada de rede/banco aqui. Quem chama (camada de métricas/app) busca os dados.
  * Toda função devolve um `PrecoTeto` com as premissas usadas e uma observação,
    para o app poder EXPLICAR o número em vez de só mostrá-lo.
  * Se o método não se aplica (ex.: Graham com lucro negativo), `valor` é None
    e `observacao` diz por quê.

Para criar um método novo: copie uma função, ajuste a fórmula, escreva o teste.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class PrecoTeto:
    metodo: str
    valor: float | None
    premissas: dict = field(default_factory=dict)
    observacao: str = ""


def bazin(dividendo_medio_anual: float, yield_alvo: float = 0.06) -> PrecoTeto:
    """Bazin: preço máximo para receber `yield_alvo` em dividendos.

    teto = dividendo médio anual por ação / yield alvo (clássico: média de 5 anos, 6%).
    """
    premissas = {"dividendo_medio_anual": dividendo_medio_anual, "yield_alvo": yield_alvo}
    if dividendo_medio_anual <= 0 or yield_alvo <= 0:
        return PrecoTeto("Bazin", None, premissas, "Sem dividendos positivos no período.")
    return PrecoTeto("Bazin", dividendo_medio_anual / yield_alvo, premissas)


def projetivo(dividendo_projetado: float, yield_minimo: float = 0.08) -> PrecoTeto:
    """Preço teto projetivo: dividendo PROJETADO (não histórico) / yield mínimo.

    ATENÇÃO: esta é uma versão mínima. A forma de projetar o dividendo (lucro
    projetado x payout, crescimento etc.) deve ser definida com o dono do
    projeto antes de ser implementada de verdade.
    """
    premissas = {"dividendo_projetado": dividendo_projetado, "yield_minimo": yield_minimo}
    if dividendo_projetado <= 0 or yield_minimo <= 0:
        return PrecoTeto("Projetivo", None, premissas, "Dividendo projetado não positivo.")
    return PrecoTeto("Projetivo", dividendo_projetado / yield_minimo, premissas)


def graham(lpa: float, vpa: float, multiplo: float = 22.5) -> PrecoTeto:
    """Número de Graham: raiz(22,5 x LPA x VPA). Exige lucro e patrimônio positivos."""
    premissas = {"lpa": lpa, "vpa": vpa, "multiplo": multiplo}
    if lpa <= 0 or vpa <= 0:
        return PrecoTeto("Graham", None, premissas, "LPA ou VPA não positivo: método não se aplica.")
    return PrecoTeto("Graham", math.sqrt(multiplo * lpa * vpa), premissas)


def gordon(dividendo_atual: float, taxa_desconto: float, crescimento: float) -> PrecoTeto:
    """Modelo de Gordon (dividendos crescentes na perpetuidade): D0 x (1+g) / (r - g)."""
    premissas = {"dividendo_atual": dividendo_atual, "taxa_desconto": taxa_desconto, "crescimento": crescimento}
    if taxa_desconto <= crescimento:
        return PrecoTeto("Gordon", None, premissas, "Taxa de desconto deve ser maior que o crescimento.")
    if dividendo_atual <= 0:
        return PrecoTeto("Gordon", None, premissas, "Dividendo atual não positivo.")
    return PrecoTeto("Gordon", dividendo_atual * (1 + crescimento) / (taxa_desconto - crescimento), premissas)


def fluxo_caixa_descontado(
    fluxos_projetados: Sequence[float],
    taxa_desconto: float,
    crescimento_perpetuidade: float,
    divida_liquida: float,
    numero_acoes: float,
) -> PrecoTeto:
    """DCF (fluxo de caixa livre da firma): valor presente dos fluxos + valor terminal.

    equity = EV - dívida líquida;  teto = equity / nº de ações.
    """
    premissas = {
        "fluxos_projetados": list(fluxos_projetados), "taxa_desconto": taxa_desconto,
        "crescimento_perpetuidade": crescimento_perpetuidade,
        "divida_liquida": divida_liquida, "numero_acoes": numero_acoes,
    }
    if not fluxos_projetados or numero_acoes <= 0:
        return PrecoTeto("DCF", None, premissas, "Sem fluxos projetados ou número de ações inválido.")
    if taxa_desconto <= crescimento_perpetuidade:
        return PrecoTeto("DCF", None, premissas, "Taxa de desconto deve ser maior que o crescimento da perpetuidade.")

    n = len(fluxos_projetados)
    vp_fluxos = sum(f / (1 + taxa_desconto) ** t for t, f in enumerate(fluxos_projetados, start=1))
    valor_terminal = fluxos_projetados[-1] * (1 + crescimento_perpetuidade) / (taxa_desconto - crescimento_perpetuidade)
    ev = vp_fluxos + valor_terminal / (1 + taxa_desconto) ** n
    return PrecoTeto("DCF", (ev - divida_liquida) / numero_acoes, premissas)


def margem_de_seguranca(preco_atual: float, preco_teto: float | None) -> float | None:
    """Folga entre o preço e o teto: positiva = preço abaixo do teto. Ex.: 0.20 = 20% de margem."""
    if preco_teto is None or preco_teto <= 0:
        return None
    return 1 - preco_atual / preco_teto
