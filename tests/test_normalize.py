import pandas as pd
import pytest

from src.normalize import aplicar_escala, manter_ultima_versao, sem_acento, so_digitos, ytd_para_trimestral


def test_sem_acento_e_cnpj():
    assert sem_acento("ÚLTIMO") == "ULTIMO"
    assert sem_acento(None) == ""
    assert so_digitos("33.000.167/0001-01") == "33000167000101"


def test_escala_mil_e_unidade():
    df = pd.DataFrame({"vl_conta": ["10", "5"], "escala_moeda": ["MIL", "UNIDADE"]})
    assert aplicar_escala(df)["vl_conta"].tolist() == [10_000.0, 5.0]


def test_escala_desconhecida_levanta_erro():
    df = pd.DataFrame({"vl_conta": ["1"], "escala_moeda": ["BILHAO"]})
    with pytest.raises(ValueError):
        aplicar_escala(df)


def test_ultima_versao():
    df = pd.DataFrame({"k": ["a", "a", "b"], "versao": ["1", "2", "1"], "v": [1, 2, 3]})
    out = manter_ultima_versao(df, ["k"])
    assert sorted(out["v"]) == [2, 3]


def _linha(ini, fim, valor):
    return {"cnpj": "1", "escopo": "con", "demonstracao": "DRE", "cd_conta": "3.11",
            "dt_ini_exerc": ini, "dt_fim_exerc": fim, "vl_conta": valor}


def test_ytd_para_trimestral_com_dfp():
    df = pd.DataFrame([
        _linha("2024-01-01", "2024-03-31", 10),   # 1T
        _linha("2024-01-01", "2024-06-30", 25),   # 6M
        _linha("2024-01-01", "2024-09-30", 45),   # 9M
        _linha("2024-01-01", "2024-12-31", 70),   # anual (DFP)
        _linha("2024-04-01", "2024-06-30", 15),   # coluna trimestral avulsa: deve ser ignorada
    ])
    out = ytd_para_trimestral(df).sort_values("trimestre")
    assert out["trimestre"].tolist() == [1, 2, 3, 4]
    assert out["vl_trimestre"].tolist() == [10, 15, 20, 25]


def test_ytd_com_buraco_vira_nan():
    df = pd.DataFrame([
        _linha("2024-01-01", "2024-03-31", 10),
        _linha("2024-01-01", "2024-09-30", 45),   # faltou o 6M
    ])
    out = ytd_para_trimestral(df).sort_values("trimestre")
    assert out["vl_trimestre"].iloc[0] == 10
    assert pd.isna(out["vl_trimestre"].iloc[1])
