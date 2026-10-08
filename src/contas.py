"""
Dicionário único de contas-chave (cd_conta) dos demonstrativos da CVM.

Mapeia nomes padronizados para códigos de conta. Duas variantes:
  * CONTAS — empresas não-financeiras (indústria, energia, mineração…)
  * CONTAS_FINANCEIRAS — bancos e seguradoras (plano de contas diferente)

Diferença principal na prática:
  * Patrimônio Líquido → 2.03 (empresa normal) vs 2.07 (banco)
  * DRE → "Receita de Venda" vs "Receitas de Intermediação Financeira"
  * Graham e DCF tradicionais não se aplicam a bancos da mesma forma.
"""
from __future__ import annotations

# ------------------------------------------------------------------ #
# Contas para empresas NÃO financeiras
# ------------------------------------------------------------------ #
CONTAS: dict[str, str] = {
    # DRE
    "receita":              "3.01",  # Receita de Venda de Bens e/ou Serviços
    "custo":                "3.02",  # Custo dos Bens e/ou Serviços Vendidos
    "resultado_bruto":      "3.03",  # Resultado Bruto
    "ebit":                 "3.05",  # Resultado Antes do Result. Financeiro e dos Tributos
    "resultado_financeiro": "3.06",  # Resultado Financeiro
    "lucro_antes_ir":       "3.07",  # Resultado Antes dos Tributos sobre o Lucro
    "ir_csll":              "3.08",  # Imposto de Renda e Contribuição Social
    "lucro_liquido":        "3.11",  # Lucro/Prejuízo Consolidado do Período
    "lpa_cvm":              "3.99",  # Lucro por Ação (informativo, calculado pela empresa)

    # BPA (ativo)
    "ativo_total":          "1",
    "ativo_circulante":     "1.01",
    "caixa_equivalentes":   "1.01.01",  # Caixa e Equivalentes de Caixa
    "ativo_nao_circulante": "1.02",

    # BPP (passivo + PL)
    "passivo_total":            "2",
    "passivo_circulante":       "2.01",
    "divida_cp":                "2.01.04",  # Empréstimos e Financiamentos Circulante
    "passivo_nao_circulante":   "2.02",
    "divida_lp":                "2.02.01",  # Empréstimos e Financiamentos Não Circulante
    "patrimonio_liquido":       "2.03",

    # DFC — método indireto
    "caixa_operacional":    "6.01",
    "caixa_investimento":   "6.02",
    "caixa_financiamento":  "6.03",
}

# ------------------------------------------------------------------ #
# Ajustes para financeiras (bancos, seguradoras)
# ------------------------------------------------------------------ #
CONTAS_FINANCEIRAS: dict[str, str] = {
    **CONTAS,
    "patrimonio_liquido": "2.07",
    # DRE: 3.01 é "Receitas de Intermediação Financeira" e 3.02 é
    # "Despesas de Intermediação Financeira".
}

# Demonstrações de fluxo (acumuladas no ano nos ITRs) vs foto (balanço).
DEMOS_FLUXO = frozenset({"DRE", "DFC_MD", "DFC_MI", "DVA"})
DEMOS_FOTO = frozenset({"BPA", "BPP"})

# Mapa demonstração → tipo para consultas rápidas.
DEMONSTRACAO_TIPO = {
    "DRE": "fluxo", "DFC_MD": "fluxo", "DFC_MI": "fluxo", "DVA": "fluxo",
    "BPA": "foto", "BPP": "foto",
}
