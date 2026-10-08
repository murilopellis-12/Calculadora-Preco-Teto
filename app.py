"""
Interface Interativa do Analisador de Ações (Streamlit + Plotly).

Recursos:
  1. Tela do Ativo: múltiplos, indicadores fundamentalistas da CVM,
     histórico anual com gráficos Plotly, simulador interativo de LPA/Payout
     e tabela lado a lado de preço teto com margens de segurança.
  2. Screener: radar comparativo de todos os ativos da carteira, com
     filtro de ações com preço abaixo do teto e ranking de desconto.
  3. Atualização de Dados: sincronização manual de cotações e proventos.

Execução:
    streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analise import avaliar_ativo
from src.db import conectar
from src.ingest_cvm import ingerir_cvm
from src.indicadores import (
    eh_instituicao_financeira,
    identificar_empresa,
    obter_historico_empresa,
    obter_metricas_ttm,
)
from src.market_data import (
    atualizar_tudo,
    calcular_dividendo_medio,
    calcular_estatisticas_bazin,
    obter_cotacao_mais_recente,
    obter_proventos_detalhados_bazin,
    obter_proventos_ultimos_anos,
)

# --------------------------------------------------------------------------- #
# Configuração da Página
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="Stock Analyzer — B3",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path("data/acoes.duckdb")


@st.cache_resource
def get_db_connection():
    return conectar(DB_PATH)


def listar_tickers(con: duckdb.DuckDBPyConnection) -> list[str]:
    res = con.execute("SELECT DISTINCT ticker FROM tickers ORDER BY 1").fetchall()
    return [r[0] for r in res] if res else []


# --------------------------------------------------------------------------- #
# Barra Lateral (Sidebar)
# --------------------------------------------------------------------------- #
con = get_db_connection()
tickers_disponiveis = listar_tickers(con)

st.sidebar.title("📊 Stock Analyzer")
st.sidebar.caption("Análise Fundamentalista & Preço Teto B3")

modo = st.sidebar.radio(
    "Navegação:",
    ["📌 Análise do Ativo", "🔍 Screener (Radar de Preço Teto)", "🔄 Atualização de Dados"],
)

st.sidebar.markdown("---")
yield_padrao = st.sidebar.slider(
    "Yield Mínimo Padrão (% a.a.):",
    min_value=4.0,
    max_value=12.0,
    value=6.0,
    step=0.5,
    help="Taxa de dividend yield esperada para os métodos Bazin e Projetivo.",
) / 100.0


# --------------------------------------------------------------------------- #
# TELA 1: ANÁLISE DO ATIVO
# --------------------------------------------------------------------------- #
if modo == "📌 Análise do Ativo":
    if not tickers_disponiveis:
        st.warning("Nenhum ticker encontrado no banco de dados. Rode a ingestão da CVM primeiro.")
        st.stop()

    ticker_selecionado = st.sidebar.selectbox("Selecione o Ticker:", tickers_disponiveis)

    # Avaliação do ativo com premissas base
    try:
        cnpj, nome = identificar_empresa(con, ticker_selecionado)
        eh_banco = eh_instituicao_financeira(con, cnpj)
    except Exception as e:
        st.error(f"Erro ao identificar empresa {ticker_selecionado}: {e}")
        st.stop()

    st.title(f"{ticker_selecionado} — {nome}")
    tipo_badge = "🏦 Instituição Financeira" if eh_banco else "🏭 Companhia Não-Financeira"
    st.caption(f"**Segmento:** {tipo_badge} | **CNPJ:** `{cnpj}`")

    # Histórico de Indicadores e Métricas TTM
    df_hist = obter_historico_empresa(con, cnpj)
    preco_atual, dt_cot = obter_cotacao_mais_recente(con, ticker_selecionado)
    metricas_ttm = obter_metricas_ttm(con, cnpj, cotacao=preco_atual)

    def ultimo_valor_valido(df: pd.DataFrame, col: str) -> float | None:
        if df.empty or col not in df.columns:
            return None
        serie = df[col].dropna()
        return float(serie.iloc[-1]) if not serie.empty else None

    # Linha de Métricas Chave (TTM + Balanços)
    lpa_anual = ultimo_valor_valido(df_hist, "lpa")
    vpa_anual = ultimo_valor_valido(df_hist, "vpa")
    roe_anual = ultimo_valor_valido(df_hist, "roe")
    dl_ebitda = ultimo_valor_valido(df_hist, "divida_liquida_ebitda")
    marg_liq_recente = ultimo_valor_valido(df_hist, "margem_liquida")

    lpa_display = metricas_ttm.lpa_ttm or lpa_anual
    vpa_display = metricas_ttm.vpa_recente or vpa_anual
    roe_display = metricas_ttm.roe_ttm or roe_anual
    pl_display = metricas_ttm.pl_ttm or (preco_atual / lpa_display if (preco_atual and lpa_display and lpa_display > 0) else None)
    pvp_display = metricas_ttm.pvp_recente or (preco_atual / vpa_display if (preco_atual and vpa_display and vpa_display > 0) else None)

    provs = obter_proventos_ultimos_anos(con, ticker_selecionado, anos=5)
    dpa_medio = calcular_dividendo_medio(provs)
    dy_medio = (dpa_medio / preco_atual) if (preco_atual and dpa_medio > 0) else None

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    col1.metric("Cotação Atual", f"R$ {preco_atual:.2f}" if preco_atual else "N/A", help="Último preço registrado")

    help_pl = "P/L TTM (Preço / Lucro dos últimos 12 meses)"
    if metricas_ttm and metricas_ttm.janela:
        help_pl += f" com base nos trimestres {metricas_ttm.janela}."
    if lpa_anual and preco_atual:
        help_pl += f" | P/L Anual Fechado (DFP): {preco_atual / lpa_anual:.1f}x."

    col2.metric("P/L (TTM)", f"{pl_display:.1f}x" if pl_display else "N/A", help=help_pl)
    col3.metric("P/VP", f"{pvp_display:.2f}x" if pvp_display else "N/A", help=f"VPA Recente: R$ {vpa_display:.2f}" if vpa_display else None)
    col4.metric("ROE (TTM)", f"{roe_display * 100:.1f}%" if roe_display else "N/A", help=f"Rentabilidade sobre o Patrimônio nos últimos 12 meses ({metricas_ttm.janela})" if metricas_ttm.janela else None)
    if not eh_banco:
        col5.metric("Dív. Líq / EBITDA", f"{dl_ebitda:.2f}x" if dl_ebitda is not None else "N/A")
    else:
        col5.metric("Margem Líquida", f"{marg_liq_recente * 100:.1f}%" if marg_liq_recente is not None else "N/A")
    col6.metric("DY Médio (5a)", f"{dy_medio * 100:.1f}%" if dy_medio else "N/A")

    if metricas_ttm and metricas_ttm.lucro_ttm:
        st.info(
            f"💡 **Indicadores TTM (Trailing Twelve Months — Últimos 12 Meses):** "
            f"Calculados com a soma dos últimos 4 trimestres contábeis da CVM (**{metricas_ttm.janela}**). "
            f"Lucro Líq. TTM: **R$ {metricas_ttm.lucro_ttm/1e9:.2f} bi** (R$ {metricas_ttm.lucro_ttm/1e6:,.0f} mi) | "
            f"LPA TTM: **R$ {metricas_ttm.lpa_ttm:.2f}** | "
            f"VPA Recente: **R$ {metricas_ttm.vpa_recente:.2f}** | "
            f"P/L TTM: **{pl_display:.1f}x**."
        )

    st.markdown("---")

    # ----------------------------------------------------------------------- #
    # Abas de Navegação do Ativo
    # ----------------------------------------------------------------------- #
    tab_teto, tab_bazin, tab_historico = st.tabs([
        "🎯 Valuation & Preço Teto",
        "💰 Detalhamento de Dividendos (Bazin)",
        "📈 Histórico & Demonstrações",
    ])

    # ----------------------------------------------------------------------- #
    # ABA 1: VALUATION & PREÇO TETO
    # ----------------------------------------------------------------------- #
    with tab_teto:
        st.subheader("🎯 Simulador de Preço Teto Projetivo")
        st.markdown("Configure as expectativas de **LPA futuro**, **Payout** e **Yield mínimo desejado**:")

        # Opções automáticas de projeção
        payout_hist_medio = 0.50
        if not df_hist.empty and "payout" in df_hist:
            payouts_validos = [p for p in df_hist["payout"].dropna().tolist() if 0.0 < p <= 1.2]
            if payouts_validos:
                payout_hist_medio = sum(payouts_validos) / len(payouts_validos)

        lpa_base = metricas_ttm.lpa_ttm or lpa_anual or 1.0
        lpa_proj_sugerido = (lpa_base * 1.05) if lpa_base > 0 else 1.0

        sim_col1, sim_col2, sim_col3 = st.columns(3)
        with sim_col1:
            fonte_lpa = st.radio(
                "Origem da Projeção de LPA:",
                [f"Histórico CVM TTM (+5% a.a.) [Base: R$ {lpa_base:.2f}]", "Manual / Personalizado"],
                help="Escolha entre a projeção automática baseada nos últimos 12 meses da CVM ou insira sua própria projeção.",
            )
            if "Manual" in fonte_lpa:
                lpa_input = st.number_input("LPA Projetado (R$/ação):", min_value=0.01, value=float(lpa_proj_sugerido), step=0.10)
            else:
                lpa_input = float(lpa_proj_sugerido)
                st.info(f"LPA Projetado CVM: **R$ {lpa_input:.2f}**")

        with sim_col2:
            payout_input = st.slider(
                "Payout Esperado (% do Lucro):",
                min_value=10.0,
                max_value=100.0,
                value=float(round(payout_hist_medio * 100, 0)),
                step=5.0,
                help="Parcela do lucro distribuída em dividendos/JCP. Pré-preenchido com a média histórica.",
            ) / 100.0
            dividendo_esperado = lpa_input * payout_input
            st.caption(f"Dividendo Anual Esperado: **R$ {dividendo_esperado:.2f} por ação**")

        with sim_col3:
            yield_proj_input = st.slider(
                "Yield Mínimo Desejado (%):",
                min_value=4.0,
                max_value=12.0,
                value=float(round(yield_padrao * 100, 1)),
                step=0.5,
            ) / 100.0
            teto_projetivo_calculado = dividendo_esperado / yield_proj_input
            st.success(f"Preço Teto Projetivo: **R$ {teto_projetivo_calculado:.2f}**")

        st.markdown("---")

        # Painel de Métodos Lado a Lado
        st.subheader("⚖️ Métodos de Preço Teto Lado a Lado")

        relatorio = avaliar_ativo(
            con,
            ticker_selecionado,
            yield_minimo_projetivo=yield_proj_input,
            yield_bazin=yield_padrao,
            lpa_projetado_manual=lpa_input,
            payout_projetado_manual=payout_input,
        )

        tabela_metodos = []
        nomes_plot = []
        valores_plot = []
        cores_plot = []

        for nome_m, pt in relatorio.metodos.items():
            if pt.valor is not None:
                marg = 1.0 - (preco_atual / pt.valor) if preco_atual else None
                status = "🟢 Abaixo do Teto (Desconto)" if (marg and marg > 0) else "🔴 Acima do Teto (Ágio)"
                tabela_metodos.append({
                    "Método": nome_m,
                    "Preço Teto": f"R$ {pt.valor:.2f}",
                    "Margem de Segurança": f"{marg*100:+.1f}%" if marg is not None else "-",
                    "Situação": status if preco_atual else "Sem cotação",
                    "Observação": pt.observacao or "Aplicável",
                })
                nomes_plot.append(nome_m)
                valores_plot.append(pt.valor)
                cores_plot.append("#10B981" if (preco_atual and pt.valor >= preco_atual) else "#EF4444")
            else:
                tabela_metodos.append({
                    "Método": nome_m,
                    "Preço Teto": "N/A",
                    "Margem de Segurança": "-",
                    "Situação": "Não Aplicável",
                    "Observação": pt.observacao,
                })

        col_tab, col_chart = st.columns([1.2, 1.0])
        with col_tab:
            st.dataframe(pd.DataFrame(tabela_metodos), use_container_width=True, hide_index=True)

        with col_chart:
            if valores_plot and preco_atual:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=valores_plot,
                    y=nomes_plot,
                    orientation="h",
                    marker_color=cores_plot,
                    text=[f"R$ {v:.2f}" for v in valores_plot],
                    textposition="auto",
                    name="Preço Teto",
                ))
                fig.add_vline(
                    x=preco_atual,
                    line_width=3,
                    line_dash="dash",
                    line_color="#3B82F6",
                    annotation_text=f"Cotação: R$ {preco_atual:.2f}",
                    annotation_position="top right",
                )
                fig.update_layout(
                    title=f"Comparativo vs. Cotação Atual (R$ {preco_atual:.2f})",
                    xaxis_title="R$ por ação",
                    yaxis_title="",
                    margin=dict(l=20, r=20, t=40, b=20),
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)

        fx = relatorio.faixa
        if fx and fx.minimo is not None:
            st.info(
                f"**Faixa Consolidada de Preço Teto:** "
                f"Mínimo: **R$ {fx.minimo:.2f}** ({fx.margem_minima*100:+.1f}%)  |  "
                f"Médio: **R$ {fx.medio:.2f}** ({fx.margem_media*100:+.1f}%)  |  "
                f"Máximo: **R$ {fx.maximo:.2f}** ({fx.margem_maxima*100:+.1f}%)"
            )

    # ----------------------------------------------------------------------- #
    # ABA 2: DETALHAMENTO DE DIVIDENDOS (BAZIN)
    # ----------------------------------------------------------------------- #
    with tab_bazin:
        st.subheader(f"💰 Histórico de Proventos & Memória de Cálculo de Bazin ({ticker_selecionado})")
        st.markdown(
            f"""
            O **Método de Décio Bazin** preconiza encontrar o preço teto dividindo o 
            **dividendo médio anual por ação** pela taxa mínima de retorno esperada (**Dividend Yield alvo**, padrão 6% a.a.):
            $$\\text{{Preço Teto (Bazin)}} = \\frac{{\\text{{Dividendo Médio Anual por Ação}}}}{{\\text{{Yield Alvo (ex: 6\\%)}}}}$$
            Abaixo estão os valores anuais auditados extraídos dos documentos enviados à **CVM** (Demonstração do Valor Adicionado — DVA e Fluxo de Caixa — DFC):
            """
        )

        df_bazin_detalhes = obter_proventos_detalhados_bazin(con, ticker_selecionado, anos=5)
        estat_bazin = calcular_estatisticas_bazin(df_bazin_detalhes, yield_alvo=yield_padrao, preco_atual=preco_atual)

        b_col1, b_col2, b_col3, b_col4 = st.columns(4)
        b_col1.metric("Dividendo Médio (DPA)", f"R$ {estat_bazin['dpa_medio']:.2f}", help=f"Média aritmética dos últimos {estat_bazin['anos_considerados']} anos")
        b_col2.metric("Yield Alvo", f"{estat_bazin['yield_alvo']*100:.1f}%", help="Taxa mínima exigida pelo investidor")
        b_col3.metric("Preço Teto de Bazin", f"R$ {estat_bazin['preco_teto']:.2f}", help="Valor máximo sugerido para compra")
        marg_b = estat_bazin['margem_seguranca']
        status_b = f"{marg_b*100:+.1f}% (Desconto)" if (marg_b and marg_b > 0) else (f"{marg_b*100:+.1f}% (Ágio)" if marg_b else "-")
        b_col4.metric("Margem de Segurança", status_b if preco_atual else "N/A", delta=f"{marg_b*100:+.1f}%" if marg_b else None)

        if estat_bazin['anos_considerados'] > 0:
            passo_soma = " + ".join([f"R$ {v:.2f}" for v in df_bazin_detalhes['dpa']])
            st.info(
                f"**Memória de Cálculo Detalhada:**\n"
                f"1. **Soma dos Dividendos Anuais:** ({passo_soma}) = **R$ {estat_bazin['soma_dpa']:.2f}**\n"
                f"2. **Dividendo Médio por Ação:** R$ {estat_bazin['soma_dpa']:.2f} ÷ {estat_bazin['anos_considerados']} anos = **R$ {estat_bazin['dpa_medio']:.2f}**\n"
                f"3. **Preço Teto:** R$ {estat_bazin['dpa_medio']:.2f} ÷ {estat_bazin['yield_alvo']:.2f} = **R$ {estat_bazin['preco_teto']:.2f}**"
            )

        if not df_bazin_detalhes.empty:
            df_exibir_bazin = df_bazin_detalhes.copy()
            df_exibir_bazin["Total Distribuído"] = df_exibir_bazin["proventos_totais"].apply(
                lambda v: f"R$ {v/1e9:.2f} bi" if v >= 1e9 else f"R$ {v/1e6:.1f} mi"
            )
            df_exibir_bazin["Ações em Circulação"] = df_exibir_bazin["acoes_circulacao"].apply(
                lambda v: f"{v/1e9:.2f} bilhões" if v >= 1e9 else f"{v/1e6:.1f} milhões"
            )
            df_exibir_bazin["Dividendo por Ação (DPA)"] = df_exibir_bazin["dpa"].apply(lambda v: f"R$ {v:.2f}")
            df_exibir_bazin["Yield s/ Cotação Atual"] = df_exibir_bazin["dpa"].apply(
                lambda v: f"{(v/preco_atual)*100:.1f}%" if (preco_atual and preco_atual > 0) else "-"
            )
            df_exibir_bazin = df_exibir_bazin[["ano", "Total Distribuído", "Ações em Circulação", "Dividendo por Ação (DPA)", "Yield s/ Cotação Atual", "fonte"]]
            df_exibir_bazin.columns = ["Ano", "Total Distribuído (Companhia)", "Ações em Circulação (CVM)", "DPA (R$/ação)", "Yield s/ Preço Atual", "Origem dos Dados"]

            st.markdown("#### 📄 Tabela de Dividendos Auditados Utilizados no Cálculo")
            st.dataframe(df_exibir_bazin, use_container_width=True, hide_index=True)

            fig_bazin = go.Figure()
            fig_bazin.add_trace(go.Bar(
                x=df_bazin_detalhes["ano"],
                y=df_bazin_detalhes["dpa"],
                name="DPA Pago no Ano",
                text=[f"R$ {v:.2f}" for v in df_bazin_detalhes["dpa"]],
                textposition="auto",
                marker_color="#10B981",
            ))
            fig_bazin.add_hline(
                y=estat_bazin["dpa_medio"],
                line_width=3,
                line_dash="dash",
                line_color="#F59E0B",
                annotation_text=f"Média Bazin: R$ {estat_bazin['dpa_medio']:.2f}",
                annotation_position="top left",
            )
            fig_bazin.update_layout(
                title=f"Histórico de Dividendos por Ação (DPA) vs. Média de Bazin ({ticker_selecionado})",
                xaxis_title="Ano",
                yaxis_title="R$ por ação",
                height=340,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig_bazin, use_container_width=True)
        else:
            st.warning("Sem histórico de proventos encontrado para este ticker.")

    # ----------------------------------------------------------------------- #
    # ABA 3: HISTÓRICO & DEMONSTRAÇÕES
    # ----------------------------------------------------------------------- #
    with tab_historico:
        st.subheader("📈 Histórico de Resultados & Composição TTM (CVM)")

        if metricas_ttm and metricas_ttm.detalhes_trimestres:
            st.markdown(f"#### 🗓️ Composição dos 4 Últimos Trimestres (Cálculo do TTM — {metricas_ttm.janela})")
            st.caption(
                "Metodologia equivalente a plataformas como Investidor10 e Status Invest: "
                "o Lucro Líquido e o LPA dos últimos 12 meses somam os 4 trimestres contábeis mais recentes entregues à CVM."
            )

            df_t = pd.DataFrame(metricas_ttm.detalhes_trimestres)
            df_t_exibir = df_t.copy()
            df_t_exibir["Lucro do Trimestre"] = df_t_exibir["vl_trimestre"].apply(
                lambda v: f"R$ {v/1e9:.3f} bi" if abs(v) >= 1e9 else f"R$ {v/1e6:.1f} mi"
            )
            df_t_exibir = df_t_exibir[["trimestre_label", "dt_fim_exerc", "Lucro do Trimestre"]]
            df_t_exibir.columns = ["Trimestre", "Data de Fechamento", "Lucro Líquido"]

            col_t1, col_t2 = st.columns([1.2, 1.0])
            with col_t1:
                st.dataframe(df_t_exibir, use_container_width=True, hide_index=True)
                st.markdown(
                    f"**Total Lucro TTM:** R$ {metricas_ttm.lucro_ttm/1e9:.2f} bi &nbsp;|&nbsp; "
                    f"**Ações:** {metricas_ttm.acoes_circulacao/1e9:.2f} bi &nbsp;|&nbsp; "
                    f"**LPA TTM:** **R$ {metricas_ttm.lpa_ttm:.2f}**"
                )
            with col_t2:
                fig_t = px.bar(
                    df_t,
                    x="trimestre_label",
                    y="vl_trimestre",
                    text=[f"R$ {v/1e9:.2f} bi" if abs(v)>=1e9 else f"R$ {v/1e6:.0f} mi" for v in df_t["vl_trimestre"]],
                    labels={"trimestre_label": "Trimestre", "vl_trimestre": "Lucro (R$)"},
                    title="Lucro por Trimestre Isolado",
                    color_discrete_sequence=["#3B82F6"],
                )
                fig_t.update_layout(height=260, margin=dict(l=20, r=20, t=30, b=20))
                st.plotly_chart(fig_t, use_container_width=True)

            st.markdown("---")

        if not df_hist.empty:
            st.markdown("#### 📊 Evolução Anual Consolidada (DFP)")
            col_hist1, col_hist2 = st.columns(2)

            with col_hist1:
                fig_lucro = go.Figure()
                if "lpa" in df_hist:
                    fig_lucro.add_trace(go.Bar(x=df_hist["ano"], y=df_hist["lpa"], name="LPA (R$)", marker_color="#3B82F6"))
                if "vpa" in df_hist:
                    fig_lucro.add_trace(go.Scatter(x=df_hist["ano"], y=df_hist["vpa"], name="VPA (R$)", line=dict(color="#10B981", width=3)))
                fig_lucro.update_layout(
                    title="Evolução de LPA e VPA por Ação",
                    xaxis_title="Ano",
                    yaxis_title="R$/ação",
                    barmode="group",
                    height=320,
                )
                st.plotly_chart(fig_lucro, use_container_width=True)

            with col_hist2:
                fig_rent = go.Figure()
                if "roe" in df_hist:
                    fig_rent.add_trace(go.Scatter(x=df_hist["ano"], y=df_hist["roe"] * 100, name="ROE (%)", mode="lines+markers", line=dict(color="#F59E0B", width=3)))
                if "roic" in df_hist and not eh_banco:
                    fig_rent.add_trace(go.Scatter(x=df_hist["ano"], y=df_hist["roic"] * 100, name="ROIC (%)", mode="lines+markers", line=dict(color="#8B5CF6", width=3)))
                fig_rent.update_layout(
                    title="Evolução de Rentabilidade (% a.a.)",
                    xaxis_title="Ano",
                    yaxis_title="%",
                    height=320,
                )
                st.plotly_chart(fig_rent, use_container_width=True)

            with st.expander("Ver Tabela Completa de Indicadores Anuais"):
                st.dataframe(df_hist, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- #
# TELA 2: SCREENER (RADAR DE OPORTUNIDADES)
# --------------------------------------------------------------------------- #
elif modo == "🔍 Screener (Radar de Preço Teto)":
    st.title("🔍 Screener — Radar de Preço Teto da Carteira")
    st.caption("Comparativo consolidado de todos os ativos acompanhados em tickers.txt")

    if not tickers_disponiveis:
        st.warning("Nenhum ticker cadastrado. Rode a ingestão da CVM.")
        st.stop()

    filtro_desconto = st.checkbox("Mostrar apenas ações ABAIXO DO TETO MÉDIO (com desconto)", value=False)

    dados_screener = []
    for t in tickers_disponiveis:
        try:
            rel = avaliar_ativo(con, t, yield_minimo_projetivo=yield_padrao, yield_bazin=yield_padrao)
            preco = rel.preco_atual
            fx = rel.faixa
            teto_med = fx.medio if fx else None
            marg_med = fx.margem_media if fx else None

            provs = obter_proventos_ultimos_anos(con, t, anos=5)
            dpa_med = calcular_dividendo_medio(provs)
            dy_med = (dpa_med / preco) if (preco and dpa_med > 0) else None

            status = "🟢 Desconto" if (marg_med and marg_med > 0) else "🔴 Ágio"

            if filtro_desconto and (not marg_med or marg_med <= 0):
                continue

            m_ttm = rel.metricas_ttm
            pl_ttm_val = m_ttm.pl_ttm if (m_ttm and m_ttm.pl_ttm) else (preco / rel.lpa_atual if (preco and rel.lpa_atual and rel.lpa_atual > 0) else None)
            pvp_val = m_ttm.pvp_recente if (m_ttm and m_ttm.pvp_recente) else (preco / rel.vpa_atual if (preco and rel.vpa_atual and rel.vpa_atual > 0) else None)
            roe_val = (m_ttm.roe_ttm if (m_ttm and m_ttm.roe_ttm) else rel.roe_atual)

            dados_screener.append({
                "Ticker": t,
                "Empresa": rel.nome_empresa,
                "Tipo": "Banco/Financ." if rel.eh_financeira else "Não-Financ.",
                "Cotação": preco,
                "Teto Médio": teto_med,
                "Margem Médio (%)": marg_med * 100 if marg_med is not None else None,
                "Situação": status,
                "DY Médio (%)": dy_med * 100 if dy_med is not None else None,
                "P/L (TTM)": pl_ttm_val,
                "P/VP": pvp_val,
                "ROE (TTM) (%)": roe_val * 100 if roe_val else None,
            })
        except Exception as e:
            continue

    if not dados_screener:
        st.info("Nenhuma ação atende aos filtros selecionados.")
    else:
        df_screener = pd.DataFrame(dados_screener).sort_values("Margem Médio (%)", ascending=False)

        # Formatação para exibição
        df_formatado = df_screener.copy()
        df_formatado["Cotação"] = df_formatado["Cotação"].apply(lambda v: f"R$ {v:.2f}" if pd.notna(v) else "-")
        df_formatado["Teto Médio"] = df_formatado["Teto Médio"].apply(lambda v: f"R$ {v:.2f}" if pd.notna(v) else "-")
        df_formatado["Margem Médio (%)"] = df_formatado["Margem Médio (%)"].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "-")
        df_formatado["DY Médio (%)"] = df_formatado["DY Médio (%)"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "-")
        df_formatado["P/L (TTM)"] = df_formatado["P/L (TTM)"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "-")
        df_formatado["P/VP"] = df_formatado["P/VP"].apply(lambda v: f"{v:.2f}x" if pd.notna(v) else "-")
        df_formatado["ROE (TTM) (%)"] = df_formatado["ROE (TTM) (%)"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "-")

        st.dataframe(df_formatado, use_container_width=True, hide_index=True)


elif modo == "🔄 Atualização de Dados":
    st.title("🔄 Atualização & Ingestão de Ativos")
    st.write("Gerencie os ativos acompanhados e sincronize balanços da CVM e cotações de mercado:")

    st.subheader("➕ Adicionar Novo Ticker à Carteira")
    st.caption("Adicione um novo ativo para buscar automaticamente os balanços na CVM e as cotações de mercado.")
    with st.form("form_add_ticker", clear_on_submit=True):
        col_t1, col_t2 = st.columns([2, 1])
        with col_t1:
            novo_t = st.text_input("Código do Ticker (ex.: BBAS3, TAEE11, SAPR11):", placeholder="Ex: BBAS3")
        with col_t2:
            st.write("")
            st.write("")
            btn_add = st.form_submit_button("📥 Ingerir da CVM & Mercado", type="primary")

        if btn_add:
            t_limpo = novo_t.strip().upper()
            if not t_limpo:
                st.warning("Por favor, digite um código de ticker válido.")
            else:
                caminho_tickers = Path("tickers.txt")
                linhas = caminho_tickers.read_text(encoding="utf-8").splitlines() if caminho_tickers.exists() else []
                tickers_existentes = {l.strip().upper() for l in linhas if l.strip() and not l.startswith("#")}
                if t_limpo not in tickers_existentes:
                    with open(caminho_tickers, "a", encoding="utf-8") as f:
                        f.write(f"\n{t_limpo}\n")

                with st.spinner(f"Processando demonstrativos da CVM e cotações para {t_limpo}..."):
                    ingerir_cvm(con, anos=None)
                    atualizar_tudo(con)
                st.success(f"Ativo **{t_limpo}** adicionado e processado com sucesso!")
                st.rerun()

    st.markdown("---")
    st.subheader("🚀 Sincronização Geral")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🔄 Atualizar Cotações & Proventos de Todos os Ativos"):
            with st.spinner("Atualizando dados de mercado..."):
                atualizar_tudo(con)
                st.success("Cotações e proventos atualizados com sucesso no DuckDB!")
                st.rerun()

    with col_btn2:
        if st.button("📑 Re-processar Balanços CVM de tickers.txt"):
            with st.spinner("Re-processando balanços locais da CVM..."):
                ingerir_cvm(con, anos=None, atualizar=True)
                atualizar_tudo(con)
                st.success("Balanços CVM reprocessados com sucesso!")
                st.rerun()
