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
from src.indicadores import (
    eh_instituicao_financeira,
    identificar_empresa,
    obter_historico_empresa,
)
from src.market_data import (
    atualizar_tudo,
    calcular_dividendo_medio,
    obter_cotacao_mais_recente,
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

    # Histórico de Indicadores
    df_hist = obter_historico_empresa(con, cnpj)
    preco_atual, dt_cot = obter_cotacao_mais_recente(con, ticker_selecionado)

    # Linha de Métricas Chave
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    lpa_recente = df_hist["lpa"].dropna().iloc[-1] if not df_hist.empty and "lpa" in df_hist else None
    vpa_recente = df_hist["vpa"].dropna().iloc[-1] if not df_hist.empty and "vpa" in df_hist else None
    roe_recente = df_hist["roe"].dropna().iloc[-1] if not df_hist.empty and "roe" in df_hist else None
    dl_ebitda = df_hist["divida_liquida_ebitda"].dropna().iloc[-1] if not df_hist.empty and "divida_liquida_ebitda" in df_hist else None

    provs = obter_proventos_ultimos_anos(con, ticker_selecionado, anos=5)
    dpa_medio = calcular_dividendo_medio(provs)
    dy_medio = (dpa_medio / preco_atual) if (preco_atual and dpa_medio > 0) else None

    col1.metric("Cotação Atual", f"R$ {preco_atual:.2f}" if preco_atual else "N/A", help="Último preço registrado")
    col2.metric("P/L", f"{preco_atual / lpa_recente:.1f}x" if (preco_atual and lpa_recente and lpa_recente > 0) else "N/A")
    col3.metric("P/VP", f"{preco_atual / vpa_recente:.2f}x" if (preco_atual and vpa_recente and vpa_recente > 0) else "N/A")
    col4.metric("ROE Atual", f"{roe_recente * 100:.1f}%" if roe_recente else "N/A")
    if not eh_banco:
        col5.metric("Dív. Líq / EBITDA", f"{dl_ebitda:.2f}x" if dl_ebitda is not None else "N/A")
    else:
        col5.metric("Margem Líquida", f"{df_hist['margem_liquida'].dropna().iloc[-1] * 100:.1f}%" if not df_hist.empty and "margem_liquida" in df_hist else "N/A")
    col6.metric("DY Médio (5a)", f"{dy_medio * 100:.1f}%" if dy_medio else "N/A")

    st.markdown("---")

    # ----------------------------------------------------------------------- #
    # Simulador do Método Projetivo
    # ----------------------------------------------------------------------- #
    st.subheader("🎯 Simulador de Preço Teto Projetivo")
    st.markdown("Configure as expectativas de **LPA futuro**, **Payout** e **Yield mínimo desejado**:")

    # Opções automáticas de projeção
    payout_hist_medio = 0.50
    if not df_hist.empty and "payout" in df_hist:
        payouts_validos = [p for p in df_hist["payout"].dropna().tolist() if 0.0 < p <= 1.2]
        if payouts_validos:
            payout_hist_medio = sum(payouts_validos) / len(payouts_validos)

    lpa_proj_sugerido = (lpa_recente * 1.05) if (lpa_recente and lpa_recente > 0) else 1.0

    sim_col1, sim_col2, sim_col3 = st.columns(3)
    with sim_col1:
        fonte_lpa = st.radio(
            "Origem da Projeção de LPA:",
            ["Histórico CVM (+5% a.a.)", "Manual / Personalizado"],
            help="Escolha entre a projeção automática baseada nos balanços da CVM ou insira sua própria projeção.",
        )
        if fonte_lpa == "Manual / Personalizado":
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

    # ----------------------------------------------------------------------- #
    # Painel de Métodos Lado a Lado
    # ----------------------------------------------------------------------- #
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
            # Barras dos tetos
            fig.add_trace(go.Bar(
                x=valores_plot,
                y=nomes_plot,
                orientation="h",
                marker_color=cores_plot,
                text=[f"R$ {v:.2f}" for v in valores_plot],
                textposition="auto",
                name="Preço Teto",
            ))
            # Linha vertical da cotação atual
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

    # Faixa consolidada
    fx = relatorio.faixa
    if fx and fx.minimo is not None:
        st.info(
            f"**Faixa Consolidada de Preço Teto:** "
            f"Mínimo: **R$ {fx.minimo:.2f}** ({fx.margem_minima*100:+.1f}%)  |  "
            f"Médio: **R$ {fx.medio:.2f}** ({fx.margem_media*100:+.1f}%)  |  "
            f"Máximo: **R$ {fx.maximo:.2f}** ({fx.margem_maxima*100:+.1f}%)"
        )

    st.markdown("---")

    # ----------------------------------------------------------------------- #
    # Histórico de Indicadores e Dividendos (Plotly)
    # ----------------------------------------------------------------------- #
    st.subheader("📈 Histórico de Resultados & Proventos (CVM)")
    if not df_hist.empty:
        col_hist1, col_hist2 = st.columns(2)

        with col_hist1:
            # Gráfico de LPA e VPA
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
            # Gráfico de Rentabilidade (ROE / ROIC)
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

        # Tabela histórica completa
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

            dados_screener.append({
                "Ticker": t,
                "Empresa": rel.nome_empresa,
                "Tipo": "Banco/Financ." if rel.eh_financeira else "Não-Financ.",
                "Cotação": preco,
                "Teto Médio": teto_med,
                "Margem Médio (%)": marg_med * 100 if marg_med is not None else None,
                "Situação": status,
                "DY Médio (%)": dy_med * 100 if dy_med is not None else None,
                "P/L": preco / rel.lpa_atual if (preco and rel.lpa_atual and rel.lpa_atual > 0) else None,
                "P/VP": preco / rel.vpa_atual if (preco and rel.vpa_atual and rel.vpa_atual > 0) else None,
                "ROE (%)": rel.roe_atual * 100 if rel.roe_atual else None,
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
        df_formatado["P/L"] = df_formatado["P/L"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "-")
        df_formatado["P/VP"] = df_formatado["P/VP"].apply(lambda v: f"{v:.2f}x" if pd.notna(v) else "-")
        df_formatado["ROE (%)"] = df_formatado["ROE (%)"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "-")

        st.dataframe(df_formatado, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- #
# TELA 3: ATUALIZAÇÃO DE DADOS
# --------------------------------------------------------------------------- #
elif modo == "🔄 Atualização de Dados":
    st.title("🔄 Atualização dos Dados")
    st.write("Sincronize cotações e proventos recentes ou execute varredura:")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🚀 Atualizar Cotações & Proventos Agora", type="primary"):
            with st.spinner("Atualizando dados de mercado..."):
                atualizar_tudo(con)
                st.success("Cotações e proventos atualizados com sucesso no DuckDB!")
                st.rerun()

    with col_btn2:
        st.caption("Os dados brutos e balanços são salvos em `data/acoes.duckdb`.")
