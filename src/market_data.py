"""
Camada de dados de mercado (cotações e proventos).

Coleta cotações e proventos usando yfinance como provedor principal,
com fallback para Google Finance e Brapi (plano B do AGENTS.md), garantindo
resiliência em redes corporativas com bloqueio de SSL ou proxy.

Persiste no DuckDB (tabelas `cotacoes` e `proventos`) de forma idempotente.

Uso via linha de comando:
    python -m src.market_data --atualizar
    python -m src.market_data --ticker WEGE3
    python -m src.market_data --resumo
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import duckdb
import pandas as pd
import requests

from src.db import conectar

log = logging.getLogger("market_data")

HEADERS_PADRAO = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


# --------------------------------------------------------------------------- #
# Funções PURAS de cálculo de mercado
# --------------------------------------------------------------------------- #
def calcular_dividend_yield(dividendo_anual: float, preco_atual: float) -> float | None:
    """Calcula o Dividend Yield (DY): Dividendo Anual / Preço Atual."""
    if preco_atual <= 0 or dividendo_anual < 0:
        return None
    return dividendo_anual / preco_atual


def calcular_preco_lucro(preco_atual: float, lpa: float | None) -> float | None:
    """Múltiplo Preço / Lucro (P/L). Retorna None se LPA for nulo ou <= 0."""
    if preco_atual <= 0 or lpa is None or lpa <= 0:
        return None
    return preco_atual / lpa


def calcular_preco_vpa(preco_atual: float, vpa: float | None) -> float | None:
    """Múltiplo Preço / Valor Patrimonial (P/VP). Retorna None se VPA for nulo ou <= 0."""
    if preco_atual <= 0 or vpa is None or vpa <= 0:
        return None
    return preco_atual / vpa


def calcular_dividendo_medio(proventos_historicos: Sequence[float]) -> float:
    """Média aritmética dos dividendos de uma série anual (usado no método Bazin)."""
    valores = [float(v) for v in proventos_historicos if v is not None and v > 0]
    if not valores:
        return 0.0
    return sum(valores) / len(valores)


# --------------------------------------------------------------------------- #
# Provedores de Cotações (I/O)
# --------------------------------------------------------------------------- #
def obter_cotacao_yfinance(ticker: str) -> float | None:
    """Tenta obter a cotação mais recente via yfinance."""
    try:
        import yfinance as yf
        simbolo = f"{ticker.strip().upper()}.SA"
        t = yf.Ticker(simbolo)
        hist = t.history(period="5d")
        if not hist.empty and "Close" in hist.columns:
            preco = float(hist["Close"].iloc[-1])
            if preco > 0:
                log.info("[%s] Cotação obtida via yfinance: R$ %.2f", ticker, preco)
                return preco
    except Exception as e:
        log.debug("[%s] yfinance indisponível ou bloqueado: %s", ticker, e)
    return None


def obter_cotacao_google_finance(ticker: str) -> float | None:
    """Obtém a cotação mais recente via Google Finance."""
    try:
        t_limpo = ticker.strip().upper()
        url = f"https://www.google.com/finance/quote/{t_limpo}:BVMF"
        resp = requests.get(url, headers=HEADERS_PADRAO, timeout=10)
        if resp.status_code == 200:
            m = re.search(r'jsname="Pdsbrc"[^>]*><span>R\$\s*([\d\.,]+)</span>', resp.text)
            if not m:
                m = re.search(r'>R\$\s*([\d\.,]+)</span>', resp.text)
            if m:
                txt = m.group(1).replace(".", "").replace(",", ".") if "," in m.group(1) else m.group(1)
                preco = float(txt)
                if preco > 0:
                    log.info("[%s] Cotação obtida via Google Finance: R$ %.2f", ticker, preco)
                    return preco
    except Exception as e:
        log.debug("[%s] Google Finance falhou: %s", ticker, e)
    return None


def obter_cotacao_brapi(ticker: str, token: str | None = None) -> float | None:
    """Obtém a cotação via API brapi (se token disponível)."""
    token_final = token or os.environ.get("BRAPI_TOKEN")
    if not token_final:
        return None
    try:
        t_limpo = ticker.strip().upper()
        url = f"https://brapi.dev/api/quote/{t_limpo}?token={token_final}"
        resp = requests.get(url, headers=HEADERS_PADRAO, timeout=10)
        if resp.status_code == 200:
            dados = resp.json()
            resultados = dados.get("results", [])
            if resultados:
                preco = float(resultados[0].get("regularMarketPrice", 0.0))
                if preco > 0:
                    log.info("[%s] Cotação obtida via brapi: R$ %.2f", ticker, preco)
                    return preco
    except Exception as e:
        log.debug("[%s] Brapi falhou: %s", ticker, e)
    return None


def obter_cotacao_recente(ticker: str, token_brapi: str | None = None) -> float | None:
    """Cadeia de fallback de cotação: yfinance -> Google Finance -> brapi."""
    preco = obter_cotacao_yfinance(ticker)
    if preco is not None:
        return preco

    preco = obter_cotacao_google_finance(ticker)
    if preco is not None:
        return preco

    return obter_cotacao_brapi(ticker, token=token_brapi)


# --------------------------------------------------------------------------- #
# Persistência no DuckDB
# --------------------------------------------------------------------------- #
def gravar_cotacao(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    preco: float,
    data_ref: date | None = None,
) -> None:
    """Grava ou atualiza a cotação de um ticker no DuckDB de forma idempotente."""
    if preco <= 0:
        return
    t = ticker.strip().upper()
    dt = data_ref or date.today()

    # Apaga cotação existente do dia para o ticker e insere
    con.execute(
        "DELETE FROM cotacoes WHERE ticker = ? AND data = ?",
        [t, dt],
    )
    con.execute(
        "INSERT INTO cotacoes (ticker, data, abertura, maxima, minima, fechamento, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [t, dt, preco, preco, preco, preco, 0.0],
    )


def gravar_provento(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    data_ref: date,
    tipo: str,
    valor: float,
) -> None:
    """Grava provento por ação no DuckDB de forma idempotente."""
    if valor <= 0:
        return
    t = ticker.strip().upper()
    con.execute(
        "DELETE FROM proventos WHERE ticker = ? AND data = ? AND tipo = ?",
        [t, data_ref, tipo],
    )
    con.execute(
        "INSERT INTO proventos (ticker, data, tipo, valor) VALUES (?, ?, ?, ?)",
        [t, data_ref, tipo, valor],
    )


def sincronizar_proventos_cvm(con: duckdb.DuckDBPyConnection) -> int:
    """Calcula e persiste proventos anuais por ação diretamente dos dados auditados da CVM.

    Garante que o banco tenha histórico sólido de proventos mesmo sem internet ativa.
    """
    from src.indicadores import (
        identificar_empresa,
        obter_acoes_circulacao,
        obter_proventos_ano,
    )

    tickers_rows = con.execute("SELECT DISTINCT ticker FROM tickers ORDER BY 1").fetchall()
    total_gravados = 0

    for (t,) in tickers_rows:
        try:
            cnpj, _ = identificar_empresa(con, t)
            anos = [r[0] for r in con.execute("""
                SELECT DISTINCT year(dt_fim_exerc)
                FROM demonstracoes
                WHERE cnpj = ? AND fonte = 'DFP' AND escopo = 'con'
                ORDER BY 1
            """, [cnpj]).fetchall()]

            for ano in anos:
                lucro_row = con.execute("""
                    SELECT vl_conta FROM demonstracoes
                    WHERE cnpj = ? AND fonte = 'DFP' AND escopo = 'con'
                      AND year(dt_fim_exerc) = ? AND cd_conta = '3.11'
                    ORDER BY versao DESC LIMIT 1
                """, [cnpj, ano]).fetchone()
                lucro = float(lucro_row[0]) if lucro_row and lucro_row[0] is not None else None

                acoes = obter_acoes_circulacao(con, cnpj, ano, lucro_liquido=lucro)
                prov_tot = obter_proventos_ano(con, cnpj, ano)

                if acoes > 0 and prov_tot > 0:
                    dpa = prov_tot / acoes
                    dt = date(ano, 12, 31)
                    gravar_provento(con, t, dt, "PROVENTOS_CVM", dpa)
                    total_gravados += 1
        except Exception as e:
            log.debug("Erro ao sincronizar proventos de %s: %s", t, e)

    return total_gravados


def obter_cotacao_mais_recente(con: duckdb.DuckDBPyConnection, ticker: str) -> tuple[float | None, date | None]:
    """Recupera a cotação mais recente gravada no DuckDB."""
    t = ticker.strip().upper()
    res = con.execute("""
        SELECT fechamento, data
        FROM cotacoes
        WHERE ticker = ?
        ORDER BY data DESC
        LIMIT 1
    """, [t]).fetchone()
    if res and res[0] is not None:
        return float(res[0]), res[1]
    return None, None


def obter_proventos_ultimos_anos(con: duckdb.DuckDBPyConnection, ticker: str, anos: int = 5) -> list[float]:
    """Retorna os proventos anuais por ação gravados no banco para o ticker."""
    t = ticker.strip().upper()
    res = con.execute("""
        SELECT sum(valor)
        FROM proventos
        WHERE ticker = ?
        GROUP BY year(data)
        ORDER BY year(data) DESC
        LIMIT ?
    """, [t, anos]).fetchall()
    return [float(r[0]) for r in res if r and r[0] is not None]


def obter_proventos_detalhados_bazin(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    anos: int = 5,
) -> pd.DataFrame:
    """Retorna tabela detalhada ano a ano dos proventos usados para o Preço Teto de Bazin.

    Inclui Proventos Totais (CVM), Quantidade de Ações e DPA (R$/ação).
    """
    from src.indicadores import (
        identificar_empresa,
        obter_acoes_circulacao,
        obter_proventos_ano,
    )

    t = ticker.strip().upper()
    try:
        cnpj, _ = identificar_empresa(con, t)
    except Exception:
        return pd.DataFrame()

    anos_disponiveis = [r[0] for r in con.execute("""
        SELECT DISTINCT year(dt_fim_exerc)
        FROM demonstracoes
        WHERE cnpj = ? AND fonte = 'DFP' AND escopo = 'con'
        ORDER BY 1 DESC
        LIMIT ?
    """, [cnpj, anos]).fetchall()]

    linhas = []
    for ano in sorted(anos_disponiveis):
        lucro_row = con.execute("""
            SELECT vl_conta FROM demonstracoes
            WHERE cnpj = ? AND fonte = 'DFP' AND escopo = 'con'
              AND year(dt_fim_exerc) = ? AND cd_conta = '3.11'
            ORDER BY versao DESC LIMIT 1
        """, [cnpj, ano]).fetchone()
        lucro = float(lucro_row[0]) if lucro_row and lucro_row[0] is not None else None

        acoes = obter_acoes_circulacao(con, cnpj, ano, lucro_liquido=lucro)
        prov_tot = obter_proventos_ano(con, cnpj, ano)
        dpa = (prov_tot / acoes) if (acoes > 0 and prov_tot > 0) else 0.0

        linhas.append({
            "ano": int(ano),
            "proventos_totais": prov_tot,
            "acoes_circulacao": acoes,
            "dpa": dpa,
            "fonte": "CVM (DVA / DFC_MI)",
        })

    return pd.DataFrame(linhas)


def calcular_estatisticas_bazin(
    df_proventos: pd.DataFrame,
    yield_alvo: float = 0.06,
    preco_atual: float | None = None,
) -> dict[str, Any]:
    """Calcula estatísticas e passo a passo da fórmula de Bazin."""
    if df_proventos.empty or "dpa" not in df_proventos:
        return {
            "anos_considerados": 0,
            "soma_dpa": 0.0,
            "dpa_medio": 0.0,
            "yield_alvo": yield_alvo,
            "preco_teto": 0.0,
            "preco_atual": preco_atual,
            "margem_seguranca": None,
        }

    dpas = df_proventos["dpa"].tolist()
    anos_qtd = len(dpas)
    soma = sum(dpas)
    medio = soma / anos_qtd if anos_qtd > 0 else 0.0
    teto = medio / yield_alvo if yield_alvo > 0 else 0.0
    marg = (1.0 - (preco_atual / teto)) if (preco_atual and teto > 0) else None

    return {
        "anos_considerados": anos_qtd,
        "soma_dpa": soma,
        "dpa_medio": medio,
        "yield_alvo": yield_alvo,
        "preco_teto": teto,
        "preco_atual": preco_atual,
        "margem_seguranca": marg,
    }


def atualizar_tudo(con: duckdb.DuckDBPyConnection, token_brapi: str | None = None) -> None:
    """Atualiza cotações online e sincroniza proventos para todos os tickers do banco."""
    # 1. Sincroniza proventos históricos da CVM
    n_prov = sincronizar_proventos_cvm(con)
    log.info("Sincronizados %d registros de proventos auditados da CVM.", n_prov)

    # 2. Busca cotações atuais de todos os tickers
    tickers_rows = con.execute("SELECT DISTINCT ticker FROM tickers ORDER BY 1").fetchall()
    sucessos = 0
    for (t,) in tickers_rows:
        preco = obter_cotacao_recente(t, token_brapi=token_brapi)
        if preco is not None:
            gravar_cotacao(con, t, preco)
            sucessos += 1

    log.info("Cotações atualizadas com sucesso para %d de %d tickers.", sucessos, len(tickers_rows))


def mostrar_resumo_mercado(con: duckdb.DuckDBPyConnection) -> None:
    """Exibe painel com cotação atual, proventos médios, P/L, P/VP e Dividend Yield."""
    from src.indicadores import (
        eh_instituicao_financeira,
        identificar_empresa,
        obter_historico_empresa,
    )

    tickers_rows = con.execute("SELECT DISTINCT ticker FROM tickers ORDER BY 1").fetchall()
    if not tickers_rows:
        print("Nenhum ticker encontrado no banco.")
        return

    linhas = []
    for (t,) in tickers_rows:
        preco, dt_cot = obter_cotacao_mais_recente(con, t)
        provs = obter_proventos_ultimos_anos(con, t, anos=5)
        prov_medio = calcular_dividendo_medio(provs)
        dy = calcular_dividend_yield(prov_medio, preco) if (preco and prov_medio > 0) else None

        # Obter último LPA e VPA
        lpa_recente = None
        vpa_recente = None
        try:
            cnpj, _ = identificar_empresa(con, t)
            df_hist = obter_historico_empresa(con, cnpj)
            if not df_hist.empty:
                lpa_recente = df_hist["lpa"].dropna().iloc[-1] if "lpa" in df_hist else None
                vpa_recente = df_hist["vpa"].dropna().iloc[-1] if "vpa" in df_hist else None
        except Exception:
            pass

        pl = calcular_preco_lucro(preco, lpa_recente) if preco else None
        pvp = calcular_preco_vpa(preco, vpa_recente) if preco else None

        linhas.append({
            "Ticker": t,
            "Cotação": f"R$ {preco:.2f}" if preco else "Sem cotação",
            "Data": str(dt_cot) if dt_cot else "-",
            "DPA Médio": f"R$ {prov_medio:.2f}" if prov_medio > 0 else "-",
            "DY (Médio)": f"{dy*100:.1f}%" if dy else "-",
            "P/L": f"{pl:.1f}x" if pl else "-",
            "P/VP": f"{pvp:.2f}x" if pvp else "-",
        })

    print("\n" + "=" * 70)
    print("  PAINEL DE MERCADO & MÚLTIPLOS (Cotações + Proventos + Múltiplos)")
    print("=" * 70)
    print(pd.DataFrame(linhas).to_string(index=False))
    print()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Módulo de Cotações e Proventos de Mercado")
    p.add_argument("--ticker", default=None, help="Atualiza ou consulta ticker específico")
    p.add_argument("--atualizar", action="store_true", help="Atualiza cotações e proventos para todos os tickers")
    p.add_argument("--resumo", action="store_true", help="Exibe resumo de cotações e múltiplos do banco")
    p.add_argument("--brapi-token", default=None, help="Token opcional para API brapi")
    p.add_argument("--db", default="data/acoes.duckdb", help="Caminho do banco DuckDB")
    args = p.parse_args(argv)

    caminho_db = Path(args.db)
    con = conectar(caminho_db)

    if args.atualizar or (not args.resumo and not args.ticker):
        atualizar_tudo(con, token_brapi=args.brapi_token)
        mostrar_resumo_mercado(con)
        return 0

    if args.ticker:
        t = args.ticker.strip().upper()
        preco = obter_cotacao_recente(t, token_brapi=args.brapi_token)
        if preco:
            gravar_cotacao(con, t, preco)
        sincronizar_proventos_cvm(con)
        mostrar_resumo_mercado(con)
        return 0

    if args.resumo:
        mostrar_resumo_mercado(con)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
