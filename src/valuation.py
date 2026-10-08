"""
Motor de preço teto: cada método é uma função PURA (entradas -> resultado).

Regras do módulo:
  * Nada de rede/banco aqui. Quem chama (camada de métricas/app) busca os dados.
  * Toda função devolve um `PrecoTeto` com as premissas usadas e uma observação,
    para o app poder EXPLICAR o número em vez de só mostrá-lo.
  * Se o método não se aplica (ex.: Graham com lucro negativo, bancos para DCF),
    `valor` é None e `observacao` diz por quê. Nunca invente número.
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


@dataclass(frozen=True)
class FaixaPrecoTeto:
    """Faixa consolidada de preço teto obtida a partir de múltiplos métodos."""
    minimo: float | None
    medio: float | None
    maximo: float | None
    preco_atual: float | None = None
    margem_minima: float | None = None
    margem_media: float | None = None
    margem_maxima: float | None = None
    metodos: dict[str, PrecoTeto] = field(default_factory=dict)


def bazin(dividendo_medio_anual: float, yield_alvo: float = 0.06) -> PrecoTeto:
    """Bazin: preço máximo para receber `yield_alvo` em dividendos.

    teto = dividendo médio anual por ação / yield alvo (padrão: 6%).
    """
    premissas = {"dividendo_medio_anual": dividendo_medio_anual, "yield_alvo": yield_alvo}
    if dividendo_medio_anual <= 0 or yield_alvo <= 0:
        return PrecoTeto("Bazin", None, premissas, "Sem dividendos positivos no período.")
    return PrecoTeto("Bazin", dividendo_medio_anual / yield_alvo, premissas)


def projetivo(
    lpa_projetado: float | None = None,
    payout_projetado: float | None = None,
    dividendo_projetado: float | None = None,
    yield_minimo: float = 0.06,
    origem_projecao: str = "manual",
) -> PrecoTeto:
    """Preço teto projetivo: dividendo PROJETADO / yield mínimo (padrão 6%).

    O dividendo projetado pode ser informado diretamente ou calculado via:
        dividendo_projetado = lpa_projetado * payout_projetado
    """
    if dividendo_projetado is None:
        if lpa_projetado is not None and payout_projetado is not None:
            dividendo_projetado = lpa_projetado * payout_projetado

    premissas = {
        "lpa_projetado": lpa_projetado,
        "payout_projetado": payout_projetado,
        "dividendo_projetado": dividendo_projetado,
        "yield_minimo": yield_minimo,
        "origem_projecao": origem_projecao,
    }

    if dividendo_projetado is None or dividendo_projetado <= 0:
        return PrecoTeto(
            "Projetivo",
            None,
            premissas,
            "Dividendo projetado não informado ou não positivo.",
        )
    if yield_minimo <= 0:
        return PrecoTeto("Projetivo", None, premissas, "Yield mínimo deve ser positivo.")

    teto = dividendo_projetado / yield_minimo
    return PrecoTeto("Projetivo", teto, premissas)


def graham(lpa: float, vpa: float, multiplo: float = 22.5) -> PrecoTeto:
    """Número de Graham: raiz(22,5 x LPA x VPA). Exige lucro e patrimônio positivos.

    Nota: Não se aplica a bancos e financeiras.
    """
    premissas = {"lpa": lpa, "vpa": vpa, "multiplo": multiplo}
    if lpa <= 0 or vpa <= 0:
        return PrecoTeto("Graham", None, premissas, "LPA ou VPA não positivo: método não se aplica.")
    return PrecoTeto("Graham", math.sqrt(multiplo * lpa * vpa), premissas)


def gordon(dividendo_atual: float, taxa_desconto: float = 0.10, crescimento: float = 0.04) -> PrecoTeto:
    """Modelo de Gordon (dividendos na perpetuidade): D0 x (1+g) / (r - g)."""
    premissas = {"dividendo_atual": dividendo_atual, "taxa_desconto": taxa_desconto, "crescimento": crescimento}
    if taxa_desconto <= crescimento:
        return PrecoTeto("Gordon", None, premissas, "Taxa de desconto deve ser estritamente maior que o crescimento.")
    if dividendo_atual <= 0:
        return PrecoTeto("Gordon", None, premissas, "Dividendo atual não positivo.")
    return PrecoTeto("Gordon", dividendo_atual * (1 + crescimento) / (taxa_desconto - crescimento), premissas)


def gordon_com_roe(
    lpa: float,
    roe: float,
    payout: float,
    taxa_desconto: float = 0.10,
) -> PrecoTeto:
    """Modelo de Gordon com taxa interna de crescimento: g = ROE * (1 - payout).

    Adequado tanto para empresas industriais estáveis quanto para bancos (DDM).
    """
    premissas = {"lpa": lpa, "roe": roe, "payout": payout, "taxa_desconto": taxa_desconto}
    if lpa <= 0 or roe <= 0 or payout <= 0 or payout >= 1.0:
        return PrecoTeto("Gordon DDM", None, premissas, "LPA, ROE ou Payout fora do intervalo válido.")

    g = roe * (1.0 - payout)
    if taxa_desconto <= g:
        return PrecoTeto(
            "Gordon DDM",
            None,
            premissas,
            f"Taxa de desconto ({taxa_desconto*100:.1f}%) não supera o crescimento g ({g*100:.1f}%).",
        )

    d0 = lpa * payout
    teto = d0 * (1.0 + g) / (taxa_desconto - g)
    premissas["g_estimado"] = g
    premissas["dividendo_base"] = d0
    return PrecoTeto("Gordon DDM", teto, premissas)


def fluxo_caixa_descontado(
    fluxos_projetados: Sequence[float],
    taxa_desconto: float,
    crescimento_perpetuidade: float,
    divida_liquida: float,
    numero_acoes: float,
) -> PrecoTeto:
    """DCF (fluxo de caixa livre da firma): valor presente dos fluxos + valor terminal.

    equity = EV - dívida líquida;  teto = equity / nº de ações.
    Não se aplica a bancos e financeiras.
    """
    premissas = {
        "fluxos_projetados": list(fluxos_projetados),
        "taxa_desconto": taxa_desconto,
        "crescimento_perpetuidade": crescimento_perpetuidade,
        "divida_liquida": divida_liquida,
        "numero_acoes": numero_acoes,
    }
    if not fluxos_projetados or numero_acoes <= 0:
        return PrecoTeto("DCF", None, premissas, "Sem fluxos projetados ou número de ações inválido.")
    if taxa_desconto <= crescimento_perpetuidade:
        return PrecoTeto("DCF", None, premissas, "Taxa de desconto deve ser maior que o crescimento da perpetuidade.")

    n = len(fluxos_projetados)
    vp_fluxos = sum(f / (1 + taxa_desconto) ** t for t, f in enumerate(fluxos_projetados, start=1))
    valor_terminal = (
        fluxos_projetados[-1]
        * (1 + crescimento_perpetuidade)
        / (taxa_desconto - crescimento_perpetuidade)
    )
    ev = vp_fluxos + valor_terminal / (1 + taxa_desconto) ** n
    equity = ev - divida_liquida
    if equity <= 0:
        return PrecoTeto("DCF", None, premissas, "Equity negativo: dívida líquida supera o valor da firma.")

    return PrecoTeto("DCF", equity / numero_acoes, premissas)


def margem_de_seguranca(preco_atual: float, preco_teto: float | None) -> float | None:
    """Folga entre o preço e o teto: positiva = preço abaixo do teto. Ex.: 0.20 = 20% de margem."""
    if preco_teto is None or preco_teto <= 0 or preco_atual <= 0:
        return None
    return 1.0 - preco_atual / preco_teto


def calcular_faixa_teto(
    metodos: dict[str, PrecoTeto],
    preco_atual: float | None = None,
) -> FaixaPrecoTeto:
    """Calcula a faixa de preço teto (Mínimo, Médio e Máximo) entre os métodos aplicáveis."""
    valores_validos = [m.valor for m in metodos.values() if m.valor is not None and m.valor > 0]

    if not valores_validos:
        return FaixaPrecoTeto(
            minimo=None,
            medio=None,
            maximo=None,
            preco_atual=preco_atual,
            metodos=metodos,
        )

    teto_min = min(valores_validos)
    teto_med = sum(valores_validos) / len(valores_validos)
    teto_max = max(valores_validos)

    marg_min = margem_de_seguranca(preco_atual, teto_min) if preco_atual else None
    marg_med = margem_de_seguranca(preco_atual, teto_med) if preco_atual else None
    marg_max = margem_de_seguranca(preco_atual, teto_max) if preco_atual else None

    return FaixaPrecoTeto(
        minimo=teto_min,
        medio=teto_med,
        maximo=teto_max,
        preco_atual=preco_atual,
        margem_minima=marg_min,
        margem_media=marg_med,
        margem_maxima=marg_max,
        metodos=metodos,
    )
