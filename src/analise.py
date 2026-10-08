"""
Motor de Preço Teto Completo (Fase 5 do Roadmap).

Integra os dados fundamentalistas da CVM (DuckDB), as cotações de mercado e o
motor de valuation puro (`src.valuation`) para calcular e exibir:
  1. Métodos de Preço Teto lado a lado (Bazin, Graham, Gordon, DCF, Projetivo);
  2. Faixa de Preço Teto (Mínimo, Médio e Máximo);
  3. Margem de Segurança de cada método em relação à cotação atual;
  4. 3 opções de projeção de LPA (Analistas/Mercado, Histórico CVM, Manual);
  5. Tratamento dedicado para Instituições Financeiras (Bancos/Seguradoras).

Uso via linha de comando:
    python -m src.analise --ticker WEGE3
    python -m src.analise --ticker BBDC3
    python -m src.analise --todos
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from src.db import conectar
from src.indicadores import (
    eh_instituicao_financeira,
    identificar_empresa,
    obter_historico_empresa,
    obter_metricas_ttm,
)
from src.metricas import MetricasTTM
from src.market_data import (
    calcular_dividendo_medio,
    obter_cotacao_mais_recente,
    obter_cotacao_recente,
    obter_proventos_ultimos_anos,
)
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

log = logging.getLogger("analise")


@dataclass
class OpcoesProjecaoLPA:
    """As 3 opções de projeção para o LPA futuro."""
    analistas_mercado: float | None = None
    origem_mercado: str = "Indisponível"
    historico_crescimento: float | None = None
    historico_media_3a: float | None = None
    manual: float | None = None


@dataclass
class RelatorioValuation:
    """Resultado completo da análise de valuation de uma empresa."""
    ticker: str
    nome_empresa: str
    cnpj: str
    eh_financeira: bool
    preco_atual: float | None
    data_cotacao: str | None
    lpa_atual: float | None
    vpa_atual: float | None
    roe_atual: float | None
    payout_historico_medio: float | None
    opcoes_lpa: OpcoesProjecaoLPA = field(default_factory=OpcoesProjecaoLPA)
    metodos: dict[str, PrecoTeto] = field(default_factory=dict)
    faixa: FaixaPrecoTeto | None = None
    metricas_ttm: MetricasTTM | None = None


def estimar_lpa_mercado(ticker: str) -> tuple[float | None, str]:
    """Tenta obter a estimativa de LPA dos analistas via mercado."""
    # 1. Tenta yfinance se disponível
    try:
        import yfinance as yf
        simbolo = f"{ticker.strip().upper()}.SA"
        t = yf.Ticker(simbolo)
        info = t.info
        forward_eps = info.get("forwardEps")
        if forward_eps and float(forward_eps) > 0:
            return float(forward_eps), "Yahoo Finance (Forward EPS)"
    except Exception:
        pass

    # 2. Informa de forma transparente se indisponível
    return None, "Projeção de analistas indisponível online no momento"


def calcular_opcoes_lpa(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    df_historico: pd.DataFrame,
    lpa_ttm: float | None = None,
    lpa_manual: float | None = None,
) -> OpcoesProjecaoLPA:
    """Monta as 3 opções de LPA projetado (Mercado, CVM/Histórico/TTM, Manual)."""
    opcoes = OpcoesProjecaoLPA()

    # Opção 1: Mercado / Analistas
    val_mercado, origem = estimar_lpa_mercado(ticker)
    opcoes.analistas_mercado = val_mercado
    opcoes.origem_mercado = origem

    # Opção 2: Histórico CVM (prioriza LPA TTM se disponível para projetar crescimento de 5%)
    base_lpa = lpa_ttm
    if not df_historico.empty and "lpa" in df_historico:
        lpas_validos = df_historico["lpa"].dropna().tolist()
        if lpas_validos:
            # Média dos últimos 3 anos
            ultimos_3 = lpas_validos[-3:]
            opcoes.historico_media_3a = sum(ultimos_3) / len(ultimos_3)
            if base_lpa is None:
                base_lpa = lpas_validos[-1]

    if base_lpa and base_lpa > 0:
        opcoes.historico_crescimento = base_lpa * 1.05

    # Opção 3: Manual do usuário
    opcoes.manual = lpa_manual

    return opcoes


def avaliar_ativo(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    yield_minimo_projetivo: float = 0.06,
    yield_bazin: float = 0.06,
    lpa_projetado_manual: float | None = None,
    payout_projetado_manual: float | None = None,
    taxa_desconto_gordon: float = 0.10,
) -> RelatorioValuation:
    """Executa valuation completo comparativo para o ticker."""
    cnpj, nome = identificar_empresa(con, ticker)
    eh_banco = eh_instituicao_financeira(con, cnpj)

    # Cotação atual
    preco_atual, dt_cot = obter_cotacao_mais_recente(con, ticker)
    if preco_atual is None:
        # Tenta buscar online se não houver no banco
        preco_atual = obter_cotacao_recente(ticker)
        dt_cot = "tempo real" if preco_atual else None

    # Histórico de indicadores e Métricas TTM
    df_hist = obter_historico_empresa(con, cnpj)
    metricas_ttm = obter_metricas_ttm(con, cnpj, cotacao=preco_atual)

    # Prioriza indicadores TTM (mais recentes) e recorre ao histórico anual DFP
    lpa_hist = df_hist["lpa"].dropna().iloc[-1] if not df_hist.empty and "lpa" in df_hist else None
    vpa_hist = df_hist["vpa"].dropna().iloc[-1] if not df_hist.empty and "vpa" in df_hist else None
    roe_hist = df_hist["roe"].dropna().iloc[-1] if not df_hist.empty and "roe" in df_hist else None

    lpa_atual = metricas_ttm.lpa_ttm or lpa_hist
    vpa_atual = metricas_ttm.vpa_recente or vpa_hist
    roe_atual = metricas_ttm.roe_ttm or roe_hist

    # Payout histórico médio dos últimos anos
    payout_medio = None
    if not df_hist.empty and "payout" in df_hist:
        payouts_validos = [p for p in df_hist["payout"].dropna().tolist() if 0.0 < p <= 1.2]
        if payouts_validos:
            payout_medio = sum(payouts_validos) / len(payouts_validos)

    payout_final = payout_projetado_manual or payout_medio or 0.50

    # Proventos por ação históricos
    provs = obter_proventos_ultimos_anos(con, ticker, anos=5)
    dpa_medio = calcular_dividendo_medio(provs)

    # 3 Opções de Projeção de LPA (prioriza TTM na projeção de crescimento)
    opcoes_lpa = calcular_opcoes_lpa(
        con, ticker, df_hist, lpa_ttm=metricas_ttm.lpa_ttm, lpa_manual=lpa_projetado_manual
    )

    # Seleciona o LPA para o método projetivo base:
    # Manual > Analistas de Mercado > Histórico Crescimento > Último LPA
    lpa_escolhido = (
        lpa_projetado_manual
        or opcoes_lpa.analistas_mercado
        or opcoes_lpa.historico_crescimento
        or lpa_atual
    )

    metodos: dict[str, PrecoTeto] = {}

    # 1. Método BAZIN (baseado na média de dividendos passados)
    metodos["Bazin"] = bazin(dpa_medio, yield_alvo=yield_bazin)

    # 2. Método PROJETIVO (LPA projetado x Payout projetado / Yield mínimo)
    metodos["Projetivo"] = projetivo(
        lpa_projetado=lpa_escolhido,
        payout_projetado=payout_final,
        yield_minimo=yield_minimo_projetivo,
        origem_projecao="manual" if lpa_projetado_manual else (
            "analistas" if opcoes_lpa.analistas_mercado else "histórico CVM"
        ),
    )

    # 3. Método GORDON (Modelo de Dividendos)
    if eh_banco and lpa_atual and roe_atual:
        # Para bancos: Gordon DDM com ROE e Payout
        metodos["Gordon"] = gordon_com_roe(
            lpa=lpa_atual,
            roe=roe_atual,
            payout=payout_final,
            taxa_desconto=taxa_desconto_gordon,
        )
    elif dpa_medio > 0:
        metodos["Gordon"] = gordon(
            dividendo_atual=dpa_medio,
            taxa_desconto=taxa_desconto_gordon,
            crescimento=0.04,
        )
    else:
        metodos["Gordon"] = PrecoTeto("Gordon", None, observacao="Sem histórico de dividendos.")

    # 4. Método GRAHAM (raiz(22.5 * LPA * VPA))
    if eh_banco:
        metodos["Graham"] = PrecoTeto(
            "Graham",
            None,
            observacao="Método tradicional de Graham não se aplica a instituições financeiras.",
        )
    elif lpa_atual and vpa_atual:
        metodos["Graham"] = graham(lpa_atual, vpa_atual)
    else:
        metodos["Graham"] = PrecoTeto("Graham", None, observacao="LPA ou VPA indisponível.")

    # 5. Método DCF (Fluxo de Caixa Descontado)
    if eh_banco:
        metodos["DCF"] = PrecoTeto(
            "DCF",
            None,
            observacao="DCF de firma não se aplica a bancos (estrutura de capital não separa dívida de funding).",
        )
    elif lpa_atual and lpa_atual > 0:
        # Projeção simplificada de FCF a partir do lucro e número de ações
        acoes_circ = df_hist["lpa"].iloc[-1] if not df_hist.empty else 1.0
        # Exemplo de fluxo projetado nos próximos 5 anos crescendo a 5%
        fluxos = [lpa_atual * ((1.05) ** i) for i in range(1, 6)]
        metodos["DCF"] = fluxo_caixa_descontado(
            fluxos_projetados=fluxos,
            taxa_desconto=0.10,
            crescimento_perpetuidade=0.03,
            divida_liquida=0.0,
            numero_acoes=1.0,  # Como fluxos já estão por ação
        )
    else:
        metodos["DCF"] = PrecoTeto("DCF", None, observacao="Sem lucro positivo recente para projetar fluxos.")

    # Faixa de Preço Teto consolidada
    faixa = calcular_faixa_teto(metodos, preco_atual=preco_atual)

    return RelatorioValuation(
        ticker=ticker.upper(),
        nome_empresa=nome,
        cnpj=cnpj,
        eh_financeira=eh_banco,
        preco_atual=preco_atual,
        data_cotacao=str(dt_cot) if dt_cot else None,
        lpa_atual=lpa_atual,
        vpa_atual=vpa_atual,
        roe_atual=roe_atual,
        payout_historico_medio=payout_medio,
        opcoes_lpa=opcoes_lpa,
        metodos=metodos,
        faixa=faixa,
        metricas_ttm=metricas_ttm,
    )


def formatar_tabela_valuation(relatorio: RelatorioValuation) -> str:
    """Gera tabela comparativa em formato texto dos métodos de valuation."""
    linhas = []
    preco = relatorio.preco_atual

    for nome_metodo, pt in relatorio.metodos.items():
        if pt.valor is not None:
            marg = margem_de_seguranca(preco, pt.valor) if preco else None
            status = "ABAIXO DO TETO (Desconto)" if (marg and marg > 0) else "ACIMA DO TETO"
            linhas.append({
                "Método": nome_metodo,
                "Preço Teto": f"R$ {pt.valor:.2f}",
                "Margem Segurança": f"{marg*100:+.1f}%" if marg is not None else "-",
                "Situação": status if preco else "Preço não informado",
                "Observação": pt.observacao or "Aplicado com sucesso",
            })
        else:
            linhas.append({
                "Método": nome_metodo,
                "Preço Teto": "N/A",
                "Margem Segurança": "-",
                "Situação": "Não Aplicável",
                "Observação": pt.observacao,
            })

    return pd.DataFrame(linhas).to_string(index=False)


def exibir_relatorio(relatorio: RelatorioValuation) -> None:
    """Imprime no terminal o relatório estruturado e visual de valuation."""
    t = relatorio.ticker
    tipo = "Instituição Financeira" if relatorio.eh_financeira else "Companhia Não-Financeira"
    preco_str = f"R$ {relatorio.preco_atual:.2f}" if relatorio.preco_atual else "Indisponível"

    print("\n" + "=" * 80)
    print(f"  VALUATION COMPLETO: {t} — {relatorio.nome_empresa} ({tipo})")
    print(f"  Cotação Atual: {preco_str}  |  Data: {relatorio.data_cotacao or '-'}")
    print("=" * 80)

    # Indicadores Base e TTM
    if relatorio.metricas_ttm and relatorio.metricas_ttm.lucro_ttm:
        m = relatorio.metricas_ttm
        pl_str = f"{m.pl_ttm:.1f}x" if m.pl_ttm else "-"
        pvp_str = f"{m.pvp_recente:.2f}x" if m.pvp_recente else "-"
        print(f"Indicadores TTM ({m.janela}): LPA TTM = R$ {m.lpa_ttm:.2f} | P/L TTM = {pl_str} | P/VP = {pvp_str} | ROE TTM = {m.roe_ttm*100:.1f}%")
    else:
        lpa_str = f"R$ {relatorio.lpa_atual:.2f}" if relatorio.lpa_atual else "-"
        vpa_str = f"R$ {relatorio.vpa_atual:.2f}" if relatorio.vpa_atual else "-"
        roe_str = f"{relatorio.roe_atual*100:.1f}%" if relatorio.roe_atual else "-"
        payout_str = f"{relatorio.payout_historico_medio*100:.1f}%" if relatorio.payout_historico_medio else "-"
        print(f"Indicadores Base: LPA = {lpa_str} | VPA = {vpa_str} | ROE = {roe_str} | Payout Médio = {payout_str}")

    # Opções de Projeção de LPA
    print("\nProjeções de LPA Disponíveis para o Método Projetivo:")
    op = relatorio.opcoes_lpa
    if op.analistas_mercado:
        print(f"  [1] Consenso de Mercado / Analistas: R$ {op.analistas_mercado:.2f} ({op.origem_mercado})")
    else:
        print(f"  [1] Consenso de Mercado / Analistas: {op.origem_mercado}")

    if op.historico_crescimento:
        print(f"  [2] Projeção Histórica CVM (Crescimento): R$ {op.historico_crescimento:.2f}")
    if op.historico_media_3a:
        print(f"  [2b] Média Histórica 3 Anos CVM: R$ {op.historico_media_3a:.2f}")

    if op.manual:
        print(f"  [3] Projeção Manual do Investidor: R$ {op.manual:.2f}")

    # Tabela de Métodos
    print("\n--- MÉTODOS DE PREÇO TETO LADO A LADO ---")
    print(formatar_tabela_valuation(relatorio))

    # Faixa de Preço Teto
    fx = relatorio.faixa
    if fx and fx.minimo is not None:
        print("\n--- FAIXA DE PREÇO TETO (CONSOLIDADA) ---")
        print(f"  * Teto Mínimo: R$ {fx.minimo:.2f}  (Margem: {fx.margem_minima*100:+.1f}%)" if fx.margem_minima else f"  * Teto Mínimo: R$ {fx.minimo:.2f}")
        print(f"  * Teto Médio:  R$ {fx.medio:.2f}   (Margem: {fx.margem_media*100:+.1f}%)" if fx.margem_media else f"  * Teto Médio:  R$ {fx.medio:.2f}")
        print(f"  * Teto Máximo: R$ {fx.maximo:.2f}  (Margem: {fx.margem_maxima*100:+.1f}%)" if fx.margem_maxima else f"  * Teto Máximo: R$ {fx.maximo:.2f}")
    print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Motor de Preço Teto Completo com Métodos Lado a Lado")
    p.add_argument("--ticker", default=None, help="Ticker para avaliação (ex.: WEGE3, PETR4)")
    p.add_argument("--todos", action="store_true", help="Avalia todos os tickers cadastrados no banco")
    p.add_argument("--yield-minimo", type=float, default=0.06, help="Yield mínimo do método projetivo (padrão: 0.06 = 6%%)")
    p.add_argument("--yield-bazin", type=float, default=0.06, help="Yield alvo do Bazin (padrão: 0.06 = 6%%)")
    p.add_argument("--lpa-projetado", type=float, default=None, help="LPA projetado manual para o método projetivo")
    p.add_argument("--payout-projetado", type=float, default=None, help="Payout projetado manual (ex.: 0.50 = 50%%)")
    p.add_argument("--db", default="data/acoes.duckdb", help="Caminho do banco DuckDB")
    args = p.parse_args(argv)

    caminho_db = Path(args.db)
    if not caminho_db.exists():
        print(f"Banco não encontrado em {caminho_db}. Rode a ingestão primeiro.")
        return 1

    con = conectar(caminho_db)

    if args.ticker:
        try:
            rel = avaliar_ativo(
                con,
                args.ticker,
                yield_minimo_projetivo=args.yield_minimo,
                yield_bazin=args.yield_bazin,
                lpa_projetado_manual=args.lpa_projetado,
                payout_projetado_manual=args.payout_projetado,
            )
            exibir_relatorio(rel)
            return 0
        except Exception as e:
            print(f"Erro ao avaliar {args.ticker}: {e}")
            return 1

    # Avaliar todos
    tickers_rows = con.execute("SELECT DISTINCT ticker FROM tickers ORDER BY 1").fetchall()
    if not tickers_rows:
        print("Nenhum ticker encontrado no banco.")
        return 0

    for (t,) in tickers_rows:
        try:
            rel = avaliar_ativo(con, t, yield_minimo_projetivo=args.yield_minimo, yield_bazin=args.yield_bazin)
            exibir_relatorio(rel)
        except Exception as e:
            print(f"Erro ao avaliar {t}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
