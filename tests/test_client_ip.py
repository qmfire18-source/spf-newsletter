"""L'IP réelle du visiteur derrière un tunnel — voir _client_ip."""
from src.app.main import _client_ip


class FakeRequest:
    def __init__(self, host, headers=None):
        self.client = type("C", (), {"host": host})() if host else None
        self.headers = headers or {}


class TestClientIp:
    def test_direct_visitor(self):
        assert _client_ip(FakeRequest("203.0.113.7")) == "203.0.113.7"

    def test_reads_the_header_behind_a_local_tunnel(self):
        # Sans ça, tout le trafic du tunnel partagerait le compteur d'échecs.
        request = FakeRequest("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"})
        assert _client_ip(request) == "203.0.113.7"

    def test_takes_the_first_of_a_forwarded_chain(self):
        request = FakeRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 10.0.0.1"})
        assert _client_ip(request) == "203.0.113.7"

    def test_cloudflare_header_wins_over_forwarded_for(self):
        request = FakeRequest("127.0.0.1", {
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "198.51.100.1",
        })
        assert _client_ip(request) == "203.0.113.7"

    def test_header_is_ignored_from_a_remote_client(self):
        # Exposée directement, l'app ne doit jamais croire un en-tête falsifiable.
        request = FakeRequest("198.51.100.9", {"x-forwarded-for": "1.2.3.4"})
        assert _client_ip(request) == "198.51.100.9"

    def test_local_visitor_without_header(self):
        assert _client_ip(FakeRequest("127.0.0.1")) == "127.0.0.1"

    def test_empty_header_falls_back(self):
        assert _client_ip(FakeRequest("127.0.0.1", {"x-forwarded-for": " "})) == "127.0.0.1"

    def test_no_client_at_all(self):
        assert _client_ip(FakeRequest(None)) == "inconnu"
