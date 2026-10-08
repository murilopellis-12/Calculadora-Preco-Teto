"""
Camada de I/O para cálculo de indicadores fundamentalistas a partir do DuckDB.

Conecta as contas definidas em `src.contas` com os dados persistidos no DuckDB,
alimenta as funções PURAS de cálculo em `src.metricas` e monta séries históricas.

Uso via linha de comando:
    python -m src.indicadores --ticker WEGE3
    python -m src.indicadores --todos
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.contas import CONTAS, CONTAS_FINANCEIRAS
from src.db import conectar
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


def identificar_empresa(con: duckdb.DuckDBPyConnection, ticker_ou_cnpj: str) -> tuple[str, str]:
    """Resolve ticker (ex.: 'WEGE3') ou CNPJ para (cnpj, nome da empresa)."""
    termo = ticker_ou_cnpj.strip().upper()
    # Tenta como ticker
    res = con.execute("""
        SELECT e.cnpj, e.nome
        FROM tickers t
        JOIN empresas e ON e.cnpj = t.cnpj
        WHERE t.ticker = ?
        LIMIT 1
    """, [termo]).fetchone()
    if res:
        return res[0], res[1]

    # Tenta como CNPJ (só dígitos)
    cnpj_limpo = "".join(c for c in termo if c.isdigit())
    res = con.execute("""
        SELECT cnpj, nome FROM empresas WHERE cnpj = ? LIMIT 1
    """, [cnpj_limpo]).fetchone()
    if res:
        return res[0], res[1]

    raise ValueError(f"Empresa não encontrada para o termo: '{ticker_ou_cnpj}'")


def eh_instituicao_financeira(con: duckdb.DuckDBPyConnection, cnpj: str) -> bool:
    """Verifica se a empresa adota plano de contas de instituição financeira.

    Identificado pela presença de Patrimônio Líquido na conta 2.07 ou
    descrição de intermediação financeira na conta 3.01 da DRE.
    """
    res = con.execute("""
        SELECT count(*)
        FROM demonstracoes
        WHERE cnpj = ?
          AND cd_conta = '2.07'
          AND lower(ds_conta) LIKE '%patrim%nio l%quido%'
    """, [cnpj]).fetchone()
    return bool(res and res[0] > 0)


def obter_contas_ano(
    con: duckdb.DuckDBPyConnection,
    cnpj: str,
    ano: int,
    mapa_contas: dict[str, str],
    fonte: str = "DFP",
    escopo: str = "con",
) -> dict[str, float | None]:
    """Obtém valores das contas mapeadas para um determinado ano."""
    dados: dict[str, float | None] = {}
    for nome_metrica, cd in mapa_contas.items():
        res = con.execute("""
            SELECT vl_conta
            FROM demonstracoes
            WHERE cnpj = ?
              AND fonte = ?
              AND escopo = ?
              AND year(dt_fim_exerc) = ?
              AND cd_conta = ?
            ORDER BY versao DESC
            LIMIT 1
        """, [cnpj, fonte, escopo, ano, cd]).fetchone()
        dados[nome_metrica] = float(res[0]) if res and res[0] is not None else None
    return dados


def obter_depreciacao_ano(
    con: duckdb.DuckDBPyConnection,
    cnpj: str,
    ano: int,
    fonte: str = "DFP",
    escopo: str = "con",
) -> float:
    """Obtém depreciação e amortização do exercício (da DVA ou DFC_MI)."""
    res = con.execute("""
        SELECT vl_conta
        FROM demonstracoes
        WHERE cnpj = ?
          AND fonte = ?
          AND escopo = ?
          AND year(dt_fim_exerc) = ?
          AND (
            cd_conta IN ('7.04.01', '7.05.01')
            OR (demonstracao = 'DFC_MI' AND lower(ds_conta) LIKE '%deprecia%')
          )
        ORDER BY abs(vl_conta) DESC
        LIMIT 1
    """, [cnpj, fonte, escopo, ano]).fetchone()
    return abs(float(res[0])) if res and res[0] is not None else 0.0


def obter_proventos_ano(
    con: duckdb.DuckDBPyConnection,
    cnpj: str,
    ano: int,
    fonte: str = "DFP",
    escopo: str = "con",
) -> float:
    """Obtém total de dividendos e JCP distribuídos no exercício (da DVA ou DFC_MI)."""
    # Soma dividendos + JCP da DVA
    res = con.execute("""
        SELECT sum(abs(vl_conta))
        FROM demonstracoes
        WHERE cnpj = ?
          AND fonte = ?
          AND escopo = ?
          AND year(dt_fim_exerc) = ?
          AND demonstracao = 'DVA'
          AND cd_conta IN ('7.08.04.01', '7.08.04.02', '7.09.04.01', '7.09.04.02')
    """, [cnpj, fonte, escopo, ano]).fetchone()
    if res and res[0] is not None and res[0] > 0:
        return float(res[0])

    # Plano B: pagamentos de dividendos da DFC_MI
    res_dfc = con.execute("""
        SELECT sum(abs(vl_conta))
        FROM demonstracoes
        WHERE cnpj = ?
          AND fonte = ?
          AND escopo = ?
          AND year(dt_fim_exerc) = ?
          AND demonstracao = 'DFC_MI'
          AND lower(ds_conta) LIKE '%dividendo%'
          AND lower(ds_conta) LIKE '%pago%'
    """, [cnpj, fonte, escopo, ano]).fetchone()
    return float(res_dfc[0]) if res_dfc and res_dfc[0] is not None else 0.0


def obter_acoes_circulacao(
    con: duckdb.DuckDBPyConnection,
    cnpj: str,
    ano: int,
    lucro_liquido: float | None = None,
    fonte: str = "DFP",
) -> float:
    """Obtém a quantidade líquida de ações em circulação, com correção automática de escala."""
    res = con.execute("""
        SELECT qt_total, qt_tesouro_total
        FROM composicao_capital
        WHERE cnpj = ?
          AND fonte = ?
          AND year(dt_refer) = ?
        ORDER BY versao DESC
        LIMIT 1
    """, [cnpj, fonte, ano]).fetchone()
    if not res or res[0] is None:
        return 0.0
    qt_total = float(res[0])
    qt_tesouro = float(res[1]) if res[1] is not None else 0.0
    return ajustar_escala_acoes(qt_total, qt_tesouro, lucro_liquido=lucro_liquido)


def calcular_indicadores_ano(
    con: duckdb.DuckDBPyConnection,
    cnpj: str,
    ano: int,
    eh_financeira: bool | None = None,
) -> MetricasAno:
    """Calcula todas as métricas fundamentalistas para uma empresa em um ano."""
    if eh_financeira is None:
        eh_financeira = eh_instituicao_financeira(con, cnpj)

    mapa_contas = CONTAS_FINANCEIRAS if eh_financeira else CONTAS
    dados = obter_contas_ano(con, cnpj, ano, mapa_contas)

    lucro = dados.get("lucro_liquido")
    pl = dados.get("patrimonio_liquido")
    rec = dados.get("receita")
    bruto = dados.get("resultado_bruto")
    ebit_val = dados.get("ebit")
    caixa = dados.get("caixa_equivalentes") or 0.0
    div_cp = dados.get("divida_cp") or 0.0
    div_lp = dados.get("divida_lp") or 0.0

    deprec = obter_depreciacao_ano(con, cnpj, ano)
    proventos = obter_proventos_ano(con, cnpj, ano)
    acoes = obter_acoes_circulacao(con, cnpj, ano, lucro_liquido=lucro)

    # Métricas universais
    _lpa = lpa(lucro, acoes) if lucro is not None and acoes > 0 else None
    _vpa = vpa(pl, acoes) if pl is not None and acoes > 0 else None
    _roe = roe(lucro, pl) if lucro is not None and pl is not None else None
    _payout = payout(proventos, lucro) if proventos > 0 and lucro is not None else None

    # Métricas exclusivas ou adaptadas para não-financeiras
    if eh_financeira:
        # Para bancos e seguradoras: ROIC e Dívida Líquida/EBITDA não se aplicam
        _marg_liq = margem_liquida(lucro, rec) if lucro is not None and rec is not None else None
        return MetricasAno(
            ano=ano,
            lpa=_lpa,
            vpa=_vpa,
            roe=_roe,
            margem_liquida=_marg_liq,
            payout=_payout,
            observacoes=["Instituição financeira: ROIC e Dívida Líq./EBITDA não aplicáveis."],
        )

    # Empresas não-financeiras
    _marg_bruta = margem_bruta(bruto, rec) if bruto is not None and rec is not None else None
    _marg_ebit = margem_ebit(ebit_val, rec) if ebit_val is not None and rec is not None else None
    _marg_liq = margem_liquida(lucro, rec) if lucro is not None and rec is not None else None

    div_bruta = div_cp + div_lp
    div_liq = divida_liquida(div_bruta, caixa)
    ebitda_val = ebitda(ebit_val, deprec) if ebit_val is not None else None
    _dl_ebitda = divida_liquida_ebitda(div_liq, ebitda_val) if ebitda_val is not None else None

    cap_inv = capital_investido(pl or 0.0, div_liq)
    _roic = roic(ebit_val, cap_inv) if ebit_val is not None and cap_inv > 0 else None

    return MetricasAno(
        ano=ano,
        lpa=_lpa,
        vpa=_vpa,
        roe=_roe,
        roic=_roic,
        margem_bruta=_marg_bruta,
        margem_ebit=_marg_ebit,
        margem_liquida=_marg_liq,
        divida_liquida=div_liq,
        ebitda=ebitda_val,
        divida_liquida_ebitda=_dl_ebitda,
        payout=_payout,
    )


def obter_historico_empresa(
    con: duckdb.DuckDBPyConnection,
    ticker_ou_cnpj: str,
) -> pd.DataFrame:
    """Calcula e formata a série histórica de indicadores para todos os anos disponíveis."""
    cnpj, nome = identificar_empresa(con, ticker_ou_cnpj)
    eh_banco = eh_instituicao_financeira(con, cnpj)

    anos = [r[0] for r in con.execute("""
        SELECT DISTINCT year(dt_fim_exerc)
        FROM demonstracoes
        WHERE cnpj = ? AND fonte = 'DFP' AND escopo = 'con'
        ORDER BY 1
    """, [cnpj]).fetchall()]

    linhas = []
    for ano in anos:
        m = calcular_indicadores_ano(con, cnpj, ano, eh_financeira=eh_banco)
        linhas.append(m.para_dicionario())

    df = pd.DataFrame(linhas)
    return df


def calcular_cagrs(df_historico: pd.DataFrame) -> dict[str, float | None]:
    """Calcula taxas de crescimento composto (CAGR) a partir do histórico anual."""
    res: dict[str, float | None] = {}
    if len(df_historico) < 2:
        return res

    periodos = len(df_historico) - 1
    for col in ("lpa", "vpa"):
        if col in df_historico.columns:
            v_ini = df_historico[col].iloc[0]
            v_fim = df_historico[col].iloc[-1]
            if v_ini and v_fim and v_ini > 0 and v_fim > 0:
                res[f"cagr_{col}_{periodos}a"] = cagr(v_ini, v_fim, periodos)
    return res


def formatar_tabela_exibicao(df: pd.DataFrame, eh_financeira: bool = False) -> pd.DataFrame:
    """Formata os números do DataFrame para apresentação amigável."""
    saida = pd.DataFrame()
    saida["Ano"] = df["ano"]
    saida["LPA"] = df["lpa"].apply(lambda v: f"R$ {v:.2f}" if pd.notna(v) else "-")
    saida["VPA"] = df["vpa"].apply(lambda v: f"R$ {v:.2f}" if pd.notna(v) else "-")
    saida["ROE"] = df["roe"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")

    if not eh_financeira:
        saida["ROIC"] = df["roic"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")
        saida["Marg.Bruta"] = df["margem_bruta"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")
        saida["Marg.EBIT"] = df["margem_ebit"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")

    saida["Marg.Líq"] = df["margem_liquida"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")

    if not eh_financeira:
        saida["Dív.Líq/EBITDA"] = df["divida_liquida_ebitda"].apply(lambda v: f"{v:.2f}x" if pd.notna(v) else "-")

    saida["Payout"] = df["payout"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")
    return saida


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exibição de Indicadores Fundamentalistas")
    p.add_argument("--ticker", default=None, help="Ticker da empresa (ex.: WEGE3, PETR4)")
    p.add_argument("--todos", action="store_true", help="Mostra resumo de todos os tickers em tickers.txt")
    p.add_argument("--db", default="data/acoes.duckdb", help="Caminho do banco DuckDB")
    args = p.parse_args(argv)

    caminho_db = Path(args.db)
    if not caminho_db.exists():
        print(f"Banco não encontrado em {caminho_db}. Rode a ingestão primeiro.")
        return 1

    con = conectar(caminho_db)

    if args.ticker:
        try:
            cnpj, nome = identificar_empresa(con, args.ticker)
            eh_banco = eh_instituicao_financeira(con, cnpj)
            df = obter_historico_empresa(con, cnpj)
            cagrs = calcular_cagrs(df)

            print(f"\n{'='*70}")
            print(f"  {nome} ({args.ticker.upper()}) — {'Instituição Financeira' if eh_banco else 'Companhia Não-Financeira'}")
            print(f"{'='*70}")
            print(formatar_tabela_exibicao(df, eh_financeira=eh_banco).to_string(index=False))

            if cagrs:
                print("\nTaxas de Crescimento (CAGR):")
                for k, v in cagrs.items():
                    print(f"  * {k.upper()}: {v*100:.2f}% a.a.")
            print()
            return 0
        except Exception as e:
            print(f"Erro ao processar {args.ticker}: {e}")
            return 1

    # Se pediu --todos ou não passou ticker
    tickers = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM tickers ORDER BY 1").fetchall()]
    if not tickers:
        print("Nenhum ticker encontrado no banco.")
        return 0

    print(f"\nIndicadores de {len(tickers)} ativos disponíveis no banco:")
    for t in tickers:
        try:
            cnpj, nome = identificar_empresa(con, t)
            eh_banco = eh_instituicao_financeira(con, cnpj)
            df = obter_historico_empresa(con, cnpj)
            print(f"\n-------------------------------------------------------------")
            print(f"  {t} — {nome} ({'Financeira' if eh_banco else 'Não-Financeira'})")
            print(f"-------------------------------------------------------------")
            print(formatar_tabela_exibicao(df, eh_financeira=eh_banco).to_string(index=False))
        except Exception as e:
            print(f"Erro ao processar {t}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
