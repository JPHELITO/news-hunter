"""Testes do caminho rápido por API da Platts no clipping/reader.py:
parse do artigo, extração do token Okta do state file e decode do exp do JWT.

Rodar: python -m pytest tests/test_clipping_reader.py -v
"""
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.reader import (
    _extract_fm_token, _extract_okta_token, _jwt_exp, _parse_platts_article,
)


def _fake_jwt(exp: int) -> str:
    """JWT dummy (header.payload.assinatura) só com o claim exp — p/ testar _jwt_exp."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.sig"


class TestParsePlattsArticle:
    def test_market_commentary_desescapa_tipo(self):
        u = ("https://core.spglobal.com/#platts/insightsArticle"
             "?articleID=c55672ce-0192-4647-aa34-58ffe96bf253&insightsType=Market%20Commentary")
        aid, typ = _parse_platts_article(u)
        assert aid == "c55672ce-0192-4647-aa34-58ffe96bf253"
        assert typ == "Market Commentary"

    def test_news_simples(self):
        u = ("https://core.spglobal.com/#platts/insightsArticle"
             "?articleID=66545a9d-4ee1-4e52-8eed-e166d15b94f3&insightsType=News")
        aid, typ = _parse_platts_article(u)
        assert aid == "66545a9d-4ee1-4e52-8eed-e166d15b94f3"
        assert typ == "News"

    def test_sem_article_id(self):
        aid, typ = _parse_platts_article("https://core.spglobal.com/#platts/allInsights")
        assert aid is None
        assert typ == "News"      # default tolerante


class TestExtractOktaToken:
    def _state(self, ls_value):
        return {"origins": [{"origin": "https://core.spglobal.com",
                             "localStorage": [{"name": "okta-token-storage", "value": ls_value}]}]}

    def test_acha_access_token_aninhado(self):
        st = self._state(json.dumps({"accessToken": {"accessToken": "eyJabc.def.ghi"},
                                     "idToken": {"idToken": "eyJxxx.yyy.zzz"}}))
        assert _extract_okta_token(st) == "eyJabc.def.ghi"

    def test_sem_okta_storage(self):
        st = {"origins": [{"origin": "x", "localStorage": [{"name": "outra", "value": "{}"}]}]}
        assert _extract_okta_token(st) is None

    def test_valor_json_invalido_nao_quebra(self):
        assert _extract_okta_token(self._state("nao-e-json")) is None

    def test_state_vazio(self):
        assert _extract_okta_token({}) is None


class TestExtractFmToken:
    def _state(self, name, value):
        return {"origins": [{"origin": "https://dashboard.fastmarkets.com",
                             "localStorage": [{"name": name, "value": value}]}]}

    def test_acha_access_token_do_oidc_user(self):
        st = self._state("oidc.user:https://auth.fastmarkets.com/:fastmarkets.das",
                         json.dumps({"access_token": "eyJfm.abc.def", "profile": {"name": "x"}}))
        assert _extract_fm_token(st) == "eyJfm.abc.def"

    def test_ignora_chave_oidc_que_nao_e_user(self):
        st = self._state("oidc.2657324a62664503", json.dumps({"foo": "bar"}))
        assert _extract_fm_token(st) is None

    def test_access_token_nao_jwt_ignorado(self):
        st = self._state("oidc.user:x", json.dumps({"access_token": "opaco-nao-jwt"}))
        assert _extract_fm_token(st) is None

    def test_state_vazio(self):
        assert _extract_fm_token({}) is None


class TestJwtExp:
    def test_decodifica_exp(self):
        exp = int(time.time()) + 3600
        assert _jwt_exp(_fake_jwt(exp)) == float(exp)

    def test_token_invalido_retorna_none(self):
        assert _jwt_exp("nao.e.jwt.valido") is None
        assert _jwt_exp("") is None
